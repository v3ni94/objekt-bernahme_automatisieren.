"""Eigentuemerakten als Datensaetze (owner_files) fuer Ablageziele: eine Akte je Einheit und Eigentuemergruppe
(CR 4, F 6.3), Sonderakten Unbekannte_WE_<Nachname> und Unzugeordnet nur als Ziel eines Falls oder einer
Review-Entscheidung."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from apps.drive.naming import OwnerFileNamingConfig, OwnerNameInput, OwnerNamePart, build_owner_folder_name
from apps.parties.models import Owner, OwnerFile, OwnerFileAssignment, OwnerUnitAssignment


def _name_part(owner: Owner) -> OwnerNamePart:
    return OwnerNamePart(kind=owner.type, last_name=owner.last_name, company_name=owner.company_name)


def _existing_names(obj) -> frozenset[str]:
    return frozenset(OwnerFile.active.filter(object=obj).values_list("folder_name", flat=True))


def owner_file_for_assignments(unit, assignments: list[OwnerUnitAssignment]) -> OwnerFile:
    """Akte der Eigentuemergruppe einer Einheit: vorhandene Akte ueber owner_file_assignments, sonst neue Akte mit
    Ordnernamen aus der Benennungsfunktion."""
    linked = OwnerFile.active.filter(file_assignments__assignment__in=assignments).distinct().first()
    if linked is not None:
        for a in assignments:
            OwnerFileAssignment.objects.get_or_create(assignment=a, defaults={"owner_file": linked})
        return linked
    from apps.parties.unit_files import adopt_owner_placeholder

    adopted = adopt_owner_placeholder(
        unit, assignments
    )  # Akten-Vorlage: Platzhalter WE01 wird zur benannten Akte
    if adopted is not None:
        return adopted
    cfg = OwnerFileNamingConfig.from_settings()
    owners = [a.owner for a in assignments]
    inp = OwnerNameInput(
        file_kind="unit_owner",
        unit_label=unit.unit_label,
        unit_type=unit.unit_type,
        owner_names=tuple(_name_part(o) for o in owners),
        valid_from=min((a.valid_from for a in assignments if a.valid_from), default=None),
        existing_names_in_object=_existing_names(unit.object),
    )
    name = build_owner_folder_name(inp, cfg)
    akte = OwnerFile.objects.create(
        object=unit.object,
        unit=unit,
        owner=owners[0] if len(owners) == 1 else None,
        file_kind="unit_owner",
        folder_name=name,
        name_basis={"owners": [o.pk for o in owners], "unit": unit.pk, "mode": cfg.unit_prefix_mode},
    )
    for a in assignments:
        OwnerFileAssignment.objects.get_or_create(assignment=a, defaults={"owner_file": akte})
    # Der Drive-Ordner entsteht mit der ersten Ablage (file_to_drive, F 6.3) oder, mit
    # owner_file.create_folders_eagerly, ueber ensure_unit_folders nach Zuordnung und Ordnerabgleich.
    return akte


def owner_file_unknown_unit(obj, owner: Owner) -> OwnerFile:
    existing = OwnerFile.active.filter(object=obj, owner=owner, file_kind="unknown_unit").first()
    if existing:
        return existing
    cfg = OwnerFileNamingConfig.from_settings()
    name = build_owner_folder_name(
        OwnerNameInput(
            file_kind="unknown_unit",
            owner_names=(_name_part(owner),),
            existing_names_in_object=_existing_names(obj),
        ),
        cfg,
    )
    return _create_or_refetch(
        lambda: OwnerFile.active.filter(object=obj, owner=owner, file_kind="unknown_unit").first(),
        object=obj,
        owner=owner,
        file_kind="unknown_unit",
        folder_name=name,
        name_basis={"owner": owner.pk},
    )


def owner_file_unassigned(obj) -> OwnerFile:
    existing = OwnerFile.active.filter(object=obj, file_kind="unassigned").first()
    if existing:
        return existing
    cfg = OwnerFileNamingConfig.from_settings()
    name = build_owner_folder_name(OwnerNameInput(file_kind="unassigned"), cfg)
    return _create_or_refetch(
        lambda: OwnerFile.active.filter(object=obj, file_kind="unassigned").first(),
        object=obj,
        file_kind="unassigned",
        folder_name=name,
        name_basis={},
    )


def _create_or_refetch(lookup, **fields) -> OwnerFile:
    """Legt die Sonderakte an; hat ein paralleler Entscheidungsjob sie inzwischen angelegt (uq_owner_files_name_active,
    Praxisfall Objekt 82: „Duplicate entry ... Unzugeordnet“), wird die vorhandene Akte verwendet statt den Job
    scheitern zu lassen."""
    try:
        with transaction.atomic():
            return OwnerFile.objects.create(**fields)
    except IntegrityError:
        existing = lookup()
        if existing is None:
            raise
        return existing
