"""
Utility per il modulo presenze.

Funzione principale:
    aggrega_presenze_per_motore(dipendente, azienda, anno, mese, utente=None)
        → RiepilogoMensilePresenze (creato/aggiornato, stato='bozza')

Monti (libro giornale ferie / ROL), dopo approvazione riepilogo:
    presenze.monte_ledger.applica_movimenti_da_riepilogo(riepilogo, utente=…)
        → movimenti GODIMENTO idempotenti da ``giorni_ferie_godute`` / ``ore_permessi_goduti``

Elenco dipendenti su pagina Monti / export riconciliazione:
    dipendenti_attivi_con_rapporto_nel_mese(azienda, anno, mese)
        → solo attivi con contratto (Rapporto di lavoro) che interseca il mese di riferimento;
          fine rapporto nulla = indeterminato; esclusi solo rapporti in ``proposta``.

Griglia riepilogo presenze mese + export Excel + aggrega mese:
    dipendenti_per_riepilogo_mese(azienda, anno, mese)
        → candidati (tutti) + attivi con contratto nel mese (come sopra).

Chiusura mese (Fase B4): ``presenze_mese_bloccate()`` → niente create/update/delete ``Presenza`` se il
``RiepilogoMensilePresenze`` del dipendente è approvato o elaborato.

Logica di classificazione delle ore:
    - Soglia giornaliera (ore_std): ore_giornaliere su proposta → parametro CCNL proposta → turno dipendente
      → part-time su rapporto (ore≠40: ore_settimanali ÷ 6, convenzione CCNL turismo/FIPE come simulatore)
      → media da pianificazione orari (fasce mensili/annuali) → tabella CCNL per livello
      → rapporto 40h/5 → default azienda/FIPE.
    - Domenica (weekday=6): ore ≤ std → ore_domenicali; eccedenza → ore_straord_domenica
    - Festività nazionali/aziendali (non domenica): ore ≤ std → ore_festivi; eccedenza → ore_straord_festivo
    - Giorno lavorativo normale (incluso sabato se non festivo):
        * causale ST con tipo_straordinario compilato → bucket corrispondente
        * causale ST senza tipo → dopo 22:00 = notturno, altrimenti diurno
        * causale P/SMART: ore > std → eccedenza in straord_diurno / straord_notturno
    - Causale F  → giorni_ferie_godute (+1 per giorno intero, +0.5 se parziale)
    - Causale PE → ore_permessi_goduti
    - Causale M  → giorni_malattia
    - Causale A  → giorni_assenza_ingiust
    - Causale CIG → giorni_cig
    - Causale R/INF/MAT: contabilizzate ma non producono maggiorazioni

Soglia notturno: 22:00 (art. 1 D.Lgs. 66/2003).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Case, IntegerField, Q, When

from .models import AssegnazioneTurnoDipendente, Presenza, RiepilogoMensilePresenze

# Soglia inizio notturno
_ORA_NOTTURNO = time(22, 0)


def presenze_mese_bloccate(dipendente, azienda, anno: int, mese: int) -> bool:
    """
    True se il mese è «chiuso» per modifiche a Presenza: esiste un
    RiepilogoMensilePresenze in stato approvata o elaborata per quel dipendente/mese.
    """
    r = (
        RiepilogoMensilePresenze.objects.filter(
            dipendente_id=dipendente.pk,
            azienda_id=azienda.pk,
            anno=anno,
            mese=mese,
        )
        .only('stato')
        .first()
    )
    return r is not None and r.stato in ('approvata', 'elaborata')


def dipendenti_attivi_con_rapporto_nel_mese(azienda, anno: int, mese: int):
    """
    Dipendenti **attivi** con almeno un :class:`~rapporto_di_lavoro.models.RapportoDiLavoro`
    il cui periodo **interseca** il calendario ``anno``/``mese``.

    Regole (allineate alla gestione presenze / monti):

    - ``data_inizio_rapporto`` ≤ ultimo giorno del mese;
    - ``data_fine_rapporto`` assente → rapporto inteso **a tempo indeterminato** (solo vincolo a sinistra);
    - se ``data_fine_rapporto`` è valorizzata, deve essere ≥ primo giorno del mese;
    - rapporti in stato **proposta** sono esclusi (non ancora in vigore);
    - stati ``sottoscritto``, ``sospeso``, ``cessato`` ammessi (il periodo effettivo è sulle date).
    """
    from anagrafiche.models import Dipendente
    from rapporto_di_lavoro.models import RapportoDiLavoro

    _, ult = calendar.monthrange(anno, mese)
    d0 = date(anno, mese, 1)
    d1 = date(anno, mese, ult)

    ids = (
        RapportoDiLavoro.objects.filter(azienda=azienda)
        .filter(data_inizio_rapporto__lte=d1)
        .filter(Q(data_fine_rapporto__isnull=True) | Q(data_fine_rapporto__gte=d0))
        .exclude(stato='proposta')
        .values_list('dipendente_id', flat=True)
        .distinct()
    )
    return (
        Dipendente.objects.filter(azienda=azienda, stato='attivo', id__in=ids)
        .order_by('cognome', 'nome')
    )


def dipendenti_per_riepilogo_mese(azienda, anno: int, mese: int):
    """
    Elenco per la griglia «Riepilogo presenze mese», «Salva riepilogo motore (mese)» e export Excel/CSV:

    - **Candidati**: tutti i candidati dell'azienda (portale / pre-assunzione, spesso senza contratto).
    - **Attivi**: solo con rapporto che interseca il mese (stessa logica di
      :func:`dipendenti_attivi_con_rapporto_nel_mese`).
    """
    from django.db.models import Q

    from anagrafiche.models import Dipendente

    ids_attivi = dipendenti_attivi_con_rapporto_nel_mese(azienda, anno, mese).values_list('id', flat=True)
    return (
        Dipendente.objects.filter(azienda=azienda)
        .filter(Q(stato='candidato') | Q(stato='attivo', id__in=ids_attivi))
        .order_by('cognome', 'nome')
    )


# Ore giornaliere standard contrattuale FIPE per dipendente full-time
# (173h mensili / 26 giorni = 6.6538h ≈ 6h39')
# Usiamo un default; se disponibile usiamo il parametro CCNL del dipendente.
_ORE_GIORN_DEFAULT = Decimal('6.6538')


def _ore_in_turno_dopo_soglia(ora_entrata: time | None, ora_uscita: time | None,
                               soglia: time = _ORA_NOTTURNO) -> Decimal:
    """
    Calcola le ore di un turno che cadono dopo la soglia notturna (22:00).
    Restituisce Decimal con 4 cifre decimali.
    """
    if not (ora_entrata and ora_uscita):
        return Decimal('0')
    base = date(2000, 1, 1)
    t_in  = datetime.combine(base, ora_entrata)
    t_out = datetime.combine(base, ora_uscita)
    if t_out <= t_in:
        return Decimal('0')
    soglia_dt = datetime.combine(base, soglia)
    if t_out <= soglia_dt:
        return Decimal('0')
    # parte dopo la soglia
    inizio_nott = max(t_in, soglia_dt)
    ore_nott = (t_out - inizio_nott).total_seconds() / 3600
    return Decimal(str(round(ore_nott, 4)))


def _ore_turno(ora_entrata: time | None, ora_uscita: time | None) -> Decimal:
    """Ore di un singolo turno (Decimal 4 cifre)."""
    if not (ora_entrata and ora_uscita):
        return Decimal('0')
    base = date(2000, 1, 1)
    t_in  = datetime.combine(base, ora_entrata)
    t_out = datetime.combine(base, ora_uscita)
    diff  = (t_out - t_in).total_seconds() / 3600
    if diff <= 0:
        return Decimal('0')
    return Decimal(str(round(diff, 4)))


def _parametro_ccnl_ore_giornaliere(cp) -> Optional[Decimal]:
    """
    Da una riga ParametroCCNLTurismo (tabella CCNL): usa i campi strutturati in ordine di preferenza.
    """
    if not cp:
        return None
    if getattr(cp, 'ore_giornaliere', None) and cp.ore_giornaliere > 0:
        return Decimal(str(cp.ore_giornaliere)).quantize(Decimal('0.0001'))
    if getattr(cp, 'ore_settimanali', None) and cp.ore_settimanali > 0:
        return (Decimal(str(cp.ore_settimanali)) / Decimal('5')).quantize(Decimal('0.0001'))
    if getattr(cp, 'ore_mensili', None) and cp.ore_mensili > 0:
        return (Decimal(str(cp.ore_mensili)) / Decimal('26')).quantize(Decimal('0.0001'))
    return None


def _trova_parametro_ccnl_per_livello(livello: str, data_rif: date, azienda) -> Optional[object]:
    """Riga ParametroCCNLTurismo coerente con livello, CCNL azienda se noto, decorrenza."""
    if not livello or not str(livello).strip():
        return None
    from rapporto_di_lavoro.models import ParametroCCNLTurismo

    livello = livello.strip()
    base = (
        ParametroCCNLTurismo.objects.filter(
            attivo=True,
            decorrenza_validita_da__lte=data_rif,
        )
        .filter(Q(livello=livello) | Q(livello__iexact=livello))
    )
    qs = base
    if azienda is not None and getattr(azienda, 'ccnl_predefinito_id', None):
        sigla = getattr(azienda.ccnl_predefinito, 'sigla', None)
        if sigla:
            qs = base.filter(ccnl__icontains=sigla)
    cp = qs.order_by('-decorrenza_validita_da').first()
    if cp is None and qs is not base:
        cp = base.order_by('-decorrenza_validita_da').first()
    return cp


def _ore_da_fascia_pianificazione(fascia) -> float:
    """Somma ore lavorate previste in un giorno da una fascia (mensile o annuale)."""
    if not fascia or getattr(fascia, 'chiuso', False):
        return 0.0
    tot_min = 0
    for a, b in (
        (getattr(fascia, 'ora_apertura_mattina', None), getattr(fascia, 'ora_chiusura_mattina', None)),
        (getattr(fascia, 'ora_apertura_pomeriggio', None), getattr(fascia, 'ora_chiusura_pomeriggio', None)),
    ):
        if a and b:
            tot_min += max(
                0,
                (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute),
            )
    return round(max(0, tot_min) / 60.0, 4)


def ore_media_settimana_da_pianificazione(azienda, anno: int, mese: int) -> Optional[Decimal]:
    """
    Media ore giornaliere dalla pianificazione orari (ConfigurazioneOrarioMensile o annuale):
    somma ore sui giorni aperti della settimana / numero di tali giorni con ore > 0.
    """
    from .models import (
        ConfigurazioneOrarioAnnuale,
        ConfigurazioneOrarioMensile,
        FasciaAperturaMensile,
        FasciaAperturaSettimanale,
    )

    def _media_da_fasce_mensili(cfg) -> Optional[Decimal]:
        riposo = set(cfg.giorni_riposo_settimanale or [])
        tot = Decimal('0')
        n = 0
        for wd in range(7):
            if wd in riposo:
                continue
            f = (
                FasciaAperturaMensile.objects.filter(
                    configurazione=cfg,
                    giorno_settimana=wd,
                ).first()
            )
            if not f:
                continue
            h = Decimal(str(_ore_da_fascia_pianificazione(f)))
            if h > 0:
                tot += h
                n += 1
        if n == 0:
            return None
        return (tot / Decimal(str(n))).quantize(Decimal('0.0001'))

    cfg_m = ConfigurazioneOrarioMensile.objects.filter(
        azienda=azienda,
        anno=anno,
        mese=mese,
    ).first()
    if cfg_m:
        og = _media_da_fasce_mensili(cfg_m)
        if og is not None:
            return og

    cfg_a = ConfigurazioneOrarioAnnuale.objects.filter(
        azienda=azienda,
        anno=anno,
    ).first()
    if not cfg_a:
        return None
    riposo = set(cfg_a.giorni_riposo_settimanale or [])
    tot = Decimal('0')
    n = 0
    for wd in range(7):
        if wd in riposo:
            continue
        f = (
            FasciaAperturaSettimanale.objects.filter(
                configurazione=cfg_a,
                giorno_settimana=wd,
            ).first()
        )
        if not f:
            continue
        h = Decimal(str(_ore_da_fascia_pianificazione(f)))
        if h > 0:
            tot += h
            n += 1
    if n == 0:
        return None
    return (tot / Decimal(str(n))).quantize(Decimal('0.0001'))


def _ore_std_da_turno_dipendente(dipendente, data_rif: date) -> Optional[Decimal]:
    """Assegnazione turno con date vigenti: durata giornaliera = ore standard per quel dipendente."""
    from django.db.models import Q

    from .models import AssegnazioneTurnoDipendente

    a = (
        AssegnazioneTurnoDipendente.objects.filter(
            dipendente=dipendente,
            attivo=True,
            data_inizio__lte=data_rif,
        )
        .filter(Q(data_fine__isnull=True) | Q(data_fine__gte=data_rif))
        .select_related('turno')
        .order_by('-data_inizio')
        .first()
    )
    if not a or not a.turno:
        return None
    t = a.turno
    if not t.ora_inizio or not t.ora_fine:
        return None
    base = date(2000, 1, 1)
    t_in = datetime.combine(base, t.ora_inizio)
    t_out = datetime.combine(base, t.ora_fine)
    diff = (t_out - t_in).total_seconds() / 3600.0
    if diff <= 0:
        return None
    return Decimal(str(round(diff, 4))).quantize(Decimal('0.0001'))


def _ore_std_da_proposta_rapporto(rdl) -> Optional[Decimal]:
    """
    Proposta collegata al rapporto (OneToOne): prima l'orario giornaliero esplicito sulla proposta
    (stesso valore usato in documenti/simulatore, es. part-time 36h → 6h con 6 gg), poi il parametro CCNL.
    """
    proposta = getattr(rdl, 'proposta_origine', None)
    if not proposta:
        return None
    og_prop = getattr(proposta, 'ore_giornaliere', None)
    if og_prop is not None and Decimal(str(og_prop)) > 0:
        return Decimal(str(og_prop)).quantize(Decimal('0.0001'))
    try:
        cp = proposta.parametro_ccnl_risolto
    except Exception:
        cp = None
    return _parametro_ccnl_ore_giornaliere(cp) if cp else None


def _rapporto_vigente_per_ore(dipendente, azienda, data_rif: date):
    """Rapporto di lavoro da usare per le ore contrattuali (sottoscritto prima, poi altri stati)."""
    from rapporto_di_lavoro.models import RapportoDiLavoro

    return (
        RapportoDiLavoro.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            data_inizio_rapporto__lte=data_rif,
        )
        .filter(Q(data_fine_rapporto__isnull=True) | Q(data_fine_rapporto__gte=data_rif))
        .filter(stato__in=['sottoscritto', 'cessato', 'sospeso', 'proposta'])
        .select_related(
            'tipo_contratto',
            'proposta_origine',
            'proposta_origine__parametro_ccnl',
        )
        .annotate(
            stato_prio=Case(
                When(stato='sottoscritto', then=0),
                When(stato='cessato', then=1),
                When(stato='sospeso', then=2),
                When(stato='proposta', then=3),
                default=4,
                output_field=IntegerField(),
            )
        )
        .order_by('stato_prio', '-data_inizio_rapporto')
        .first()
    )


def ore_std_giornaliere_contratto(dipendente, azienda, anno: int, mese: int) -> Decimal:
    """
    Ore giornaliere per soglie straordinario / riepilogo.

    Priorità:
      1. ParametroCCNLTurismo da proposta collegata al Rapporto
      2. Turno assegnato al dipendente (orario specifico)
      3. Rapporto part-time: ore_settimanali ≠ 40 → /6 (allineato a CCNL turismo / simulatore paga; non ÷26 busta)
      4. Media settimanale da pianificazione orari (fasce mensili o annuali azienda)
      5. Tabella CCNL per livello
      6. Rapporto full-time: ore_settimanali / 5 (tip. 40 → 8h lun–ven)
      7. Azienda ore_settimanali_standard / 5, poi ore_giornaliere_standard
      8. Default FIPE ~6.65h
    """
    _, ultimo_g = calendar.monthrange(anno, mese)
    data_rif = date(anno, mese, min(15, ultimo_g))
    fulltime_ref = Decimal('40')

    try:
        rdl = _rapporto_vigente_per_ore(dipendente, azienda, data_rif)

        if rdl:
            og_prop = _ore_std_da_proposta_rapporto(rdl)
            if og_prop is not None:
                return og_prop

        og_turno = _ore_std_da_turno_dipendente(dipendente, data_rif)
        if og_turno is not None:
            return og_turno

        if rdl and rdl.ore_settimanali is not None:
            os = Decimal(str(rdl.ore_settimanali))
            if os > 0 and os != fulltime_ref:
                # 6 gg/sett. come schema divisori / simulatore (non la paga oraria ÷26)
                return (os / Decimal('6')).quantize(Decimal('0.0001'))

        if azienda is not None:
            og_piano = ore_media_settimana_da_pianificazione(azienda, anno, mese)
            if og_piano is not None:
                return og_piano

        livello = None
        if rdl and getattr(rdl, 'livello_ccnl', None):
            livello = (rdl.livello_ccnl or '').strip() or None
        if not livello and getattr(dipendente, 'livello', None):
            livello = (dipendente.livello or '').strip() or None

        cp = _trova_parametro_ccnl_per_livello(livello or '', data_rif, azienda)
        og_ccnl = _parametro_ccnl_ore_giornaliere(cp)
        if og_ccnl is not None:
            return og_ccnl

        if rdl and rdl.ore_settimanali and rdl.ore_settimanali > 0:
            return (Decimal(str(rdl.ore_settimanali)) / Decimal('5')).quantize(Decimal('0.0001'))

        if azienda is not None:
            os = getattr(azienda, 'ore_settimanali_standard', None)
            if os is not None and os > 0:
                return (Decimal(str(os)) / Decimal('5')).quantize(Decimal('0.0001'))

        if azienda is not None:
            og = getattr(azienda, 'ore_giornaliere_standard', None)
            if og is not None and og > 0:
                return Decimal(str(og)).quantize(Decimal('0.0001'))
    except Exception:
        pass
    return _ORE_GIORN_DEFAULT


def tipo_eccesso_vs_contratto(ore_giorno: float, ore_std: Decimal, giorno_ctx: dict) -> Optional[str]:
    """
    Se le ore timbrate superano la soglia contrattuale del giorno, indica come il motore le classifica:
      'lav' → straordinario (diurno/notturno); 'dom' / 'fest' → parte a maggiorazione + eccedenza in straord festivo.
    """
    if giorno_ctx.get('fuori_rapporto'):
        return None
    p = giorno_ctx.get('presenza')
    if not p:
        return None
    if p.causale in ('F', 'PE', 'M', 'A', 'CIG', 'INF', 'MAT', 'R'):
        return None
    og = Decimal(str(round(ore_giorno, 4)))
    std = ore_std if isinstance(ore_std, Decimal) else Decimal(str(ore_std))
    if og <= std:
        return None
    if giorno_ctx.get('is_domenica'):
        return 'dom'
    if giorno_ctx.get('is_festivo'):
        return 'fest'
    return 'lav'


def ore_eccesso_vs_contratto(ore_giorno: float, ore_std: Decimal, giorno_ctx: dict) -> Optional[Decimal]:
    """
    Ore giornaliere eccedenti rispetto alla soglia contrattuale (solo se tipo_eccesso è valorizzato).
    """
    if not tipo_eccesso_vs_contratto(ore_giorno, ore_std, giorno_ctx):
        return None
    og = Decimal(str(round(ore_giorno, 4)))
    std = ore_std if isinstance(ore_std, Decimal) else Decimal(str(ore_std))
    return (og - std).quantize(Decimal('0.01'))


def _aggregazione_mensile_core(presenze, festivita_set: set, ore_std: Decimal) -> dict:
    """
    Stessa logica di classificazione ore di :func:`aggrega_presenze_per_motore`,
    senza persistenza. ``presenze`` è un iterable ordinato per data.
    """
    ore_ordinarie = Decimal('0')
    ore_domenicali = Decimal('0')
    ore_festivi = Decimal('0')
    ore_straord_diurno = Decimal('0')
    ore_straord_notturno = Decimal('0')
    ore_straord_festivo = Decimal('0')
    ore_straord_domenica = Decimal('0')
    ore_straord_nott_fest = Decimal('0')
    giorni_ferie_godute = Decimal('0')
    ore_permessi_goduti = Decimal('0')
    giorni_malattia = 0
    giorni_assenza_ingiust = 0
    giorni_cig = 0

    for p in presenze:
        causale = p.causale
        giorno = p.data
        wd = giorno.weekday()
        is_dom = wd == 6
        is_fest = giorno in festivita_set

        if causale == 'F':
            ore_lav = Decimal(str(p.ore_lavorate()))
            if ore_lav == 0:
                giorni_ferie_godute += Decimal('1')
            else:
                giorni_ferie_godute += (ore_lav / ore_std).quantize(Decimal('0.01'))
            continue

        if causale == 'PE':
            ore_permessi_goduti += Decimal(str(p.ore_lavorate()))
            continue

        if causale == 'M':
            giorni_malattia += 1
            continue

        if causale == 'A':
            giorni_assenza_ingiust += 1
            continue

        if causale == 'CIG':
            giorni_cig += 1
            continue

        if causale in ('INF', 'MAT', 'R'):
            continue

        turni = [
            (p.ora_entrata, p.ora_uscita),
            (p.ora_entrata2, p.ora_uscita2),
            (p.ora_entrata3, p.ora_uscita3),
        ]
        ore_lav_totali = Decimal(str(p.ore_lavorate()))

        if causale == 'ST' and p.ore_straordinario and p.tipo_straordinario:
            ore_st = Decimal(str(p.ore_straordinario))
            tipo = p.tipo_straordinario
            if tipo == 'diurno':
                ore_straord_diurno += ore_st
            elif tipo == 'notturno':
                ore_straord_notturno += ore_st
            elif tipo == 'festivo':
                if is_dom:
                    ore_straord_domenica += ore_st
                else:
                    ore_straord_festivo += ore_st
            elif tipo == 'nott_fest':
                ore_straord_nott_fest += ore_st
            ore_ordinarie += max(Decimal('0'), ore_lav_totali - ore_st)
            continue

        if is_dom:
            ore_contr = min(ore_lav_totali, ore_std)
            ore_eccesso = max(Decimal('0'), ore_lav_totali - ore_std)
            ore_domenicali += ore_contr
            ore_straord_domenica += ore_eccesso
            continue

        if is_fest:
            ore_contr = min(ore_lav_totali, ore_std)
            ore_eccesso = max(Decimal('0'), ore_lav_totali - ore_std)
            ore_festivi += ore_contr
            ore_straord_festivo += ore_eccesso
            continue

        ore_nott_tot = Decimal('0')
        for ent, usc in turni:
            ore_nott_tot += _ore_in_turno_dopo_soglia(ent, usc)

        ore_extra = max(Decimal('0'), ore_lav_totali - ore_std)
        if ore_extra:
            ore_nott_extra = min(ore_nott_tot, ore_extra)
            ore_diur_extra = ore_extra - ore_nott_extra
            ore_straord_notturno += ore_nott_extra
            ore_straord_diurno += ore_diur_extra

        ore_ordin = ore_lav_totali - ore_extra
        ore_ordinarie += ore_ordin

    return {
        'ore_ordinarie': ore_ordinarie,
        'ore_domenicali': ore_domenicali,
        'ore_festivi': ore_festivi,
        'ore_straord_diurno': ore_straord_diurno,
        'ore_straord_notturno': ore_straord_notturno,
        'ore_straord_festivo': ore_straord_festivo,
        'ore_straord_domenica': ore_straord_domenica,
        'ore_straord_nott_fest': ore_straord_nott_fest,
        'giorni_ferie_godute': giorni_ferie_godute,
        'ore_permessi_goduti': ore_permessi_goduti,
        'giorni_malattia': giorni_malattia,
        'giorni_assenza_ingiust': giorni_assenza_ingiust,
        'giorni_cig': giorni_cig,
    }


def _ore_mensili_ccnl_riferimento(dipendente, azienda, anno: int, mese: int) -> Decimal:
    """
    Ore mensili contrattuali da regola/parametro CCNL, **prorate al tipo di contratto**
    (coefficiente ore), come in ``rapporto_di_lavoro.normativa_ccnl``.
    """
    from rapporto_di_lavoro.normativa_ccnl import parametri_normativi_contrattuali

    _, ultimo_g = calendar.monthrange(anno, mese)
    data_rif = date(anno, mese, min(15, ultimo_g))
    return parametri_normativi_contrattuali(dipendente, azienda, data_rif)['ore_mensili']


def _giorni_lavorativi_teorici_mese(
    dipendente,
    azienda,
    anno: int,
    mese: int,
    *,
    cfg_e_fasce: tuple | None = None,
) -> int:
    """
    Numero di giorni del mese in cui la generazione teorica creerebbe causale **P**
    (stessa logica di ``_genera_presenze_teoriche_mese_azienda``).
    """
    from presenze.views import (
        _fasce_teoriche_da_config,
        _get_config_orario_mese,
        _periodo_rapporto_dipendente_per_mese,
    )

    if cfg_e_fasce is not None:
        cfg, fasce_map = cfg_e_fasce
    else:
        cfg, fasce_map = _get_config_orario_mese(azienda, anno, mese, create=True)
    if not cfg or not getattr(cfg, 'genera_presenze_teoriche', False):
        return 0

    riposi = set(cfg.giorni_riposo_settimanale or [])
    _, ultimo = calendar.monthrange(anno, mese)
    m_start = date(anno, mese, 1)
    m_end = date(anno, mese, ultimo)

    data_inizio, data_fine = _periodo_rapporto_dipendente_per_mese(dipendente, azienda, anno, mese)

    assegnazioni_turno = list(
        AssegnazioneTurnoDipendente.objects.filter(
            dipendente=dipendente,
            attivo=True,
            data_inizio__lte=m_end,
        )
        .filter(Q(data_fine__isnull=True) | Q(data_fine__gte=m_start))
        .select_related('turno')
        .order_by('-data_inizio')
    )

    n = 0
    cur = m_start
    while cur <= m_end:
        if data_inizio and cur < data_inizio:
            cur += timedelta(days=1)
            continue
        if data_fine and cur > data_fine:
            cur += timedelta(days=1)
            continue

        wd = cur.weekday()
        fascia = fasce_map.get(wd)
        chiuso = wd in riposi or (fascia.chiuso if fascia else False)
        causale = 'R' if chiuso else 'P'

        if causale == 'P':
            n += 1

        cur += timedelta(days=1)

    return n


def riepilogo_ore_mese_sidebar(dipendente, azienda, anno: int, mese: int) -> dict:
    """
    Riepilogo mensile per calendario: ore teoriche (pianificazione o note TEORICA_AUTO),
    totali effettivi e ripartizione come il motore (feriali / domeniche / festivi + straordinari).
    """
    from presenze.views import _get_config_orario_mese

    from rapporto_di_lavoro.normativa_ccnl import parametri_normativi_contrattuali
    from rapporto_di_lavoro.utils_calendario import get_festivita_mese

    _, ult_ref = calendar.monthrange(anno, mese)
    data_rif = date(anno, mese, min(15, ult_ref))
    pn_norm = parametri_normativi_contrattuali(dipendente, azienda, data_rif)
    ore_mens_ref = pn_norm['ore_mensili']
    ferie_maturate_mese = (pn_norm['ferie_annue_giorni'] / Decimal('12')).quantize(Decimal('0.01'))
    permessi_maturati_mese = (pn_norm['permessi_annui_ore'] / Decimal('12')).quantize(Decimal('0.01'))

    cfg_mese, fasce_map_mese = _get_config_orario_mese(azienda, anno, mese, create=True)
    pianificazione_attiva = bool(cfg_mese and getattr(cfg_mese, 'genera_presenze_teoriche', False))

    festivita_set = {f['data'] for f in get_festivita_mese(anno, mese, azienda)}
    ore_std = ore_std_giornaliere_contratto(dipendente, azienda, anno, mese)
    presenze = list(
        Presenza.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            data__year=anno,
            data__month=mese,
        ).order_by('data')
    )
    acc = _aggregazione_mensile_core(presenze, festivita_set, ore_std)

    ore_teoriche_da_note = Decimal('0')
    ha_note_teorica_auto = False
    for p in presenze:
        if p.causale == 'P' and 'TEORICA_AUTO' in (p.note or ''):
            ha_note_teorica_auto = True
            ore_teoriche_da_note += Decimal(str(p.ore_lavorate()))

    giorni_lav_teorici = 0
    if pianificazione_attiva:
        giorni_lav_teorici = _giorni_lavorativi_teorici_mese(
            dipendente, azienda, anno, mese, cfg_e_fasce=(cfg_mese, fasce_map_mese)
        )
        # Mese tipo: ore_mensili CCNL ÷ 26 gg lav. medi × gg lavorativi effettivi nel mese (come P da pianificazione)
        ore_teoriche = (ore_mens_ref / Decimal('26')) * Decimal(giorni_lav_teorici)
    else:
        ore_teoriche = ore_teoriche_da_note

    def q(d: Decimal) -> Decimal:
        return d.quantize(Decimal('0.01'))

    tot_eff = (
        acc['ore_ordinarie']
        + acc['ore_domenicali']
        + acc['ore_festivi']
        + acc['ore_straord_diurno']
        + acc['ore_straord_notturno']
        + acc['ore_straord_festivo']
        + acc['ore_straord_domenica']
        + acc['ore_straord_nott_fest']
    )

    straord_feriali = acc['ore_straord_diurno'] + acc['ore_straord_notturno']

    ore_t_q = q(ore_teoriche)
    mostra_avviso_teoriche = ore_t_q == Decimal('0') and not pianificazione_attiva

    return {
        'ore_teoriche': ore_t_q,
        'teoriche_da_pianificazione': pianificazione_attiva,
        'ore_mensili_contratto': q(ore_mens_ref),
        'coefficiente_ore_contratto': pn_norm['coefficiente_ore'],
        'ore_settimanali_contratto': pn_norm['ore_settimanali'],
        'ha_regola_normativa_ccnl': pn_norm['ha_regola_normativa'],
        'giorni_lavorativi_teorici': giorni_lav_teorici,
        'ferie_maturate_mese_gg': ferie_maturate_mese,
        'permessi_maturati_mese_ore': permessi_maturati_mese,
        'ha_note_teorica_auto': ha_note_teorica_auto,
        'mostra_avviso_teoriche': mostra_avviso_teoriche,
        'ore_effettive_totali': q(tot_eff),
        'ore_entro_contratto_feriali': q(acc['ore_ordinarie']),
        'ore_entro_contratto_domeniche': q(acc['ore_domenicali']),
        'ore_entro_contratto_festivi': q(acc['ore_festivi']),
        'straord_feriali': q(straord_feriali),
        'straord_diurno': q(acc['ore_straord_diurno']),
        'straord_notturno': q(acc['ore_straord_notturno']),
        'straord_domeniche': q(acc['ore_straord_domenica']),
        'straord_festivi': q(acc['ore_straord_festivo']),
        'straord_nott_fest': q(acc['ore_straord_nott_fest']),
    }


def aggrega_presenze_per_motore(
    dipendente,
    azienda,
    anno: int,
    mese: int,
    utente=None,
) -> RiepilogoMensilePresenze:
    """
    Legge tutte le Presenze del dipendente nel mese e produce (o aggiorna)
    un RiepilogoMensilePresenze con i campi pronti per il motore paga.

    Il riepilogo viene sempre ricreato in stato 'bozza' (sovrascrive una
    bozza esistente; non sovrascrive stati approvata/elaborata).

    Returns:
        RiepilogoMensilePresenze istanza salvata.

    Raises:
        ValueError: se il riepilogo è già in stato approvata/elaborata
                    e non si vuole sovrascrivere.
    """
    from rapporto_di_lavoro.utils_calendario import get_festivita_mese

    # Controllo stato esistente
    esistente = RiepilogoMensilePresenze.objects.filter(
        dipendente=dipendente, anno=anno, mese=mese
    ).first()
    if esistente and esistente.stato in ('approvata', 'elaborata'):
        raise ValueError(
            f"Riepilogo {anno}/{mese:02d} per {dipendente} è già in stato "
            f"'{esistente.get_stato_display()}': non può essere ricalcolato automaticamente."
        )

    festivita_set: set[date] = {
        f['data'] for f in get_festivita_mese(anno, mese, azienda)
    }

    ore_std = ore_std_giornaliere_contratto(dipendente, azienda, anno, mese)

    presenze = Presenza.objects.filter(
        dipendente=dipendente,
        azienda=azienda,
        data__year=anno,
        data__month=mese,
    ).order_by('data')

    acc = _aggregazione_mensile_core(presenze, festivita_set, ore_std)

    # ── Salvataggio ───────────────────────────────────────────────────────────
    dati = dict(
        azienda=azienda,
        stato='bozza',
        ore_ordinarie=acc['ore_ordinarie'].quantize(Decimal('0.01')),
        ore_domenicali=acc['ore_domenicali'].quantize(Decimal('0.01')),
        ore_festivi=acc['ore_festivi'].quantize(Decimal('0.01')),
        ore_straord_diurno=acc['ore_straord_diurno'].quantize(Decimal('0.01')),
        ore_straord_notturno=acc['ore_straord_notturno'].quantize(Decimal('0.01')),
        ore_straord_festivo=acc['ore_straord_festivo'].quantize(Decimal('0.01')),
        ore_straord_domenica=acc['ore_straord_domenica'].quantize(Decimal('0.01')),
        ore_straord_nott_fest=acc['ore_straord_nott_fest'].quantize(Decimal('0.01')),
        giorni_ferie_godute=acc['giorni_ferie_godute'].quantize(Decimal('0.01')),
        ore_permessi_goduti=acc['ore_permessi_goduti'].quantize(Decimal('0.01')),
        giorni_malattia=acc['giorni_malattia'],
        giorni_assenza_ingiust=acc['giorni_assenza_ingiust'],
        giorni_cig=acc['giorni_cig'],
        generata_da=utente,
        note='',
    )

    if esistente:
        for k, v in dati.items():
            setattr(esistente, k, v)
        esistente.save()
        return esistente

    riepilogo = RiepilogoMensilePresenze(
        dipendente=dipendente,
        anno=anno,
        mese=mese,
        **dati,
    )
    riepilogo.save()
    return riepilogo


def saldi_monti_calendario(dipendente, azienda, anno: int, mese: int | None = None) -> dict:
    """
    Saldo corrente monti (libro giornale) per anno di competenza + conteggi grezzi da calendario
    (giorni con causale F / PE nell'anno solare) per confronto rapido in HR.

    Se ``mese`` è indicato (1–12), aggiunge anche la maturazione teorica ferie/ROL
    (normativa CCNL + rapporto vigente + tabella assenze non maturative).
    """
    from .models import Presenza, SaldoMonteDipendente
    from .monte_ledger import calcola_saldo_corrente

    out = {}
    for tipo in ('FERIE_GG', 'ROL_ORE', 'RIPOSI_COMP'):
        saldo = SaldoMonteDipendente.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            anno_competenza=anno,
            tipo_monte=tipo,
        ).first()
        out[tipo] = calcola_saldo_corrente(saldo) if saldo else Decimal('0')

    ferie_godute = Presenza.objects.filter(
        dipendente=dipendente, data__year=anno, causale='F'
    ).count()
    giorni_pe = Presenza.objects.filter(
        dipendente=dipendente, data__year=anno, causale='PE'
    ).count()

    result = {
        'residuo_ferie_gg': out['FERIE_GG'],
        'residuo_rol_ore': out['ROL_ORE'],
        'residuo_riposi_gg': out['RIPOSI_COMP'],
        'giorni_ferie_goduti_anno': ferie_godute,
        'giorni_permesso_anno': giorni_pe,
    }
    if mese is not None and 1 <= mese <= 12:
        from .maturazione_griglia_utils import calcolo_maturazione_griglia_mese

        mat = calcolo_maturazione_griglia_mese(dipendente, azienda, anno, mese)
        result.update(mat)
    return result


def _streak_assenza_da_date_ordinate(dates_sorted: list) -> dict:
    """Massima sequenza consecutiva su lista di date ordinate (causale A)."""
    if not dates_sorted:
        return {
            'max_giorni_consecutivi': 0,
            'periodo_max_da': None,
            'periodo_max_a': None,
            'supera_soglia_15': False,
            'totale_giorni_a': 0,
        }
    n = len(dates_sorted)
    best_len = 1
    best_start = dates_sorted[0]
    best_end = dates_sorted[0]
    cur_len = 1
    for i in range(1, n):
        if dates_sorted[i] == dates_sorted[i - 1] + timedelta(days=1):
            cur_len += 1
        else:
            cur_len = 1
        if cur_len > best_len:
            best_len = cur_len
            best_end = dates_sorted[i]
            best_start = dates_sorted[i] - timedelta(days=cur_len - 1)
    return {
        'max_giorni_consecutivi': best_len,
        'periodo_max_da': best_start,
        'periodo_max_a': best_end,
        'supera_soglia_15': best_len > 15,
        'totale_giorni_a': n,
    }


def streak_assenza_ingiustificata(dipendente, anno: Optional[int] = None) -> dict:
    """
    Massima sequenza di giorni **consecutivi** con causale «Assenza ingiustificata» (A).

    Con ``anno`` impostato (anno del calendario in uso), i campi principali si riferiscono
    **solo a quel anno solare**; sono aggiunti anche i totali sullo **storico completo**.
    Con ``anno`` None, il comportamento coincide con lo storico unico.
    """
    from .models import Presenza

    dates_all = list(
        Presenza.objects.filter(dipendente=dipendente, causale='A')
        .order_by('data')
        .values_list('data', flat=True)
    )
    storico = _streak_assenza_da_date_ordinate(dates_all)

    if anno is None:
        return storico

    dates_anno = [d for d in dates_all if d.year == anno]
    nel_anno = _streak_assenza_da_date_ordinate(dates_anno)

    return {
        'anno_riferimento': anno,
        'max_giorni_consecutivi': nel_anno['max_giorni_consecutivi'],
        'periodo_max_da': nel_anno['periodo_max_da'],
        'periodo_max_a': nel_anno['periodo_max_a'],
        'supera_soglia_15': nel_anno['supera_soglia_15'],
        'totale_giorni_a': nel_anno['totale_giorni_a'],
        'max_giorni_consecutivi_storico': storico['max_giorni_consecutivi'],
        'periodo_max_da_storico': storico['periodo_max_da'],
        'periodo_max_a_storico': storico['periodo_max_a'],
        'supera_soglia_15_storico': storico['supera_soglia_15'],
        'totale_giorni_a_storico': storico['totale_giorni_a'],
    }
