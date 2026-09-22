from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("objects", "0002_eingangsobjekt"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedobject",
            name="additional_addresses",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="weitere Anschriften desselben Gebäudes (Eckobjekt, weitere Hausnummern): Liste aus street, house_number, postal_code, city",
            ),
        ),
    ]
