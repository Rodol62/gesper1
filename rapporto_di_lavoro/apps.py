from django.apps import AppConfig


class RapportoDiLavoroConfig(AppConfig):
    name = 'rapporto_di_lavoro'

    def ready(self):
        # Segnali: sync anagrafica dipendente da RapportoDiLavoro
        from . import signals  # noqa: F401
