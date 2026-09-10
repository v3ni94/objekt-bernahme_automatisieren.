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
}


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
    for key in ("filename_regex", "text_regex", "head_regex", "not_text_regex", "not_filename_regex"):
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
    return RuleHit(rule, rule.confidence, rule.hard, metadata, matched)


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
            "rule_kind": "composite",
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
