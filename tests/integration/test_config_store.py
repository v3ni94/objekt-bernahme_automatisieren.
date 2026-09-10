import pytest

from apps.config import store
from apps.config.models import AppSetting

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_seed_legt_alle_katalogschluessel_an(seeded):
    assert AppSetting.objects.count() == len(store.seeds())
    assert set(store.seeds()) <= set(store.catalog())
    assert store.get("classification.threshold_auto_file") == 0.90
    assert store.get("security.iban_decrypt_roles") == []


def test_seed_ist_idempotent(seeded):
    created, updated = store.seed_missing()
    assert (created, updated) == (0, 0)


def test_set_validiert_gegen_schema_und_protokolliert(seeded, admin_user):
    from apps.audit.models import AuditEvent

    with pytest.raises(store.InvalidSetting):
        store.set("classification.threshold_auto_file", 1.5, user=admin_user)
    with pytest.raises(store.InvalidSetting):
        store.set("owner_file.unit_prefix_mode", "irgendwas", user=admin_user)
    store.set("owner_file.unit_prefix_mode", "by_type", user=admin_user, reason="Test")
    assert store.get("owner_file.unit_prefix_mode") == "by_type"
    e = AuditEvent.objects.filter(action="setting.update").latest("id")
    assert (
        e.before_state["value"] == "always_we"
        and e.after_state["value"] == "by_type"
        and e.user_id == admin_user.pk
    )


def test_unbekannter_schluessel_wird_abgewiesen(seeded):
    with pytest.raises(store.UnknownSetting):
        store.get("gibt.es.nicht")


def test_altbezeichnung_nur_im_seed(seeded):
    aliases = store.get("drive.legacy_folder_aliases")
    assert "06_Sonstiges" in aliases and len(aliases["06_Sonstiges"]) == 1
