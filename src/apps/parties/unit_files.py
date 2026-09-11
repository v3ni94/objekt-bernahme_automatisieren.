"""Akten-Vorlage je Einheit (Entscheidung 11.09.2026).

Eigentuemer- und Mieterakte einer Einheit entstehen als Platzhalter mit dem Einheitenkuerzel (etwa WE01), sobald die
Einheit bekannt ist, und werden umbenannt, sobald Eigentuemer oder Mieter zugeordnet sind. Die Ordner in Drive folgen
ueber apps.drive.folders (ensure_owner_folder, ensure_tenant_folder, sync_file_names). Namensbildung ausschliesslich
ueber apps.drive.naming, damit Platzhalter und endgueltige Namen demselben Schema folgen.
"""

from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record
from apps.drive.naming import (
    OwnerFileNamingConfig,
    OwnerNameInput,
    OwnerNamePart,
    build_owner_folder_name,
    unit_token,
)
from apps.objects.models import Unit
from apps.objects.units import parse_unit_label
from apps.parties.models import (
    OwnerFile,
    OwnerFileAssignment,
    OwnerUnitAssignment,
    TenantFile,
    TenantFileAssignment,
    TenantUnitAssignment,
)


def placeholder_name(unit: Unit, cfg: OwnerFileNamingConfig | None = None) -> str:
    """Platzhaltername einer Akte: nur das Einheitenkuerzel nach Konfiguration (WE01, GE03, ...)."""
    return unit_token(unit.unit_label, unit.unit_type, cfg or OwnerFileNamingConfig.from_settings())


def _unique_name(base: str, model, obj) -> str:
    taken = set(model.active.filter(object=obj).values_list("folder_name", flat=True))
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def _party_part(party) -> OwnerNamePart:
    return OwnerNamePart(kind=party.type, last_name=party.last_name, company_name=party.company_name)


def ensure_placeholder_units(obj, count: int, *, user=None) -> list[Unit]:
    """Einheiten WE 1 bis WE n anlegen, wenn das Objekt noch keine Einheiten hat (Sollzahl aus der Objektanlage).
    Bezeichnung und Typ ueber die Einheitennormalisierung; Datenstatus unvollstaendig, bis Stammdaten folgen."""
    if not count or count <= 0 or Unit.active.filter(object=obj).exists():
        return []
    if not cache.add(f"unit-files:{obj.pk}", "1", timeout=30):
        return []  # ein anderer Aufruf (Doppelklick, paralleler Task) legt gerade an
    created: list[Unit] = []
    with transaction.atomic():
        for i in range(1, count + 1):
            label = f"WE {i}"
            parsed = parse_unit_label(label)
            unit = Unit.objects.create(
                object=obj,
                unit_label=label,
                unit_label_normalized=parsed.label_normalized,
                unit_number=str(i),
                unit_type=parsed.unit_type or "apartment",
                data_status="incomplete",
            )
            record(
                "unit.create",
                entity_type="unit",
                entity_id=unit.pk,
                object_id=obj.pk,
                actor=user,
                actor_type=None if user else "system",
                after={"unit_label": label, "placeholder": True, "source": "expected_unit_count"},
            )
            created.append(unit)
    cache.delete(f"unit-files:{obj.pk}")
    return created


def current_owner_assignments(unit: Unit) -> list[OwnerUnitAssignment]:
    today = timezone.localdate()
    qs = OwnerUnitAssignment.active.filter(unit=unit).filter(
        valid_to__isnull=True
    ) | OwnerUnitAssignment.active.filter(unit=unit, valid_to__gte=today)
    return list(qs.select_related("owner").distinct().order_by("valid_from"))


def current_tenant_assignments(unit: Unit) -> list[TenantUnitAssignment]:
    today = timezone.localdate()
    qs = TenantUnitAssignment.active.filter(unit=unit).filter(
        valid_to__isnull=True
    ) | TenantUnitAssignment.active.filter(unit=unit, valid_to__gte=today)
    return list(qs.select_related("tenant").distinct().order_by("valid_from"))


def _owner_groups(assignments: list[OwnerUnitAssignment]) -> list[list[OwnerUnitAssignment]]:
    """Eigentuemergruppen wie in der Klassifikation (gleicher Zeitraum = eine Gruppe, CR 4); die heute geltende
    Gruppe zuerst, damit sie den Platzhalter uebernimmt, kuenftige Gruppen erhalten eigene Akten."""
    today = timezone.localdate()
    groups: dict[tuple, list[OwnerUnitAssignment]] = {}
    for a in assignments:
        groups.setdefault((a.valid_from, a.valid_to), []).append(a)

    def rank(key):
        start, end = key
        current = (start is None or start <= today) and (end is None or end >= today)
        return (0 if current else 1, start or date.min)

    return [groups[k] for k in sorted(groups, key=rank)]


