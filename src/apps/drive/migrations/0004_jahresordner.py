# Jahresordner unter 03_Buchhaltung (24.09.2026): Knotenart year_folder, Feld year, Positionsschluessel mit Jahr.
# Der generierte Schluessel laesst sich nicht aendern, er wird entfernt und neu angelegt (Bestandszeilen bleiben).
# Datenbankseitig laeuft alles in EINER ALTER-Anweisung, damit waehrend des Deploys (Migration bei laufenden alten
# Containern) kein Zwischenzustand ohne position_key entsteht; der Django-Zustand wird getrennt gefuehrt.
# Rueckwaerts laeuft die Migration nur, solange keine Zeilen mit node_kind year_folder existieren (der alte
# CHECK und der alte Schluessel ohne Jahr wuerden sie verletzen); danach ist die Sicherung vor der Migration
# der Rueckweg, nicht migrate drive 0003. Empfehlung fuer das Deploy: Worker vorher anhalten (worker-stop).

import django.db.models.functions.comparison
from django.db import migrations, models

import objektakte.db


class Migration(migrations.Migration):
    dependencies = [
        ("drive", "0003_takeover_sources"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE `drive_nodes`\n  DROP INDEX `uq_drive_nodes_position`,\n  DROP CONSTRAINT IF EXISTS `ck_drive_nodes_kind`,\n  DROP COLUMN `position_key`,\n  ADD COLUMN `year` smallint UNSIGNED NULL CHECK (`year` >= 0),\n  ADD COLUMN `position_key` varchar(190) GENERATED ALWAYS AS (CASE WHEN `status` = 'active' THEN CONCAT_WS('|', `node_kind`, COALESCE(`object_id`, 0), COALESCE(`category_code`, ''), COALESCE(`subfolder_id`, 0), COALESCE(`owner_file_id`, 0), COALESCE(`tenant_file_id`, 0), COALESCE(`list_type`, ''), COALESCE(`list_format`, ''), COALESCE(`year`, 0)) ELSE NULL END) STORED,\n  ADD CONSTRAINT `ck_drive_nodes_kind` CHECK (`node_kind` IN ('data_root', 'object_root', 'main_folder', 'subfolder', 'owner_file_folder', 'owner_file_subfolder', 'tenant_file_folder', 'tenant_file_subfolder', 'list_file', 'year_folder')),\n  ADD CONSTRAINT `uq_drive_nodes_position` UNIQUE (`position_key`)",
                    reverse_sql="ALTER TABLE `drive_nodes`\n  DROP INDEX `uq_drive_nodes_position`,\n  DROP CONSTRAINT IF EXISTS `ck_drive_nodes_kind`,\n  DROP COLUMN `position_key`,\n  DROP COLUMN `year`,\n  ADD COLUMN `position_key` varchar(190) GENERATED ALWAYS AS (CASE WHEN `status` = 'active' THEN CONCAT_WS('|', `node_kind`, COALESCE(`object_id`, 0), COALESCE(`category_code`, ''), COALESCE(`subfolder_id`, 0), COALESCE(`owner_file_id`, 0), COALESCE(`tenant_file_id`, 0), COALESCE(`list_type`, ''), COALESCE(`list_format`, '')) ELSE NULL END) STORED,\n  ADD CONSTRAINT `ck_drive_nodes_kind` CHECK (`node_kind` IN ('data_root', 'object_root', 'main_folder', 'subfolder', 'owner_file_folder', 'owner_file_subfolder', 'tenant_file_folder', 'tenant_file_subfolder', 'list_file')),\n  ADD CONSTRAINT `uq_drive_nodes_position` UNIQUE (`position_key`)",
                ),
            ],
            state_operations=[
                migrations.RemoveConstraint(model_name="drivenode", name="uq_drive_nodes_position"),
                migrations.RemoveConstraint(model_name="drivenode", name="ck_drive_nodes_kind"),
                migrations.RemoveField(model_name="drivenode", name="position_key"),
                migrations.AlterField(
                    model_name="drivenode",
                    name="node_kind",
                    field=models.CharField(
                        choices=[
                            ("data_root", "Wurzel 01_Daten"),
                            ("object_root", "Objektordner"),
                            ("main_folder", "Hauptordner 01 bis 06"),
                            ("subfolder", "Unterordner"),
                            ("owner_file_folder", "Eigentümerakte"),
                            ("owner_file_subfolder", "Unterordner der Eigentümerakte"),
                            ("tenant_file_folder", "Mieterakte"),
                            ("tenant_file_subfolder", "Unterordner der Mieterakte"),
                            ("list_file", "Listen-Datei"),
                            ("year_folder", "Jahresordner (03 Buchhaltung)"),
                        ],
                        max_length=24,
                    ),
                ),
                migrations.AddField(
                    model_name="drivenode",
                    name="year",
                    field=models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="drivenode",
                    name="position_key",
                    field=models.GeneratedField(
                        db_persist=True,
                        expression=models.Case(
                            models.When(
                                status="active",
                                then=objektakte.db.ConcatWS(
                                    models.Value("|"),
                                    models.F("node_kind"),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("object_id"),
                                        models.Value(0),
                                        output_field=models.IntegerField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("category_id"),
                                        models.Value(""),
                                        output_field=models.CharField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("subfolder_id"),
                                        models.Value(0),
                                        output_field=models.IntegerField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("owner_file_id"),
                                        models.Value(0),
                                        output_field=models.IntegerField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("tenant_file_id"),
                                        models.Value(0),
                                        output_field=models.IntegerField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("list_type"),
                                        models.Value(""),
                                        output_field=models.CharField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("list_format"),
                                        models.Value(""),
                                        output_field=models.CharField(),
                                    ),
                                    django.db.models.functions.comparison.Coalesce(
                                        models.F("year"), models.Value(0), output_field=models.IntegerField()
                                    ),
                                ),
                            ),
                            default=models.Value(None),
                        ),
                        output_field=models.CharField(max_length=190, null=True),
                    ),
                ),
                migrations.AddConstraint(
                    model_name="drivenode",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "node_kind__in",
                                [
                                    "data_root",
                                    "object_root",
                                    "main_folder",
                                    "subfolder",
                                    "owner_file_folder",
                                    "owner_file_subfolder",
                                    "tenant_file_folder",
                                    "tenant_file_subfolder",
                                    "list_file",
                                    "year_folder",
                                ],
                            )
                        ),
                        name="ck_drive_nodes_kind",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="drivenode",
                    constraint=models.UniqueConstraint(
                        fields=("position_key",), name="uq_drive_nodes_position"
                    ),
                ),
            ],
        ),
    ]
