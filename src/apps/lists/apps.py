from django.apps import AppConfig


class ListsConfig(AppConfig):
    name = "apps.lists"
    verbose_name = "Listen"

    def ready(self) -> None:
        # Nachlauf bestaetigender Review-Entscheidungen (H 2.4 Regel 4, H 5.6 Nr. 2): entprellt je Objekt
        from apps.lists.services import request_generation
        from apps.review import hooks

        hooks.register(lambda object_id, key: request_generation(object_id, "review_confirm"))
