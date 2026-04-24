"""
Produzione: GESPER sotto https://www.plazapretoria.it/gesper/ (sito aziendale in root).

Avvio separato da quello in root su gesper1.plazapretoria.it (o gesper.*), es.:
  gunicorn wsgi:application --bind 127.0.0.1:8003 \\
    --env DJANGO_SETTINGS_MODULE=settings_production_www

Stesso DB/media/static on disk del servizio principale; solo URL pubblici cambiano.
"""
import os
from pathlib import Path

from settings_production import *  # noqa: F403

# Host: se GESPER_ALLOWED_HOSTS elenca solo gesper.*, qui servono anche www/apex.
# Inoltre in settings_production USE_X_FORWARDED_HOST=False: con True, un client
# può mandare X-Forwarded-Host non ammesso e Django risponde 400 (DisallowedHost).
_www_public_hosts = ("www.plazapretoria.it", "plazapretoria.it")
ALLOWED_HOSTS = list(dict.fromkeys([*(ALLOWED_HOSTS or []), *_www_public_hosts]))

# Prefisso URL (coerente con location Nginx /gesper/)
FORCE_SCRIPT_NAME = "/gesper"
STATIC_URL = "/gesper/static/"
MEDIA_URL = "/gesper/media/"

LOGIN_URL = "/gesper/accounts/login/"
LOGIN_REDIRECT_URL = "/gesper/accounts/profile/"

# Link «Visualizza sito» nell'admin (urls.py legge questo attributo)
GESPER_ADMIN_SITE_URL = "/gesper/moduli/"

# Sessione/CSRF limitati al prefisso (non interferiscono col sito in /)
SESSION_COOKIE_PATH = "/gesper/"
CSRF_COOKIE_PATH = "/gesper/"

_log_default = str(BASE_DIR / "logs" / "gesper_www.log")  # noqa: F405
_LOG_FILE = os.environ.get("GESPER_LOG_FILE_WWW", "").strip() or _log_default
Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
LOGGING["handlers"]["file"]["filename"] = _LOG_FILE  # noqa: F405
