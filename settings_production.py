"""
Settings di produzione per GESPER su Oracle Cloud
Importa tutto da settings.py e sovrascrive solo ciò che serve per la produzione.

Avvio tipico:
  export DJANGO_SETTINGS_MODULE=settings_production
  gunicorn wsgi:application --bind unix:/run/gesper/gunicorn.sock
"""
import os
from pathlib import Path

from settings import *

# === SICUREZZA ===
DEBUG = False
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'CAMBIA-QUESTA-CHIAVE-IN-PRODUZIONE')

_allowed = os.environ.get('GESPER_ALLOWED_HOSTS', '').strip()
if _allowed:
    ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()]
else:
    ALLOWED_HOSTS = [
        'www.plazapretoria.it',
        'plazapretoria.it',
        'gesper1.plazapretoria.it',
        'gesper.plazapretoria.it',  # opzionale: stesso IP/DNS durante migrazione
    ]

# === URL PREFIX ===
# Produzione in root su gesper1.plazapretoria.it (senza prefisso /gesper).
# Per www.plazapretoria.it/gesper/ usare settings_production_www + secondo Gunicorn.
FORCE_SCRIPT_NAME = None
# Link «Visualizza sito» nell'admin (override in settings_production_www)
GESPER_ADMIN_SITE_URL = "/moduli/"

# === STATIC FILES ===
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# === MEDIA + DATABASE (radice dati unificata opzionale) ===
# Consigliato in produzione: stessa radice per SQLite e file caricati (allineato a Nginx /media).
# Esempio in /etc/gesper.env (Hetzner / migrazione Aruba):
#   GESPER_DATA_ROOT=/var/www/gesper
#   → DB: /var/www/gesper/db.sqlite3, media: /var/www/gesper/media/
# Alternativa documentata nel repo: GESPER_DATA_ROOT=/var/www/gesper/documento
# Override puntuali (se DB e media non condividono la stessa radice):
#   GESPER_SQLITE_PATH=/percorso/db.sqlite3
#   GESPER_MEDIA_ROOT=/percorso/media
# Nginx: alias per /media/ deve puntare allo stesso path di MEDIA_ROOT (vedi deploy/nginx-*.conf).
# Senza GESPER_DATA_ROOT: MEDIA_ROOT=/var/www/media e DB sotto BASE_DIR (WorkingDirectory Gunicorn).
_pd = os.environ.get("GESPER_DATA_ROOT", "").strip()
_media_override = os.environ.get("GESPER_MEDIA_ROOT", "").strip()
_sqlite_override = os.environ.get("GESPER_SQLITE_PATH", "").strip()
if _pd:
    _root = Path(_pd).expanduser().resolve()
    if _media_override:
        MEDIA_ROOT = str(Path(_media_override).expanduser().resolve())
    else:
        MEDIA_ROOT = str(_root / "media")
    _db_name = (
        Path(_sqlite_override).expanduser().resolve()
        if _sqlite_override
        else _root / "db.sqlite3"
    )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": _db_name,
            "OPTIONS": {"timeout": 60},
        }
    }
    GESPER_DATA_ROOT = _root
else:
    if _media_override:
        MEDIA_ROOT = str(Path(_media_override).expanduser().resolve())
    else:
        MEDIA_ROOT = "/var/www/media"
    _db_name = (
        Path(_sqlite_override).expanduser().resolve()
        if _sqlite_override
        else BASE_DIR / "db.sqlite3"
    )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": _db_name,
            "OPTIONS": {"timeout": 60},
        }
    }

# === CSRF E SESSIONI (HTTPS) ===
_csrf = os.environ.get('GESPER_CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(',') if o.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        'https://www.plazapretoria.it',
        'https://plazapretoria.it',
        'https://gesper1.plazapretoria.it',
        'https://gesper.plazapretoria.it',
    ]
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
# Obbligatorio su gesper1.* in root: cookie validi su tutto il path pubblico (/rapporti/, /accounts/, …).
# Se systemd usa per errore ``settings_production_www``, quel modulo imposta SESSION_COOKIE_PATH=/gesper/:
# il browser non invia più la sessione su URL come /rapporti/contratti/N/ → redirect continuo al login.
SESSION_COOKIE_PATH = '/'
CSRF_COOKIE_PATH = '/'
SECURE_SSL_REDIRECT = False  # Nginx gestisce HTTPS, non Django
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Nginx imposta il nome host con ``proxy_set_header Host $host``. Con
# USE_X_FORWARDED_HOST=True un client può inviare ``X-Forwarded-Host`` arbitrario:
# Django lo preferisce a ``Host`` e può generare DisallowedHost → HTTP 400.
USE_X_FORWARDED_HOST = False

# === E-mail ===
# SMTP e mittente: da Admin GESPER → Configurazione di sistema (vedi accounts.email_backend).
# Opzionale: variabili EMAIL_* in .env solo come fallback se SMTP non è ancora compilato in admin.

# === LOGIN ===
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/accounts/profile/'

# === CACHE (OTP registrazione, login step-up) ===
# Con più worker Gunicorn usare Redis (es. in /etc/gesper.env):
#   GESPER_REDIS_URL=redis://127.0.0.1:6379/1
# Senza variabile, settings.py usa LocMem (non condivisa tra worker).

# === LOGGING PRODUZIONE ===
_log_default = str(BASE_DIR / 'logs' / 'gesper.log')
_LOG_FILE = os.environ.get('GESPER_LOG_FILE', '').strip() or _log_default
Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': _LOG_FILE,
        },
    },
    'root': {
        'handlers': ['file'],
        'level': 'WARNING',
    },
}
