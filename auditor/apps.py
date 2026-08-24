from django.apps import AppConfig


class AuditorConfig(AppConfig):
    # O schema histórico da aplicação usa AutoField; declarar explicitamente
    # evita migrations espúrias e elimina o warning do Django.
    default_auto_field = "django.db.models.BigAutoField"
    name = "auditor"
