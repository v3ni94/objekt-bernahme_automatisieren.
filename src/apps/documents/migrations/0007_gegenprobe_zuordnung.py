from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0006_dokument_uuid_quelle_paperless"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="assignment_checked_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Gegenprobe der Objektzuordnung (Feldimport aus Paperless) abgeschlossen am",
                null=True,
            ),
        ),
    ]
