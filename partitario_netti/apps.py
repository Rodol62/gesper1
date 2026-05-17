from django.apps import AppConfig


class PartitarioNettiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "partitario_netti"
    verbose_name = "Partitario netti dipendenti"

    def ready(self) -> None:
        import partitario_netti.signals  # noqa: F401 — registrazione receiver referenziali
