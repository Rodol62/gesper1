from django.apps import AppConfig


class SandboxDimostrativoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sandbox_dimostrativo"
    verbose_name = "Sandbox dimostrativo"

    def ready(self) -> None:
        from sandbox_dimostrativo import signals  # noqa: F401 — sessione post-login sandbox
