"""Stufe 1: deterministisches Regelwerk (E 2.2, docs/architektur.md 6.2). Regeln liegen als JSON unter db/seeds/rules/
und in classification_rules (definition). Ziele sind Codes (B-12). Alle passenden Regeln werden gesammelt; der beste
harte Treffer gewinnt, sonst der beste weiche; widerspruechliche harte Treffer ergeben einen Konflikt (Konfidenz 0)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.classification.confidence import Stage1Result
from apps.classification.context import DocContext

ENTITY_NAMES = {
    "unit",
    "owner",
    "owner_candidate",
    "tenant",
    "period_year",
    "period",
    "document_date",
    "own_object_marker",
    "foreign_object_marker",
    "own_company_as_agent",
    "contract_partner",
    "iban",
    "id_document",
    "amount",
    "email",  # Dokument ist eine E-Mail mit Kopfzeilen (26.09.2026)
}
CONDITION_KEYS = {
    "filename_regex",
    "text_regex",
    "head_regex",
    "not_text_regex",
    "not_filename_regex",
    "entity_required",
    "not_entity",
    "folder_code",
    "min_pages",
    "max_pages",
    "any_of",
    "email_subject_regex",  # gegen subject_clean der E-Mail (26.09.2026, Vorlage E-4)
    "email_sender_regex",  # gegen from_address und from_name der E-Mail
}
REGEX_KEYS = (
    "filename_regex",
    "text_regex",
    "head_regex",
    "not_text_regex",
    "not_filename_regex",
    "email_subject_regex",
    "email_sender_regex",
)
TEXT_FEATURE_KEYS = ("text_regex", "head_regex")
# Betreff-Regeln zaehlen als starkes Merkmal nur zusammen mit Textmerkmalen (Schwellen unveraendert): ohne
# text_regex, head_regex oder verlangte Entitaet ist der Treffer weich und bleibt unter threshold_auto_file
EMAIL_SUBJECT_ONLY_CONFIDENCE = 0.6


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    id: str
    name: str
    version: int = 1
    priority: int = 100
    scope: dict = field(default_factory=dict)
    when: dict = field(default_factory=dict)
    then: dict = field(default_factory=dict)
    examples: dict = field(default_factory=dict)
    active: bool = True

    @property
    def category(self) -> str | None:
        return self.then.get("category")

    @property
    def hard(self) -> bool:
        return bool(self.then.get("hard"))

    @property
    def confidence(self) -> float:
        return float(self.then.get("confidence", 0.8))

    def to_definition(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "priority": self.priority,
            "scope": self.scope,
            "when": self.when,
            "then": self.then,
            "examples": self.examples,
        }

    @classmethod
    def from_definition(cls, d: dict) -> Rule:
        rule = cls(
            id=d["id"],
            name=d.get("name") or d["id"],
            version=int(d.get("version", 1)),
            priority=int(d.get("priority", 100)),
            scope=d.get("scope") or {},
            when=d.get("when") or {},
            then=d.get("then") or {},
            examples=d.get("examples") or {},
            active=bool(d.get("active", True)),
        )
        validate(rule)
        return rule


@dataclass
class RuleHit:
    rule: Rule
    confidence: float
    hard: bool
    metadata: dict
    matched: list[str]

    def as_dict(self) -> dict:
        return {
            "rule": self.rule.id,
            "version": self.rule.version,
            "priority": self.rule.priority,
            "category": self.rule.then.get("category"),
            "subfolder": self.rule.then.get("subfolder"),
            "document_type": self.rule.then.get("document_type"),
            "confidence": self.confidence,
            "hard": self.hard,
            "matched": self.matched,
        }


def validate(rule: Rule) -> None:
    if not re.fullmatch(r"R-[0-9]{2}-[A-Z0-9]+(-[A-Z0-9]+)*-[0-9]{3}", rule.id):
        raise RuleError(f"Regel-ID {rule.id!r} nicht im Format R-NN-NAME-NNN")
    if rule.category not in {"01", "02", "03", "04", "05", "06"}:
        raise RuleError(
            f"{rule.id}: then.category muss ein Kategoriecode 01 bis 06 sein (B-12), nicht {rule.category!r}"
        )
    for key in rule.when:
        if key not in CONDITION_KEYS:
            raise RuleError(f"{rule.id}: unbekannte Bedingung {key}")
    for key in rule.when.get("any_of", {}):
        if key not in CONDITION_KEYS or key == "any_of":
            raise RuleError(f"{rule.id}: unbekannte Bedingung in any_of: {key}")
    for name in [*rule.when.get("entity_required", []), *rule.when.get("not_entity", [])]:
        if name not in ENTITY_NAMES:
            raise RuleError(f"{rule.id}: unbekannte Entität {name}")
    for key in REGEX_KEYS:
        for pattern in [*rule.when.get(key, []), *rule.when.get("any_of", {}).get(key, [])]:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RuleError(f"{rule.id}: ungültiger Ausdruck {pattern!r}: {exc}") from exc
    for mt in rule.scope.get("management_types", []):
        if mt not in ("weg", "rental", "weg_with_se"):
            raise RuleError(f"{rule.id}: Verwaltungsart {mt!r} muss ein Code sein (weg, rental, weg_with_se)")
    if not 0 <= rule.confidence <= 1:
        raise RuleError(f"{rule.id}: confidence außerhalb 0 bis 1")
    if uses_email_subject(rule) and rule.hard and not has_text_feature(rule):
        raise RuleError(f"{rule.id}: Betreff-Regel ohne Textmerkmal darf nicht hart sein (Vorlage E-4)")


def uses_email_subject(rule: Rule) -> bool:
    return "email_subject_regex" in rule.when or "email_subject_regex" in rule.when.get("any_of", {})


def uses_email_sender(rule: Rule) -> bool:
    return "email_sender_regex" in rule.when or "email_sender_regex" in rule.when.get("any_of", {})


def has_text_feature(rule: Rule) -> bool:
    """Textmerkmal im Sinne der Vorlage E-4: Text- oder Kopfmuster (auch in any_of) oder eine verlangte Entitaet
    ausser 'email' selbst."""
    if any(k in rule.when for k in TEXT_FEATURE_KEYS):
        return True
    any_of = rule.when.get("any_of", {})
    if "email_subject_regex" not in any_of and any(k in any_of for k in TEXT_FEATURE_KEYS):
        # any_of mit Betreffmuster ist schon ueber den Betreff allein erfuellt, die Textmuster darin sind dann
        # kein sicheres Merkmal (Gegenpruefung 26.09.2026)
        return True
    return any(n != "email" for n in rule.when.get("entity_required", []))


def rule_kind_for(rule: Rule) -> str:
    """rule_kind der Tabellenzeile (RuleKind): email_subject bei Betreffmuster, email_sender bei Absendermuster,
    sonst composite (26.09.2026)."""
    if uses_email_subject(rule):
        return "email_subject"
    if uses_email_sender(rule):
        return "email_sender"
    return "composite"


def rules_dir() -> Path:
    return Path(settings.OBJEKTAKTE["SEED_DIR"]) / "rules"


@lru_cache(maxsize=1)
def load_seed_rules() -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for path in sorted(rules_dir().glob("*.json")):
        for d in json.loads(path.read_text(encoding="utf-8")):
            rule = Rule.from_definition(d)
            if rule.id in seen:
                raise RuleError(f"Regel {rule.id} doppelt")
            seen.add(rule.id)
            rules.append(rule)
    return rules


def load_active_rules() -> list[Rule]:
    """Aktive Regeln aus classification_rules (definition); ohne Datenbankzeilen die Seed-Dateien."""
    from apps.documents.models import ClassificationRule

    rows = list(ClassificationRule.objects.filter(is_active=True, definition__isnull=False))
    if not rows:
        return [r for r in load_seed_rules() if r.active]
    return [Rule.from_definition(r.definition) for r in rows]


# ---------------------------------------------------------------- Auswertung
def _any(patterns: list[str], value: str) -> str | None:
    for p in patterns:
        if re.search(p, value or ""):
            return p
    return None


def _condition(key: str, spec, ctx: DocContext) -> tuple[bool, str | None]:
    if key == "filename_regex":
        m = _any(spec, ctx.filename)
        return m is not None, m and f"filename~{m}"
    if key == "text_regex":
        m = _any(spec, ctx.text)
        return m is not None, m and f"text~{m}"
    if key == "head_regex":
        m = _any(spec, ctx.head)
        return m is not None, m and f"head~{m}"
    if key == "not_text_regex":
        m = _any(spec, ctx.text)
        return m is None, None
    if key == "not_filename_regex":
        m = _any(spec, ctx.filename)
        return m is None, None
    if key == "entity_required":
        missing = [n for n in spec if not ctx.has(n)]
        return not missing, f"entities:{','.join(spec)}" if not missing else None
    if key == "not_entity":
        present = [n for n in spec if ctx.has(n)]
        return not present, None
    if key == "folder_code":
        ok = ctx.folder_code is not None and any(
            ctx.folder_code == c or ctx.folder_code.startswith(c + "/") for c in spec
        )
        return ok, f"folder:{ctx.folder_code}" if ok else None
    if key == "min_pages":
        return ctx.page_count >= int(spec), None
    if key == "max_pages":
        return ctx.page_count <= int(spec), None
    if key == "email_subject_regex":
        # nur E-Mails; Muster gegen den bereinigten Betreff (ohne AW:, WG:, Re:, Fwd:)
        if ctx.email is None:
            return False, None
        m = _any(spec, ctx.email.get("subject_clean") or "")
        return m is not None, m and f"email_subject~{m}"
    if key == "email_sender_regex":
        if ctx.email is None:
            return False, None
        m = _any(spec, ctx.email.get("from_address") or "") or _any(spec, ctx.email.get("from_name") or "")
        return m is not None, m and f"email_sender~{m}"
    return False, None


def matches(rule: Rule, ctx: DocContext) -> RuleHit | None:
    scope_types = rule.scope.get("management_types")
    if scope_types and ctx.management_type not in scope_types:
        return None
    matched: list[str] = []
    for key, spec in rule.when.items():
        if key == "any_of":
            hit = None
            for sub_key, sub_spec in spec.items():
                ok, why = _condition(sub_key, sub_spec, ctx)
                if ok:
                    hit = why or sub_key
                    break
            if hit is None:
                return None
            matched.append(f"any_of:{hit}")
            continue
        ok, why = _condition(key, spec, ctx)
        if not ok:
            return None
        if why:
            matched.append(why)
    metadata = {}
    for k, v in (rule.then.get("metadata") or {}).items():
        if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            metadata[k] = ctx.metadata().get(v[1:-1])
        else:
            metadata[k] = v
    confidence, hard = rule.confidence, rule.hard
    if uses_email_subject(rule) and not has_text_feature(rule):
        # Betreff allein ist ein weiches Merkmal (Vorlage E-4): Konfidenz gedeckelt, nie hart, Schwellen unveraendert
        confidence, hard = min(confidence, EMAIL_SUBJECT_ONLY_CONFIDENCE), False
    return RuleHit(rule, confidence, hard, metadata, matched)


def evaluate(ctx: DocContext, rules: list[Rule] | None = None) -> Stage1Result:
    rules = rules if rules is not None else load_active_rules()
    hits = [h for h in (matches(r, ctx) for r in rules if r.active) if h is not None]
    result = Stage1Result(hits=[h.as_dict() for h in hits])
    if not hits:
        return result
    hits.sort(key=lambda h: (-h.rule.priority, -h.confidence, h.rule.id))
    hard = [h for h in hits if h.hard]
    if hard:
        categories = {h.rule.category for h in hard}
        if len(categories) > 1:
            result.conflict = True
            result.rule_codes = [h.rule.id for h in hard]
            return result
        best = hard[0]
    else:
        best = hits[0]
    then = best.rule.then
    result.category = then.get("category")
    result.subfolder = then.get("subfolder")
    result.document_type = then.get("document_type")
    result.scope = then.get("scope")
    result.confidence = best.confidence
    result.hard = best.hard
    result.rule_codes = [h.rule.id for h in hits]
    result.metadata = best.metadata
    result.proposal = then.get("proposal")
    result.subtype = then.get("subtype")
    return result


def sync_rules_to_db(rules: list[Rule] | None = None) -> tuple[int, int, int]:
    """Seed-Regeln in classification_rules laden (idempotent): Zeile je Regel-ID mit definition und Zusammenfassung."""
    from apps.documents.models import ClassificationRule, DocumentSubfolder, DocumentType

    rules = rules if rules is not None else load_seed_rules()
    created = updated = unchanged = 0
    for rule in rules:
        sub = None
        if rule.then.get("subfolder"):
            sub = DocumentSubfolder.objects.filter(
                category_id=rule.category, code=rule.then["subfolder"]
            ).first()
        dtype = (
            DocumentType.objects.filter(code=rule.then["document_type"]).first()
            if rule.then.get("document_type")
            else None
        )
        values = {
            "name": rule.name,
            "rule_kind": rule_kind_for(rule),
            "pattern": json.dumps(rule.when, ensure_ascii=False),
            "target_category_id": rule.category,
            "target_subfolder": sub,
            "target_document_type": dtype,
            "scope_decision": rule.then.get("scope"),
            "weight": rule.confidence,
            "is_hard": rule.hard,
            "management_types": rule.scope.get("management_types") or None,
            "sort_order": max(0, min(32767, 1000 - rule.priority)),
            "is_active": rule.active,
            "version": rule.version,
            "definition": rule.to_definition(),
        }
        row, was_created = ClassificationRule.objects.get_or_create(code=rule.id, defaults=values)
        if was_created:
            created += 1
            continue
        if row.definition == values["definition"] and row.is_active == rule.active:
            unchanged += 1
            continue
        for k, v in values.items():
            setattr(row, k, v)
        row.save()
        updated += 1
    return created, updated, unchanged
