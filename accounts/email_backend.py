"""
Backend SMTP che bypassa la verifica SSL dei certificati.
Necessario su macOS con Python da python.org (certificati di sistema non configurati).
NON usare su server pubblici esposti a Internet.

ConfigurazioneSistemaEmailBackend: usa host/porta/utente/password da
Admin → Configurazione di sistema (tabella singleton); se incompleto, fallback su settings.
"""
import ssl
import smtplib
from django.core.mail.backends.smtp import EmailBackend as _SMTPBackend
from django.core.mail.utils import DNS_NAME


def apply_config_smtp_to_settings(config=None):
    """
    Allinea ``DEFAULT_FROM_EMAIL`` e i campi ``EMAIL_*`` di Django a una
    ``ConfigurazioneSistema`` (singleton admin). Usato dopo salvataggio in admin
    e alla creazione della connessione SMTP.
    """
    try:
        if config is None:
            from accounts.models import ConfigurazioneSistema

            config = ConfigurazioneSistema.get()
    except Exception:
        return
    if not (
        config.smtp_user
        and config.smtp_password
        and config.smtp_host
        and config.smtp_port
    ):
        return
    try:
        from django.conf import settings as dj_settings

        dj_settings.DEFAULT_FROM_EMAIL = config.from_email()
        dj_settings.EMAIL_HOST = config.smtp_host
        dj_settings.EMAIL_PORT = int(config.smtp_port)
        dj_settings.EMAIL_HOST_USER = config.smtp_user
        dj_settings.EMAIL_HOST_PASSWORD = config.smtp_password
        dj_settings.EMAIL_USE_TLS = bool(config.smtp_use_tls and not config.smtp_use_ssl)
        dj_settings.EMAIL_USE_SSL = bool(config.smtp_use_ssl)
    except Exception:
        pass


class UnverifiedSSLEmailBackend(_SMTPBackend):
    """
    Identico al backend SMTP standard di Django, ma passa un contesto SSL
    non verificato a starttls() — risolve il CERTIFICATE_VERIFY_FAILED su macOS.
    """

    def open(self):
        if self.connection:
            return False

        ctx = ssl._create_unverified_context()

        connection_params = {'local_hostname': DNS_NAME.get_fqdn()}
        if self.use_ssl:
            connection_params['context'] = ctx

        try:
            self.connection = self.connection_class(
                self.host, self.port, **connection_params
            )
            if not self.use_ssl and self.use_tls:
                self.connection.ehlo()
                self.connection.starttls(context=ctx)
                self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except OSError:
            if not self.fail_silently:
                raise


class ConfigurazioneSistemaEmailBackend(UnverifiedSSLEmailBackend):
    """
    SMTP letto da ``ConfigurazioneSistema`` (impostazioni admin GESPER).
    Se utente e password SMTP non sono compilati in admin, si usano ``EMAIL_*`` di Django.
    """

    def __init__(
        self,
        host=None,
        port=None,
        username=None,
        password=None,
        use_tls=None,
        fail_silently=False,
        use_ssl=None,
        timeout=None,
        ssl_keyfile=None,
        ssl_certfile=None,
        **kwargs,
    ):
        try:
            from accounts.models import ConfigurazioneSistema

            c = ConfigurazioneSistema.get()
        except Exception:
            c = None

        if c and c.smtp_user and c.smtp_password and c.smtp_host and c.smtp_port:
            host = c.smtp_host
            port = int(c.smtp_port)
            username = c.smtp_user
            password = c.smtp_password
            use_tls = bool(c.smtp_use_tls and not c.smtp_use_ssl)
            use_ssl = bool(c.smtp_use_ssl)
            apply_config_smtp_to_settings(c)

        super().__init__(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            fail_silently=fail_silently,
            use_ssl=use_ssl,
            timeout=timeout,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            **kwargs,
        )
