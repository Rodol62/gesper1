"""
Gruppo Django e permessi predefiniti per l’accesso al portale personale (dipendente/candidato).

Il gruppo raggruppa i permessi custom su ``accounts.User``; viene assegnato automaticamente
quando si collega un utente a un dipendente (anagrafica) o quando l’utente ha ruolo candidato/dipendente.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

# Nome mostrato in Admin → Autenticazione e autorizzazioni → Gruppi
GRUPPO_PORTALE_DIPENDENTE = 'Dipendente — portale self-service'

# Codename dei permessi custom (app accounts)
PERMESSI_PORTALE_CODENAMES = (
    'portale_documenti_personali',
    'portale_buste_paga_cud',
    'portale_presenze_calendario',
    'portale_richieste',
)


def get_o_crea_gruppo_portale_dipendente():
    """Restituisce il gruppo predefinito, con tutti i permessi portale collegati."""
    from .models import User

    group, _ = Group.objects.get_or_create(name=GRUPPO_PORTALE_DIPENDENTE)
    ct = ContentType.objects.get_for_model(User)
    perms = Permission.objects.filter(
        content_type=ct, codename__in=PERMESSI_PORTALE_CODENAMES
    )
    if perms.count() == len(PERMESSI_PORTALE_CODENAMES):
        group.permissions.set(perms)
    return group


def applica_gruppo_portale_a_utente(user):
    """Aggiunge il gruppo portale all’utente (idempotente)."""
    if not user or not getattr(user, 'pk', None):
        return
    group = Group.objects.filter(name=GRUPPO_PORTALE_DIPENDENTE).first()
    if not group:
        try:
            group = get_o_crea_gruppo_portale_dipendente()
        except Exception:
            return
    user.groups.add(group)


def sync_gruppo_portale_se_ruolo_portale(user):
    """Se l’utente ha ruolo candidato o dipendente, assicura il gruppo portale."""
    if not user or not getattr(user, 'pk', None):
        return
    if not hasattr(user, 'has_ruolo'):
        return
    if user.has_ruolo('dipendente') or user.has_ruolo('candidato'):
        applica_gruppo_portale_a_utente(user)


def sync_ruoli_e_gruppo_da_dipendente(dipendente):
    """
    Chiamato quando un Dipendente ha un utente collegato:
    - ruolo applicativo (M2M Ruolo) in base allo stato anagrafico
    - gruppo Django con i permessi portale
    - azienda sull’utente se mancante
    """
    user = getattr(dipendente, 'utente', None)
    if not user or not user.pk:
        return

    from .models import Ruolo

    applica_gruppo_portale_a_utente(user)

    stato = getattr(dipendente, 'stato', None) or ''
    if stato == 'attivo':
        r, _ = Ruolo.objects.get_or_create(
            codice='dipendente', defaults={'nome': 'Dipendente'}
        )
        user.ruoli.add(r)
    elif stato == 'candidato':
        r, _ = Ruolo.objects.get_or_create(
            codice='candidato', defaults={'nome': 'Candidato'}
        )
        user.ruoli.add(r)

    az = getattr(dipendente, 'azienda', None)
    if az and getattr(user, 'azienda_id', None) is None:
        user.azienda = az
        user.save(update_fields=['azienda'])
