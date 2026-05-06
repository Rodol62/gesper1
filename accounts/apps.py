from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.db.models.signals import post_save


def _gesper_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor != 'sqlite':
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
    except Exception:
        pass


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        connection_created.connect(
            _gesper_sqlite_pragmas,
            dispatch_uid='gesper_sqlite_journal_wal',
        )
        # Signal: collegamento Dipendente ↔ Utente → gruppo e ruoli portale
        from . import signals_dipendente  # noqa: F401
        # Signal: allineamento dati anagrafici User/ProfiloCandidato/Dipendente
        from . import signals_anagrafica  # noqa: F401
        # Signal: dopo eliminazione movimento partitario → ricalcolo saldi
        from . import signals_registro_studio  # noqa: F401
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
