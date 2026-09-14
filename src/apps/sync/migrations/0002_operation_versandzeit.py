# Versandzeitpunkt je Operation (14.09.2026): dispatch_due versendet eine wartende Operation nur, wenn keine
# Nachricht unterwegs ist. Additiv, ohne Datenwanderung.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sync", "0001_synchronisation"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncoperation",
            name="dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
