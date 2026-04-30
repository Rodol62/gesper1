"""
Libro monti: movimenti da riepilogo mensile (Fase B2).

Convenzione (allineata a PRESENZE_MOTORE_FASE_B):
  - quantita negativa = godimento / consumo a carico del monte;
  - chiavi idempotenza per chiusura mese: ``rie-{anno}-{mese}-ferie``, ``rie-{anno}-{mese}-rol``.

``RiepilogoMensilePresenze`` espone ``giorni_ferie_godute`` e ``ore_permessi_goduti`` (ROL).
Non c'è ancora un aggregato per riposi compensativi: nessun movimento ``RIPOSI_COMP`` da riepilogo.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from .models import MovimentoMonte, RiepilogoMensilePresenze, SaldoMonteDipendente

# Ordine colonne UI / export (allineato a TIPO_MONTE_CHOICES).
TIPO_MONTE_ORDINE = ('FERIE_GG', 'ROL_ORE', 'RIPOSI_COMP')


def calcola_saldo_corrente(saldo: SaldoMonteDipendente) -> Decimal:
    """saldo_iniziale + Σ movimenti (quantità con segno)."""
    agg = MovimentoMonte.objects.filter(saldo_monte=saldo).aggregate(s=Sum('quantita'))
    mov = agg['s'] or Decimal('0')
    return (saldo.saldo_iniziale + mov).quantize(Decimal('0.01'))


def data_competenza_riepilogo(anno: int, mese: int) -> date:
    """Data di competenza batch (ultimo giorno del mese)."""
    ultimo = calendar.monthrange(anno, mese)[1]
    return date(anno, mese, ultimo)


def chiave_idempotenza_riepilogo(anno: int, mese: int, suffisso: str) -> str:
    """Es. ``rie-2026-04-ferie`` (max 80 caratteri)."""
    return f'rie-{anno}-{mese:02d}-{suffisso}'


@dataclass
class EsitoMovimentiMonte:
    """Esito per tipo monte gestito da riepilogo (ferie, ROL)."""

    ferie: str = ''
    rol: str = ''

    def as_dict(self) -> dict[str, str]:
        return {'ferie': self.ferie, 'rol': self.rol}


def _saldo_o_create(riepilogo: RiepilogoMensilePresenze, tipo_monte: str) -> SaldoMonteDipendente:
    saldo, _ = SaldoMonteDipendente.objects.get_or_create(
        dipendente=riepilogo.dipendente,
        azienda=riepilogo.azienda,
        tipo_monte=tipo_monte,
        anno_competenza=riepilogo.anno,
        defaults={'saldo_iniziale': Decimal('0')},
    )
    return saldo


def _sincronizza_godimento(
    *,
    saldo: SaldoMonteDipendente,
    data_movimento: date,
    goduto: Decimal,
    unita: str,
    idempotency_key: str,
    riepilogo: RiepilogoMensilePresenze,
    utente,
) -> str:
    """
    Crea o aggiorna un movimento GODIMENTO con quantita = -goduto (goduto ≥ 0).
    Se goduto == 0, elimina il movimento con quella chiave se presente.
    """
    goduto = goduto.quantize(Decimal('0.01'))
    q = (-goduto).quantize(Decimal('0.01'))
    mov = MovimentoMonte.objects.filter(
        saldo_monte=saldo,
        idempotency_key=idempotency_key,
    ).first()

    if goduto == 0:
        if mov:
            mov.delete()
            return 'eliminato'
        return 'invariato_zero'

    note = f'Godimento da riepilogo {riepilogo.anno}/{riepilogo.mese:02d}'
    if mov:
        changed = (
            mov.quantita != q
            or mov.data_movimento != data_movimento
            or mov.riepilogo_mensile_id != riepilogo.id
            or mov.unita != unita
        )
        if changed:
            mov.quantita = q
            mov.data_movimento = data_movimento
            mov.tipo_movimento = 'GODIMENTO'
            mov.unita = unita
            mov.origine = 'RIEPILOGO_MENSILE'
            mov.riepilogo_mensile = riepilogo
            mov.presenza = None
            mov.note = note[:255]
            mov.registrato_da = utente
            mov.save()
            return 'aggiornato'
        return 'invariato'

    MovimentoMonte.objects.create(
        saldo_monte=saldo,
        data_movimento=data_movimento,
        tipo_movimento='GODIMENTO',
        quantita=q,
        unita=unita,
        origine='RIEPILOGO_MENSILE',
        presenza=None,
        riepilogo_mensile=riepilogo,
        idempotency_key=idempotency_key,
        note=note[:255],
        registrato_da=utente,
    )
    return 'creato'


@transaction.atomic
def applica_movimenti_da_riepilogo(
    riepilogo: RiepilogoMensilePresenze,
    *,
    utente=None,
    solo_se_approvato: bool = True,
) -> EsitoMovimentiMonte:
    """
    Registra sul libro monti i godimenti coerenti con un ``RiepilogoMensilePresenze``.

    Idempotente: rieseguire aggiorna o elimina i movimenti legati alle chiavi mensili,
    non duplica righe.

    Args:
        riepilogo: riga riepilogo già allineata ai dati presenze del mese.
        utente: autore registrazione (opzionale).
        solo_se_approvato: se True, accetta solo stato ``approvata`` o ``elaborata``.

    Raises:
        ValueError: se ``solo_se_approvato`` e lo stato del riepilogo non consente la chiusura monti.
    """
    if solo_se_approvato and riepilogo.stato not in ('approvata', 'elaborata'):
        raise ValueError(
            f'Riepilogo {riepilogo.anno}/{riepilogo.mese:02d} per {riepilogo.dipendente} '
            f"è in stato '{riepilogo.get_stato_display()}': "
            'servono approvata o elaborata per applicare i movimenti monti.'
        )

    data_mov = data_competenza_riepilogo(riepilogo.anno, riepilogo.mese)
    anno, mese = riepilogo.anno, riepilogo.mese

    saldo_ferie = _saldo_o_create(riepilogo, 'FERIE_GG')
    saldo_rol = _saldo_o_create(riepilogo, 'ROL_ORE')

    k_ferie = chiave_idempotenza_riepilogo(anno, mese, 'ferie')
    k_rol = chiave_idempotenza_riepilogo(anno, mese, 'rol')

    ferie = _sincronizza_godimento(
        saldo=saldo_ferie,
        data_movimento=data_mov,
        goduto=riepilogo.giorni_ferie_godute,
        unita='GG',
        idempotency_key=k_ferie,
        riepilogo=riepilogo,
        utente=utente,
    )
    rol = _sincronizza_godimento(
        saldo=saldo_rol,
        data_movimento=data_mov,
        goduto=riepilogo.ore_permessi_goduti,
        unita='ORE',
        idempotency_key=k_rol,
        riepilogo=riepilogo,
        utente=utente,
    )
    return EsitoMovimentiMonte(ferie=ferie, rol=rol)


def applica_movimenti_da_tutti_riepiloghi_mese(
    azienda,
    anno: int,
    mese: int,
    *,
    utente=None,
    solo_se_approvato: bool = True,
) -> dict[int, EsitoMovimentiMonte]:
    """
    Applica ``applica_movimenti_da_riepilogo`` a tutti i riepiloghi dell'azienda nel mese.

    I riepiloghi in stato non ammesso (se ``solo_se_approvato``) vengono ignorati senza errore;
    per un elenco completo con errori usare il singolo riepilogo.
    """
    out: dict[int, EsitoMovimentiMonte] = {}
    qs = RiepilogoMensilePresenze.objects.filter(azienda=azienda, anno=anno, mese=mese).select_related(
        'dipendente',
        'azienda',
    )
    for r in qs:
        try:
            out[r.dipendente_id] = applica_movimenti_da_riepilogo(
                r,
                utente=utente,
                solo_se_approvato=solo_se_approvato,
            )
        except ValueError:
            continue
    return out


@transaction.atomic
def elimina_movimenti_monti_da_riepilogo(riepilogo: RiepilogoMensilePresenze) -> int:
    """
    Rimuove i movimenti GODIMENTO generati dalla chiusura monti su quel riepilogo
    (stesso ``riepilogo_mensile`` oppure chiavi idempotenza ferie/ROL del mese).

    Usato quando HR riapre il mese alle modifiche presenze dopo approvazione/elaborazione.
    """
    anno, mese = riepilogo.anno, riepilogo.mese
    keys = (
        chiave_idempotenza_riepilogo(anno, mese, 'ferie'),
        chiave_idempotenza_riepilogo(anno, mese, 'rol'),
    )
    qs = MovimentoMonte.objects.filter(
        Q(riepilogo_mensile_id=riepilogo.pk)
        | Q(
            idempotency_key__in=keys,
            saldo_monte__dipendente_id=riepilogo.dipendente_id,
            saldo_monte__azienda_id=riepilogo.azienda_id,
            saldo_monte__anno_competenza=anno,
        )
    )
    n, _ = qs.delete()
    return int(n)
