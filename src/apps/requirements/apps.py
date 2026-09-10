from django.apps import AppConfig


class RequirementsConfig(AppConfig):
    name = "apps.requirements"
    verbose_name = "Vollstaendigkeit und Nachforderung"

    def ready(self) -> None:
        # Nachlauf bestaetigender Review-Entscheidungen (H 2.4 Regel 4, H 3.5): entprellt je Objekt
        from apps.requirements.tasks import trigger_evaluation
        from apps.review import hooks

        hooks.register(lambda object_id, key: trigger_evaluation(object_id, "review"))
