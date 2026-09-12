from django.apps import AppConfig


class SyncConfig(AppConfig):
    name = "apps.sync"
    verbose_name = "Synchronisation Paperless und Drive"

    def ready(self) -> None:
        # Celery-Tasks (sync.* und pipeline.assign_object) beim Start registrieren, damit Worker, Beat und der
        # lokale Job-Runner sie ohne gesonderten Import finden
        from apps.sync import tasks  # noqa: F401
