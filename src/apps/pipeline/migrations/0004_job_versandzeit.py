# Versandzeitpunkt je Job (14.09.2026): Nachversand nur fuer nie versandte oder seit Tagen nicht angekommene
# Nachrichten wartender Jobs laufender Laeufe. Additiv, ohne Datenwanderung.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0003_job_objektzuordnung"),
    ]

    operations = [
        migrations.AddField(
            model_name="processingjob",
            name="dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