def _tenant_groups(assignments: list[TenantUnitAssignment]) -> list[list[TenantUnitAssignment]]:
    """Mitmieter desselben Mietverhaeltnisses teilen die Akte (H 6.9); ohne Mietverhaeltnis je Zuordnung eine."""
    groups: dict[object, list[TenantUnitAssignment]] = {}
    for a in assignments:
        groups.setdefault(a.lease_id or f"a{a.pk}", []).append(a)
    return list(groups.values())


def _create_placeholder(model, obj, unit: Unit, file_kind: str, cfg: OwnerFileNamingConfig):
    try:
        with transaction.atomic():
            return model.objects.create(
                object=obj,
                unit=unit,
                file_kind=file_kind,
                folder_name=_unique_name(placeholder_name(unit, cfg), model, obj),
                name_basis={"unit": unit.pk, "placeholder": True, "mode": cfg.unit_prefix_mode},
            )
    except IntegrityError:  # gleichzeitiger Aufruf hat die Akte angelegt
        return model.active.filter(unit=unit, file_kind=file_kind).order_by("id").first()


def ensure_unit_files(obj) -> dict[str, int]:
    """Je aktive Einheit eine Eigentuemer- und eine Mieterakte. Sind Eigentuemer oder Mieter bereits zugeordnet,
    entsteht je Gruppe eine Akte mit Namen (aktuelle Gruppe zuerst); sonst ein Platzhalter mit dem Einheitenkuerzel."""
    from apps.classification.ownerfiles import owner_file_for_assignments

    cfg = OwnerFileNamingConfig.from_settings()
    stats = {"owner_files": 0, "tenant_files": 0}
    for unit in Unit.active.filter(object=obj).order_by("unit_label_normalized"):
        if not OwnerFile.active.filter(unit=unit, file_kind="unit_owner").exists():
            groups = _owner_groups(current_owner_assignments(unit))
            if groups:
                for group in groups:
                    owner_file_for_assignments(unit, group)
            else:
                _create_placeholder(OwnerFile, obj, unit, "unit_owner", cfg)
            stats["owner_files"] += 1
        if not TenantFile.active.filter(unit=unit, file_kind="unit_tenant").exists():
            groups = _tenant_groups(current_tenant_assignments(unit))
            if groups:
                for group in groups:
                    tenant_file_for_assignments(unit, group)
            else:
                _create_placeholder(TenantFile, obj, unit, "unit_tenant", cfg)
            stats["tenant_files"] += 1
    return stats


def rename_unit_files(unit: Unit) -> int:
    """Einheit umbenannt (WE 1 wird WE 1a): Platzhalter und von der Anwendung benannte Akten der Einheit folgen dem
    neuen Kuerzel; von Hand in Drive vergebene Ordnernamen bleiben (sync_file_names). Rueckgabe: geaenderte Akten."""
    cfg = OwnerFileNamingConfig.from_settings()
    changed = 0
    for model, kind in ((OwnerFile, "unit_owner"), (TenantFile, "unit_tenant")):
        for akte in model.active.filter(unit=unit, file_kind=kind):
            basis = akte.name_basis or {}
            links = list(akte.file_assignments.select_related("assignment"))
            if basis.get("placeholder") and not links:
                name = (
                    _unique_name(placeholder_name(unit, cfg), model, unit.object)
                    if akte.folder_name != placeholder_name(unit, cfg)
                    else akte.folder_name
                )
                if name.rsplit("_", 1)[0] == akte.folder_name:  # nur Zaehlsuffix, Name unveraendert
                    name = akte.folder_name
            elif basis.get("adopted_placeholder") or basis.get("managed_name"):
                assignments = [fl.assignment for fl in links]
                parties = [getattr(a, "owner", None) or getattr(a, "tenant", None) for a in assignments]
                name = _group_name(model, unit, parties, assignments, akte.folder_name, akte.pk)
            else:
                continue
            if name != akte.folder_name:
                akte.folder_name = name
                akte.save(update_fields=["folder_name", "updated_at"])
                changed += 1
    return changed


