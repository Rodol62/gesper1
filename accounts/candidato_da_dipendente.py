"""
Collegamento anagrafiche HR (Dipendente stato=candidato) al flusso registrazione portale.

Stesse regole del candidato che si registra da solo:
- username: nome.cognome (univoco)
- password iniziale: codice fiscale (16 caratteri)
- account attivo dopo verifica e-mail (come portale)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

if TYPE_CHECKING:
    from accounts.models import User
    from anagrafiche.models import Dipendente

logger = logging.getLogger('django')


@dataclass
class RisultatoAccountCandidato:
    utente: User | None
    username: str = ''
    creato: bool = False
    errore: str = ''
    messaggio_hr: str = ''


def _cf_valido(dip: Dipendente) -> str:
    cf = (dip.codice_fiscale or '').strip().upper()
    if len(cf) != 16:
        raise ValidationError(
            'Per creare l’account portale del candidato serve un codice fiscale valido (16 caratteri).'
        )
    return cf


def _username_portale(dip: Dipendente) -> str:
    from accounts.registrazione_otp import allocate_username, build_username_nome_cognome

    try:
        base = build_username_nome_cognome(dip.nome or '', dip.cognome or '')
    except ValidationError:
        base = (dip.codice_fiscale or dip.email or f'dip{dip.pk}').strip().lower()[:40]
        base = base.replace('@', '_')
    return allocate_username(base)


def _crea_profilo_da_dipendente(utente, dip: Dipendente, cf: str):
    from accounts.models import ProfiloCandidato

    return ProfiloCandidato.objects.create(
        user=utente,
        dipendente=dip,
        azienda_interesse=dip.azienda,
        codice_fiscale=cf[:16],
        data_nascita=dip.data_nascita,
        luogo_nascita=dip.luogo_nascita or '',
        sesso=dip.sesso or '',
        indirizzo=dip.indirizzo or '',
        cap=dip.cap or '',
        citta=dip.citta or '',
        provincia=dip.provincia or '',
        regione_residenza=dip.regione_residenza or '',
        telefono=dip.telefono or '',
    )


def _messaggio_credenziali_hr(username: str, creato: bool) -> str:
    azione = 'creato' if creato else 'aggiornato'
    return (
        f'Account portale {azione}: utente «{username}», password iniziale = codice fiscale '
        f'(come registrazione candidato). Il candidato deve verificare l’e-mail prima del primo accesso.'
    )


@transaction.atomic
def assicura_account_candidato_da_dipendente(dip: Dipendente) -> RisultatoAccountCandidato:
    """
    Per Dipendente stato «candidato»: crea o collega User + ProfiloCandidato come da portale.
    Idempotente.
    """
    from accounts.models import ProfiloCandidato, Ruolo, User

    if dip.stato != 'candidato':
        return RisultatoAccountCandidato(utente=dip.utente)

    profilo_esistente = getattr(dip, 'profilo_candidato', None)
    if profilo_esistente:
        if not dip.utente_id:
            dip.utente = profilo_esistente.user
            dip.save(update_fields=['utente'])
        u = profilo_esistente.user
        return RisultatoAccountCandidato(
            utente=u,
            username=u.username,
            messaggio_hr=_messaggio_credenziali_hr(u.username, creato=False),
        )

    if dip.utente_id:
        utente = dip.utente
        if not hasattr(utente, 'profilo_candidato'):
            try:
                cf = _cf_valido(dip)
            except ValidationError as exc:
                return RisultatoAccountCandidato(utente=utente, errore=str(exc))
            _crea_profilo_da_dipendente(utente, dip, cf)
        ruolo_candidato = Ruolo.objects.filter(codice='candidato').first()
        if ruolo_candidato and not utente.ruoli.filter(codice='candidato').exists():
            utente.ruoli.add(ruolo_candidato)
        return RisultatoAccountCandidato(
            utente=utente,
            username=utente.username,
            messaggio_hr=_messaggio_credenziali_hr(utente.username, creato=False),
        )

    email = (dip.email or '').strip().lower()
    if not email:
        return RisultatoAccountCandidato(
            errore='E-mail obbligatoria per l’account portale del candidato.',
        )

    try:
        cf = _cf_valido(dip)
    except ValidationError as exc:
        return RisultatoAccountCandidato(errore=str(exc))

    if User.objects.filter(email__iexact=email).exists():
        return RisultatoAccountCandidato(
            errore='Esiste già un account con questa e-mail: collegare manualmente il dipendente.',
        )
    if ProfiloCandidato.objects.filter(codice_fiscale__iexact=cf).exists():
        return RisultatoAccountCandidato(
            errore='Questo codice fiscale risulta già registrato su un altro profilo candidato.',
        )

    username = _username_portale(dip)
    nome = (dip.nome or '').strip().upper()[:150]
    cognome = (dip.cognome or '').strip().upper()[:150]

    utente = User(
        username=username,
        email=email,
        first_name=nome,
        last_name=cognome,
        azienda=dip.azienda,
        email_verificata=False,
        convalidato=False,
        is_active=False,
        privacy_accettata=True,
    )
    utente.set_password(cf)
    utente.save()

    ruolo_candidato, _ = Ruolo.objects.get_or_create(codice='candidato', defaults={'nome': 'Candidato'})
    utente.ruoli.add(ruolo_candidato)

    _crea_profilo_da_dipendente(utente, dip, cf)

    dip.utente = utente
    dip.save(update_fields=['utente'])

    logger.info(
        '[CANDIDATO_HR] Account portale (stile registrazione) dip %s → user %s (%s)',
        dip.pk, utente.pk, username,
    )
    return RisultatoAccountCandidato(
        utente=utente,
        username=username,
        creato=True,
        messaggio_hr=_messaggio_credenziali_hr(username, creato=True),
    )


def assicura_candidati_hr_azienda(azienda_id: int | None) -> int:
    """Backfill: collega dipendenti candidato senza utente. Restituisce il numero gestiti."""
    from anagrafiche.models import Dipendente

    qs = Dipendente.objects.filter(stato='candidato', utente__isnull=True)
    if azienda_id:
        qs = qs.filter(azienda_id=azienda_id)
    n = 0
    for dip in qs.select_related('azienda'):
        if assicura_account_candidato_da_dipendente(dip).utente:
            n += 1
    return n


def notifica_credenziali_da_risultato(request, risultato: RisultatoAccountCandidato) -> None:
    """Mostra messaggi Django dopo crea/modifica dipendente candidato."""
    from django.contrib import messages

    if risultato.errore:
        messages.warning(request, risultato.errore)
    elif risultato.messaggio_hr:
        messages.success(request, risultato.messaggio_hr)
        if risultato.creato and risultato.utente and request is not None:
            try:
                from accounts.views_registration import _invia_email_verifica

                token = risultato.utente.genera_token_verifica()
                _invia_email_verifica(request, risultato.utente, token)
                messages.info(
                    request,
                    f'Inviata e-mail di verifica a {risultato.utente.email} (come registrazione portale).',
                )
            except Exception as exc:
                logger.warning(
                    '[CANDIDATO_HR] Invio e-mail verifica fallito user %s: %s',
                    risultato.utente.pk, exc,
                )
                messages.warning(
                    request,
                    'Account creato ma e-mail di verifica non inviata: controlla SMTP in impostazioni.',
                )
