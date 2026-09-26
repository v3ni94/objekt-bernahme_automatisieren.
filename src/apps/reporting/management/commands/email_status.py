"""E-Mail-Auswertung fuer die Regel 06 Sonstiges (Vorlage E-4, 26.09.2026), nur lesend: Stand der beiden Schalter,
E-Mail-Dokumente (.eml, .msg) nach Status, Ablageziel und Entscheider, Treffer der E-Mail-Regeln, Zuordnungen ueber
die Absenderadresse, automatische Ablagen nach 06/01. Ausgabe nur Zaehler und Codes: keine Betreffe, keine Absender,
keine Dateinamen. Optional je Objekt. Ergaenzt review-status (Faelle) um die Wirkung der Schalter."""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.config import store
from apps.documents.models import ClassificationRule, Document, DocumentClassification

MAIL = r"\.(eml|msg)$"


class Command(BaseCommand):
    help = "E-Mails: Schalter, Verteilung nach Status und Ziel, Regeltreffer, Absenderabgleich, Ablage nach 06/01"

    def add_arguments(self, parser):
        parser.add_argument("--objekt", help="nur dieses Objekt (Nummer)")

    def handle(self, *args, **options):
        nummer = (options["objekt"] or "").strip()
        if nummer and not nummer.isdigit():
            raise CommandError("Objektnummer muss aus Ziffern bestehen")
        out = self.stdout.write
        out(
            "Schalter: email.auto_misc="
            f"{'an' if store.get('email.auto_misc', False) else 'aus'}, email.match_sender_to_party="
            f"{'an' if store.get('email.match_sender_to_party', False) else 'aus'}"
        )
        regeln = ClassificationRule.objects.filter(
            is_active=True, rule_kind__in=["email_subject", "email_sender"]
        )
        out(
            f"E-Mail-Regeln aktiv: {regeln.count()} "
            f"(Betreff {regeln.filter(rule_kind='email_subject').count()}, "
            f"Absender {regeln.filter(rule_kind='email_sender').count()})"
        )
        docs = Document.objects.filter(deleted_at__isnull=True, original_name__iregex=MAIL)
        if nummer:
            docs = docs.filter(object__object_number_numeric=int(nummer))
        gesamt = docs.count()
        out(f"E-Mail-Dokumente: {gesamt}" + (f" (Objekt {nummer})" if nummer else ""))
        if not gesamt:
            return
        rows = docs.values("status").annotate(n=Count("id")).order_by("-n")
        out("  Status: " + ", ".join(f"{r['status']}={r['n']}" for r in rows))
        rows = (
            docs.exclude(category__isnull=True)
            .values("category_id", "subfolder__code")
            .annotate(n=Count("id"))
            .order_by("-n")[:12]
        )
        out(
            "  Kategorie/Unterordner: "
            + (
                ", ".join(f"{r['category_id']}/{r['subfolder__code'] or '-'}={r['n']}" for r in rows)
                or "keine"
            )
        )
        rows = docs.values("final_decided_by").annotate(n=Count("id")).order_by("-n")
        out("  entschieden durch: " + ", ".join(f"{r['final_decided_by'] or '-'}={r['n']}" for r in rows))
        auto = docs.filter(final_decided_by="email_auto").count()
        out(f"Automatisch nach 06/01 (email.auto_misc): {auto}")
        final = DocumentClassification.objects.filter(document__in=docs, is_final=True)
        absender = Counter()
        for kind in final.filter(features__email_sender_match__in=["owner", "tenant"]).values_list(
            "features__email_sender_match", flat=True
        ):
            absender[kind] += 1
        out(
            "Ueber Absenderadresse zugeordnet: "
            f"{sum(absender.values())} (Eigentuemer {absender.get('owner', 0)}, Mieter {absender.get('tenant', 0)})"
        )
        treffer = DocumentClassification.objects.filter(
            document__in=docs, stage=1, provider="rules", reasoning__contains="-EMAIL-"
        )
        out(f"Stufe-1-Zeilen mit Treffer einer E-Mail-Regel: {treffer.count()}")
        je_regel: Counter = Counter()
        for reasoning in treffer.values_list("reasoning", flat=True).iterator(chunk_size=1000):
            for code in (reasoning or "").replace(" (Konflikt)", "").split(", "):
                if "-EMAIL-" in code:
                    je_regel[code] += 1
        if je_regel:
            out("  je Regel: " + ", ".join(f"{k}={v}" for k, v in je_regel.most_common(15)))
        rows = docs.values("object__object_number").annotate(n=Count("id")).order_by("-n")[:10]
        out(
            "  Objekte mit den meisten E-Mails: "
            + ", ".join(f"{r['object__object_number']}={r['n']}" for r in rows)
        )
