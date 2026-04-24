"""Token firmati e invio link certificazione firma (e-mail)."""

from __future__ import annotations

import logging
from typing import Literal

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse

from accounts.models import ConfigurazioneSistema
from accounts.outbound_uri import outbound_absolute_uri

logger = logging.getLogger(__name__)

_SIGNER_SALT = "gesper.certificazione-firma.v1"
_MAX_AGE_SEC = 60 * 60 * 24 * 14  # 14 giorni

CertKind = Literal["candidato", "dipendente"]


def crea_token_certificazione_firma(kind: CertKind, user_id: int, dipendente_id: int | None) -> str:
    signer = TimestampSigner(salt=_SIGNER_SALT)
    did = dipendente_id or 0
    return signer.sign(f"{kind}:{user_id}:{did}")


def decodifica_token_certificazione_firma(token: str) -> tuple[CertKind, int, int | None]:
    signer = TimestampSigner(salt=_SIGNER_SALT)
    raw = signer.unsign(token, max_age=_MAX_AGE_SEC)
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError("token malformato")
    kind_s, uid_s, did_s = parts
    if kind_s not in ("candidato", "dipendente"):
        raise ValueError("tipo non valido")
    uid = int(uid_s)
    did = int(did_s)
    return kind_s, uid, (did if did else None)  # type: ignore[return-value]


def costruisci_url_certificazione(request, token: str) -> str:
    """URL assoluto per il destinatario (gestisce localhost tramite ``url_pubblica_base``)."""
    path = reverse("certificazione_firma_pubblica", kwargs={"token": token})
    return outbound_absolute_uri(request, path)


def invia_email_certificazione_firma(
    *,
    destinatario_email: str,
    nome_destinatario: str,
    url_cert: str,
    nome_sito: str,
) -> None:
    config = ConfigurazioneSistema.get()
    corpo = (
        f"Gentile {nome_destinatario},\n\n"
        f"per completare la certificazione di avvenuta firma (o presa visione del processo contrattuale) "
        f"su {nome_sito}, apri il link seguente entro 14 giorni:\n\n"
        f"{url_cert}\n\n"
        f"Dopo l'apertura del link potrai confermare la ricezione.\n\n"
        f"— {nome_sito}"
    )
    if config.smtp_user and config.smtp_password:
        conn = get_connection(
            backend="accounts.email_backend.ConfigurazioneSistemaEmailBackend",
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_user,
            password=config.smtp_password,
            use_tls=config.smtp_use_tls and not config.smtp_use_ssl,
            use_ssl=config.smtp_use_ssl,
            fail_silently=False,
        )
        msg = EmailMessage(
            subject=f"[{nome_sito}] Certificazione firma — link di conferma",
            body=corpo,
            from_email=config.from_email(),
            to=[destinatario_email],
            connection=conn,
        )
    else:
        msg = EmailMessage(
            subject=f"[{nome_sito}] Certificazione firma — link di conferma",
            body=corpo,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@gesper.it"),
            to=[destinatario_email],
        )
    msg.send()


__all__ = [
    "crea_token_certificazione_firma",
    "decodifica_token_certificazione_firma",
    "costruisci_url_certificazione",
    "invia_email_certificazione_firma",
]
