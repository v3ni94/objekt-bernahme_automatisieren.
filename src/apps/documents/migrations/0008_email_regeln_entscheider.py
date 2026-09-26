# E-Mail-Regeln und automatische Ablage von E-Mails (26.09.2026, Vorlage E-4): rule_kind um email_subject und
# email_sender, final_decided_by um email_auto; beide CHECK-Constraints wie im Bestand (0005, 0006) ersetzt.

from django.db import migrations, models

RULE_KINDS = [
    "filename",
    "folder",
    "keyword",
    "entity",
    "period",
    "composite",
    "email_subject",
    "email_sender",
]
DECIDERS = ["stage1", "stage2", "stage3", "human", "email_auto"]


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0007_gegenprobe_zuordnung"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="classificationrule", name="ck_rules_kind"),
        migrations.AlterField(
            model_name="classificationrule",
            name="rule_kind",
            field=models.CharField(
                choices=[
                    ("filename", "Dateiname"),
                    ("folder", "Herkunftsordner"),
                    ("keyword", "Stichwort im Text"),
                    ("entity", "erkannte Entität"),
                    ("period", "Zeitbezug"),
                    ("composite", "zusammengesetzte Regel (Definition nach E 2.2)"),
                    ("email_subject", "Betreff einer E-Mail"),
                    ("email_sender", "Absender einer E-Mail"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="classificationrule",
            constraint=models.CheckConstraint(
                condition=models.Q(("rule_kind__in", RULE_KINDS)), name="ck_rules_kind"
            ),
        ),
        migrations.RemoveConstraint(model_name="document", name="ck_documents_decider"),
        migrations.AlterField(
            model_name="document",
            name="final_decided_by",
            field=models.CharField(
                blank=True,
                choices=[
                    ("stage1", "Stufe 1"),
                    ("stage2", "Stufe 2"),
                    ("stage3", "Stufe 3"),
                    ("human", "Mensch"),
                    ("email_auto", "E-Mail automatisch nach Sonstiges"),
                ],
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.CheckConstraint(
                condition=models.Q(("final_decided_by__isnull", True))
                | models.Q(("final_decided_by__in", DECIDERS)),
                name="ck_documents_decider",
            ),
        ),
    ]
