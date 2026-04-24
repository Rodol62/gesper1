from django.apps import AppConfig


class DocumentiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "documenti"
    verbose_name = "Documenti"

    def ready(self) -> None:
        from . import checks  # noqa: F401 — registra i system check Django
        from . import signals  # noqa: F401 — pre_delete Documento → CedolinoMotoreV4
        from .upload_paths import ensure_documenti_media_subdirs

        try:
            ensure_documenti_media_subdirs()
        except Exception:
            # Non bloccare l'avvio Django (es. MEDIA_ROOT read-only in container read-only)
            import logging

            logging.getLogger(__name__).exception("ensure_documenti_media_subdirs")