def _existing_names(model, obj, exclude_pk: int | None = None) -> frozenset[str]:
    qs = model.active.filter(object=obj)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return frozenset(qs.values_list("folder_name", flat=True))


def adopt_owner_placeholder(unit: Unit, assignments: list[OwnerUnitAssignment]) -> OwnerFile | None:
    """Platzhalter-Akte der Einheit (ohne Zuordnungen) uebernehmen: Name aus den Eigentuemern bilden, Zuordnungen
    verknuepfen. Der Drive-Ordner wird beim naechsten Abgleich der Akten umbenannt (sync_file_names)."""
    placeholder = (
        OwnerFile.active.filter(unit=unit, file_kind="unit_owner", file_assignments__isnull=True)
        .order_by("id")
        .first()
    )
    if placeholder is None or not assignments:
        return None
    cfg = OwnerFileNamingConfig.from_settings()
    owners = [a.owner for a in assignments]
    name = build_owner_folder_name(
        OwnerNameInput(
            file_kind="unit_owner",
            unit_label=unit.unit_label,
            unit_type=unit.unit_type,
            owner_names=tuple(_party_part(o) for o in owners),
            valid_from=min((a.valid_from for a in assignments if a.valid_from), default=None),
            existing_names_in_object=_existing_names(OwnerFile, unit.object, placeholder.pk),
            current_name=placeholder.folder_name,
        ),
        cfg,
    )
    with transaction.atomic():
        placeholder.folder_name = name
        placeholder.owner = owners[0] if len(owners) == 1 else None
        placeholder.name_basis = {
            "owners": [o.pk for o in owners],
            "unit": unit.pk,
            "mode": cfg.unit_prefix_mode,
            "adopted_placeholder": True,
        }
        placeholder.save(update_fields=["folder_name", "owner", "name_basis", "updated_at"])
        for a in assignments:
            OwnerFileAssignment.objects.get_or_create(assignment=a, defaults={"owner_file": placeholder})
    return placeholder


def _group_name(model, unit: Unit, parties, assignments, current: str | None, exclude_pk: int | None) -> str:
    cfg = OwnerFileNamingConfig.from_settings()
    return build_owner_folder_name(
        OwnerNameInput(
            file_kind="unit_owner",
            unit_label=unit.unit_label,
            unit_type=unit.unit_type,
            owner_names=tuple(_party_part(p) for p in parties),
            valid_from=min((a.valid_from for a in assignments if a.valid_from), default=None),
            existing_names_in_object=_existing_names(model, unit.object, exclude_pk),
            current_name=current,
        ),
        cfg,
    )


def refresh_owner_file_name(akte: OwnerFile, unit: Unit, assignments: list[OwnerUnitAssignment]) -> bool:
    """Akte aus der Vorlage folgt ihrer Eigentuemergruppe: kommt ein weiterer Eigentuemer derselben Gruppe hinzu
    (Import einer Zeile mit zwei Namen, zweite Zuordnung von Hand), wird der Name neu gebildet
    (WE02_Mustermann wird zu WE02_Beispiel-Mustermann). Akten ohne Vorlagenherkunft behalten ihren Namen (F 6.3)."""
    basis = akte.name_basis or {}
    if not (basis.get("adopted_placeholder") or basis.get("managed_name")):
        return False
    owners = [a.owner for a in assignments]
    if set(basis.get("owners") or []) == {o.pk for o in owners}:
        return False
    name = _group_name(OwnerFile, unit, owners, assignments, akte.folder_name, akte.pk)
    akte.folder_name = name
    akte.owner = owners[0] if len(owners) == 1 else None
    akte.name_basis = {**basis, "owners": [o.pk for o in owners]}
    akte.save(update_fields=["folder_name", "owner", "name_basis", "updated_at"])
    return True


def _refresh_tenant_file_name(akte: TenantFile, unit: Unit, assignments: list[TenantUnitAssignment]) -> bool:
    basis = akte.name_basis or {}
    tenants = [a.tenant for a in assignments]
    managed = basis.get("adopted_placeholder") or basis.get("managed_name")
    if not managed or set(basis.get("tenants") or []) == {t.pk for t in tenants}:
        return False
    akte.folder_name = _group_name(TenantFile, unit, tenants, assignments, akte.folder_name, akte.pk)
    akte.name_basis = {**basis, "tenants": [t.pk for t in tenants]}
    akte.save(update_fields=["folder_name", "name_basis", "updated_at"])
    return True


