from django.apps import AppConfig
from django.db.models.signals import post_save


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Signal: collegamento Dipendente ↔ Utente → gruppo e ruoli portale
        from . import signals_dipendente  # noqa: F401
        # Signal: allineamento dati anagrafici User/ProfiloCandidato/Dipendente
        from . import signals_anagrafica  # noqa: F401
        from accounts.models import ConfigurazioneSistema

        post_save.connect(
            self._on_configurazione_sistema_saved,
            sender=ConfigurazioneSistema,
            dispatch_uid='gesper_sync_email_on_config_save',
        )

    @staticmethod
    def _on_configurazione_sistema_saved(sender, instance, **kwargs):
        from accounts.email_backend import apply_config_smtp_to_settings

        apply_config_smtp_to_settings(instance)
