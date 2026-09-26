from django.apps import AppConfig


class CrmApiConfig(AppConfig):
    name = "apps.crm_api"
    verbose_name = "CRM-Schnittstelle (M29 Stufe 3)"

    def ready(self) -> None:
        # Celery-Task crm_api.send_webhook beim Start registrieren (Worker io, eager in Tests)
        from apps.crm_api import tasks  # noqa: F401