def tenant_file_for_assignments(unit: Unit, assignments: list[TenantUnitAssignment]) -> TenantFile:
    """Mieterakte der Mietergruppe einer Einheit: vorhandene Akte ueber tenant_file_assignments, sonst Platzhalter
    uebernehmen, sonst neue Akte. Namensschema wie bei Eigentuemerakten (Einheitenkuerzel plus Nachnamen)."""
    linked = TenantFile.active.filter(file_assignments__assignment__in=assignments).distinct().first()
    if linked is not None:
        for a in assignments:
            TenantFileAssignment.objects.get_or_create(assignment=a, defaults={"tenant_file": linked})
        alle = [
            fl.assignment
            for fl in linked.file_assignments.select_related("assignment__tenant")
            if fl.assignment.deleted_at is None
        ]
        _refresh_tenant_file_name(linked, unit, alle or list(assignments))
        return linked
    cfg = OwnerFileNamingConfig.from_settings()
    placeholder = (
        TenantFile.active.filter(unit=unit, file_kind="unit_tenant", file_assignments__isnull=True)
        .order_by("id")
        .first()
    )
    tenants = [a.tenant for a in assignments]
    name = build_owner_folder_name(
        OwnerNameInput(
            file_kind="unit_owner",
            unit_label=unit.unit_label,
            unit_type=unit.unit_type,
            owner_names=tuple(_party_part(t) for t in tenants),
            valid_from=min((a.valid_from for a in assignments if a.valid_from), default=None),
            existing_names_in_object=_existing_names(
                TenantFile, unit.object, placeholder.pk if placeholder else None
            ),
            current_name=placeholder.folder_name if placeholder else None,
        ),
        cfg,
    )
    basis = {
        "tenants": [t.pk for t in tenants],
        "unit": unit.pk,
        "mode": cfg.unit_prefix_mode,
        "managed_name": True,
    }
    with transaction.atomic():
        if placeholder is not None:
            placeholder.folder_name = name
            placeholder.name_basis = {**basis, "adopted_placeholder": True}
            placeholder.save(update_fields=["folder_name", "name_basis", "updated_at"])
            akte = placeholder
        else:
            try:
                with transaction.atomic():
                    akte = TenantFile.objects.create(
                        object=unit.object,
                        unit=unit,
                        file_kind="unit_tenant",
                        folder_name=name,
                        name_basis=basis,
                    )
            except IntegrityError:  # gleichzeitige Anlage durch ein zweites Dokument derselben Einheit
                akte = TenantFile.active.get(object=unit.object, folder_name=name)
        for a in assignments:
            TenantFileAssignment.objects.get_or_create(assignment=a, defaults={"tenant_file": akte})
    return akte


def tenant_file_for_unit(unit: Unit) -> TenantFile:
    """Mieterakte einer Einheit fuer die Ablage: aktuelle Mieter, sonst vorhandene Akte, sonst Platzhalter."""
    tenants = current_tenant_assignments(unit)
    if tenants:
        return tenant_file_for_assignments(unit, tenants)
    existing = TenantFile.active.filter(unit=unit, file_kind="unit_tenant").order_by("id").first()
    if existing is not None:
        return existing
    return _create_placeholder(
        TenantFile, unit.object, unit, "unit_tenant", OwnerFileNamingConfig.from_settings()
    )


def tenant_file_for_document(
    obj, tenant_ids: list[int], unit_ids: list[int], *, create: bool = True
) -> TenantFile | None:
    """Ablageziel eines Mieterdokuments: ueber den erkannten Mieter dessen Einheit, sonst die eindeutig erkannte
    Einheit; ohne beides bleibt die Ablage flach im Hauptordner 04. Ohne create wird nur eine vorhandene Akte
    genutzt (Schalter owner_file.create_folders_eagerly aus: keine Akten durch die Pipeline)."""
    unit = None
    assignment = None
    if tenant_ids:
        assignment = (
            TenantUnitAssignment.active.filter(tenant_id__in=tenant_ids, unit__object=obj)
            .select_related("unit", "tenant")
            .order_by("-valid_from")
            .first()
        )
        if assignment is not None:
            unit = assignment.unit
    if unit is None and len(unit_ids) == 1:
        unit = Unit.active.filter(pk=unit_ids[0], object=obj).first()
    if unit is None:
        return None
    if not create:
        return TenantFile.active.filter(unit=unit, file_kind="unit_tenant").order_by("id").first()
    if assignment is not None:
        return tenant_file_for_assignments(unit, [assignment])
    return tenant_file_for_unit(unit)
