# Volltextindex auf den maskierten Seitentext (Fachentwurf D 6.2). Django kennt keinen FULLTEXT-Index,
# daher als SQL mit Rueckwaertsoperation. InnoDB-Volltext ohne deutsche Stammformreduktion (ANNAHME D 15.2).
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_fremdschluessel_parties_review")]

    operations = [
        migrations.RunSQL(
            sql="CREATE FULLTEXT INDEX ft_pages_text ON document_pages (text_content)",
            reverse_sql="DROP INDEX ft_pages_text ON document_pages",
        )
    ]
