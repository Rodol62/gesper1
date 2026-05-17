"""
Motore paga mensile — calcolo busta paga completo.
Condiviso tra Simulatore Paga e Simulazione annua.

Riferimento architetturale (motori canonici / cosa non duplicare):
:mod:`rapporto_di_lavoro.motori_canonici`.
"""
from __future__ import annotations
import calendar as _cal_mod
from datetime import date
from decimal import Decimal

from django.db.models import Q

from .motori_canonici import MOTORE_PAGA_CANONICO_ORIGINE

Q2 = Decimal('0.01')
Q4 = Decimal('0.0001')
Q6 = Decimal('0.000001')


def _dimensione_numerica_da_azienda(azienda) -> int:
    """Coerente con ``views._stima_dimensione_azienda``: sintesi dipendenti per fascia contributiva."""
    if not azienda:
        return 1
    tipologia = getattr(azienda, 'tipologia_dimensionale', None)
    if tipologia == 'piccola':
        return 10
    if tipologia == 'media':
        return 30
    if tipologia == 'grande':
        return 80
    try:
        return max(1, int(azienda.dipendenti.count()))
    except Exception:
        return 1


def _categorie_contributive_in_ordine(azienda) -> list[str]:
    """Stesso ordine di ``_carica_regole_contributive_da_db`` (piccola/media/grande ristorazione)."""
    d = _dimensione_numerica_da_azienda(azienda)
    if d <= 15:
        prim = 'piccola_ristorazione'
    elif d <= 50:
        prim = 'media_ristorazione'
    else:
        prim = 'grande_ristorazione'
    out: list[str] = []
    seen: set[str] = set()
    for c in (prim, 'piccola_ristorazione', 'media_ristorazione', 'grande_ristorazione'):
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def risolvi_parametro_contributi_ccnl(
    *,
    ccnl_obj,
    anno: int,
    tipo_contributo: str,
    azienda,
    mese: int,
):
    """
    Una riga ``ParametroContributi`` per tipo (inps/inail), fascia dimensionale e validità nel mese.

    Prima del filtro per ``categoria`` e ``data_validita_*``, con più righe per anno il motore
    usava ``.first()`` senza categoria: in produzione poteva applicare aliquote INPS/INAIL della
    fascia sbagliata (record arbitrario).
    """
    if not ccnl_obj:
        return None
    from .models import ParametroContributi

    ultimo_gg = _cal_mod.monthrange(int(anno), int(mese))[1]
    data_rif = date(int(anno), int(mese), ultimo_gg)

    for cat in _categorie_contributive_in_ordine(azienda):
        row = (
            ParametroContributi.objects.filter(
                ccnl=ccnl_obj,
                anno=int(anno),
                tipo_contributo=tipo_contributo,
                categoria=cat,
                attivo=True,
                data_validita_da__lte=data_rif,
            )
            .filter(Q(data_validita_a__isnull=True) | Q(data_validita_a__gte=data_rif))
            .order_by('-data_validita_da')
            .first()
        )
        if row:
            return row
    return None


def ccnl_fipe_edr_assorbito_in_contingenza(parametro_ccnl, ccnl_obj=None) -> bool:
    """
    CCNL FIPE (Pubblici esercizi / ristorazione, decorrenze 2024+): l'EDR storico è assorbito
    nella contingenza tabellare — non va reintrodotto come voce distinta in busta né nel rateo €/h EDR.

    ``ParametroCCNLTurismo.ccnl`` può essere vuoto o non contenere «FIPE»; in quel caso si considera
    anche ``ccnl_obj`` (modello ``CCNL``) se la sigla è FIPE.

    I parametri **Turismo Confcommercio** restano esclusi: lì l'EDR può restare voce distinta in tabella.
    """
    p = (getattr(parametro_ccnl, 'ccnl', None) or '')
    pu = p.upper()
    if 'CONFCOMMERCIO' in pu:
        return False
    if 'FIPE' in pu:
        return True
    if 'PUBBLICI ESERCIZI' in pu:
        return True
    if ccnl_obj is not None and 'FIPE' in (getattr(ccnl_obj, 'sigla', None) or '').upper():
        return True
    return False


def anno_efficace_parametro_ratei(ccnl_obj, anno_riferimento: int, parametro_ccnl) -> int:
    """
    Anno da usare su `ParametroRatei` quando il rapporto ha data inizio «vecchia»
    (es. legacy 2020) ma le righe ratei in anagrafica sono solo per anni recenti
    o allineate alla decorrenza della tabella CCNL (es. 2027).

    Ordine: stesso anno del periodo → anno decorrenza tabella → ultimo anno a DB.
    """
    if not ccnl_obj:
        return int(anno_riferimento)
    from .models import ParametroRatei

    # Una sola query: gli anni disponibili; la scelta resta identica a prima (exists + max).
    annos = set(
        ParametroRatei.objects.filter(ccnl=ccnl_obj, attivo=True).values_list('anno', flat=True)
    )
    if not annos:
        return int(anno_riferimento)
    ar = int(anno_riferimento)
    if ar in annos:
        return ar
    dec = getattr(parametro_ccnl, 'decorrenza_validita_da', None)
    if dec:
        ay = int(dec.year)
        if ay in annos:
            return ay
    return int(max(annos))


def ricava_parametri_proposta_contrattuale(
    *,
    parametro_ccnl,
    tipo_contratto=None,
    anno: int | None = None,
    mese: int | None = None,
    azienda=None,
    data_inizio_rapporto=None,
    data_fine_rapporto=None,
    superminimo: Decimal = Decimal('0'),
    indennita_turno: Decimal = Decimal('0'),
    scatto_anzianita: Decimal = Decimal('0'),
    indennita_extra: Decimal = Decimal('0'),
    ccnl_obj=None,
    num_familiari_a_carico: int = 0,
) -> dict:
    """
    Ricava i parametri economici/normativi della proposta usando il motore canonico.

    Restituisce un payload pronto per essere salvato su `PropostaAssunzione`
    o riusato per generare il futuro `RapportoDiLavoro` definitivo.

    Usa sempre la mensilità tabellare piena (coefficiente part-time sì, prorata
    giorni di ingresso/uscita sul mese no), coerente con documento di proposta.
    """
    from django.utils import timezone
    from .models import CCNL, ParametroRatei, RegolaNormativaCCNL

    oggi = timezone.localdate()
    anno = int(anno or oggi.year)
    mese = int(mese or oggi.month)

    if ccnl_obj is None:
        ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()

    anno_ratei = anno_efficace_parametro_ratei(ccnl_obj, anno, parametro_ccnl)

    risultato = calcola_busta_paga_mese(
        parametro_ccnl=parametro_ccnl,
        tipo_contratto=tipo_contratto,
        anno=anno,
        mese=mese,
        azienda=azienda,
        data_inizio_rapporto=data_inizio_rapporto,
        data_fine_rapporto=data_fine_rapporto,
        divisore_str=str(round(float(parametro_ccnl.ore_mensili))) if parametro_ccnl.ore_mensili else '173',
        superminimo=superminimo,
        indennita_turno=indennita_turno,
        scatto_anzianita=scatto_anzianita,
        indennita_extra=indennita_extra,
        ccnl_obj=ccnl_obj,
        num_familiari_a_carico=num_familiari_a_carico,
        # Proposta/contratto: mensilità tabellare piena (no prorata ingresso/uscita sul mese di riferimento)
        mensilita_contrattuale_piena=True,
        anno_parametro_ratei=anno_ratei,
        # Anteprima economica: ratei in imponibile solo se flag espliciti su proposta/contratto (default no).
        rateo_13_mensile_in_imponibile=False,
        rateo_14_mensile_in_imponibile=False,
    )

    tredicesima = False
    quattordicesima = False
    if ccnl_obj:
        tipi_rateo = set(
            ParametroRatei.objects.filter(
                ccnl=ccnl_obj,
                anno=anno_ratei,
                attivo=True,
                tipo_rateo__in=('tredicesima', 'quattordicesima'),
            ).values_list('tipo_rateo', flat=True)
        )
        tredicesima = 'tredicesima' in tipi_rateo
        quattordicesima = 'quattordicesima' in tipi_rateo

    giorni_ferie_annuali = 26
    giorni_permesso_annuali = 3
    regola = RegolaNormativaCCNL.objects.filter(
        ccnl=parametro_ccnl.ccnl,
        versione=parametro_ccnl.versione,
        livello=parametro_ccnl.livello,
        attivo=True,
    ).order_by('-decorrenza_validita_da').first()
    if regola:
        giorni_ferie_annuali = int(regola.ferie_annue_giorni or 26)
        giorni_permesso_annuali = int((regola.permessi_annui_ore or 24) // 8)

    ore_sett = Decimal(str(risultato['ore_settimanali_contr'] or 0)).quantize(Q2)
    ore_mens = Decimal(str(risultato['ore_mensili'] or 0)).quantize(Q2)
    ore_giorn = Decimal(str(risultato['ore_giornaliere'] or 0)).quantize(Q2)

    return {
        'livello_ccnl': parametro_ccnl.livello,
        'qualifica': parametro_ccnl.qualifica,
        'stipendio_lordo_mensile': risultato['lordo_mensile'],
        'paga_base_mensile': risultato['paga_base'],
        'contingenza_mensile': risultato['contingenza'],
        'edr_mensile': risultato['edr'],
        'superminimo_mensile': superminimo.quantize(Q2),
        'indennita_mensile': risultato['indennita'],
        'ore_settimanali': ore_sett,
        'ore_mensili': ore_mens,
        'ore_giornaliere': ore_giorn,
        'decorrenza_validita_da': parametro_ccnl.decorrenza_validita_da,
        'decorrenza_validita_a': parametro_ccnl.decorrenza_validita_a,
        'scatto_periodicita_mesi': parametro_ccnl.scatto_periodicita_mesi,
        'scatto_importo': parametro_ccnl.scatto_importo,
        'numero_scatti_massimi': parametro_ccnl.numero_scatti_massimi,
        'straordinario_diurno_maggiorazione': parametro_ccnl.straordinario_diurno_maggiorazione,
        'straordinario_notturno_maggiorazione': parametro_ccnl.straordinario_notturno_maggiorazione,
        'straordinario_festivo_maggiorazione': parametro_ccnl.straordinario_festivo_maggiorazione,
        'riposi_compensativi_regola': parametro_ccnl.riposi_compensativi_regola,
        'tredicesima': tredicesima,
        'quattordicesima': quattordicesima,
        'giorni_ferie_annuali': giorni_ferie_annuali,
        'giorni_permesso_annuali': giorni_permesso_annuali,
        'motore_origine': MOTORE_PAGA_CANONICO_ORIGINE,
        'calendario_origine': risultato.get('calendario_motore_id', 'calendario_lavorativo_aziendale_v1'),
    }


def calcola_busta_paga_mese(
    *,
    parametro_ccnl,            # ParametroCCNLTurismo instance
    tipo_contratto=None,       # TipoContratto instance | None
    anno: int,
    mese: int,
    azienda=None,              # Azienda instance | None (for calendar)
    data_inizio_rapporto=None, # date | None
    data_fine_rapporto=None,   # date | None
    divisore_str: str = '26',  # '26', '172', '173.33'
    superminimo: Decimal = Decimal('0'),
    indennita_turno: Decimal = Decimal('0'),
    scatto_anzianita: Decimal = Decimal('0'),
    indennita_extra: Decimal = Decimal('0'),
    ore_straord_diurno: Decimal = Decimal('0'),
    ore_straord_notturno: Decimal = Decimal('0'),
    ore_straord_festivo: Decimal = Decimal('0'),
    ore_straord_domenica: Decimal = Decimal('0'),
    ore_straord_nott_fest: Decimal = Decimal('0'),
    ore_ordinarie_retribuite: Decimal = Decimal('0'),
    ore_domenicali: Decimal = Decimal('0'),
    ore_festivi: Decimal = Decimal('0'),
    auto_ore_domenicali_da_calendario: bool = True,
    modalita_ore_effettive: bool = False,
    domenicale_compenso_completo: bool = True,
    festivo_compenso_completo: bool = False,
    fiscale_modalita_cedolino: bool = False,
    l207_percentuale_imponibile: Decimal | None = None,
    ti_l207_non_cumulabili: bool = False,
    l207_come_detrazione_irpef: bool = False,
    l207_anche_come_credito_netto: bool = False,
    competenze_extra_non_imponibili: Decimal = Decimal('0'),
    trattenute_extra_mese: Decimal = Decimal('0'),
    giorni_assenza_ingiust: Decimal = Decimal('0'),
    giorni_ferie_godute: Decimal = Decimal('0'),
    ore_permessi_goduti: Decimal = Decimal('0'),
    ccnl_obj=None,             # CCNL model instance | None (for DB lookups)
    num_familiari_a_carico: int = 0,
    regione_residenza: str = 'Sicilia',  # usato per calcolo addizionale regionale
    comune_residenza: str | None = None,   # addizionale comunale (default Palermo se None)
    provincia_residenza: str | None = None,  # sigla provincia (default PA)
    mensilita_contrattuale_piena: bool = False,
    anno_parametro_ratei: int | None = None,
    contratto_esclude_tredicesima: bool = False,
    contratto_esclude_quattordicesima: bool = False,
    rateo_13_mensile_in_imponibile: bool = False,
    rateo_14_mensile_in_imponibile: bool = False,
    forza_add_reg_m: Decimal | None = None,
    forza_add_com_m: Decimal | None = None,
    forza_paga_oraria: Decimal | None = None,
) -> dict:
    """
    Calcola la busta paga mensile completa per un dipendente.
    Implementazione canonica — usata da Simulatore Paga e Simulazione annua.

    L.207/2024 (art.1 c.4): fuori dal lordo contributivo; credito IRPEF (detrazione / netto)
    secondo i flag fiscali — non concorre all'imponibile INPS.

    Quote mensili 13ª/14ª (``rat13_m`` / ``rat14_m``): sempre calcolate come riferimento/accantonamento.
    Concorrono alla base INPS/IRPEF/INAIL del mese **solo** se ``rateo_*_mensile_in_imponibile`` è True
    (quota 1/12 effettivamente in busta). Se False, restano fuori dall'imponibile mensile ma compaiono
    comunque nei totali ratei / costo differito. Se il contratto esclude la mensilità (``contratto_esclude_*``),
    il coefficiente e quindi il rateo lordo sono zero.

    **Domenicale lavorato:** default ``domenicale_compenso_completo=True`` → importo = ore × ROEL × (1 + magg. %).
    Con ``False`` → solo maggiorazione: ore × ROEL × magg. % (base tabellare già nel lordo mensile).

    **Festivo lavorato:** con ``festivo_compenso_completo=True`` applica ore × ROEL × (1 + magg. %).
    Con ``False`` applica solo la maggiorazione.

    **Superminimo:** ``superminimo`` è la mensilità di riferimento **a tempo pieno** (misura fissa);
    in busta e nella €/h mostrata per part-time si applica il ``coefficiente_ore`` del tipo contratto
    (es. €/h effettiva = (Sm_ref × coeff) / 172).

    Con ore ordinarie retribuite > 0, ``oraria_ordinario_da_competenza`` = ``imp_ordinario_ore`` ÷ ore
    (rapporto dopo arrotondamento della competenza ordinaria; confrontabile alla BASE riga 8001).

    ``forza_paga_oraria`` (€/h): se valorizzato, sostituisce la paga oraria usata per competenze
    ordinarie, straordinari e maggiorazioni (es. allineamento al cedolino ufficiale / ROF in busta).

    ``forza_add_reg_m`` / ``forza_add_com_m``: se valorizzati, sostituiscono le trattenute mensili
    addizionali IRPEF; in quel caso vengono anche sottratte da ``netto_totale`` (come in busta erogata).

    Returns full result dict with: voci, lordo, netto, contributi, ratei, f24, costo azienda.
    """
    from .models import (
        ParametroCCNLTurismo,
        ParametroRatei,
        ParametroMaggiorazione,
        VoceRetributiva,
    )
    from .utils_calcoli import (
        calcola_irpef_lorda, calcola_detrazioni,
        calcola_trattamento_integrativo, calcola_bonus_l207_2024,
        calcola_addizionale_regionale_sicilia, calcola_addizionale_comunale_stima,
    )
    from .utils_calendario import (
        get_giorni_lavorativi_mese,
        build_griglia_mese,
        get_calendario_motore_id,
        count_giorni_ordinari_calendario,
    )
    from .parametro_ccnl_voci_retributive import parametro_ccnl_motore_con_voci_retributive

    if isinstance(parametro_ccnl, ParametroCCNLTurismo):
        parametro_ccnl = parametro_ccnl_motore_con_voci_retributive(parametro_ccnl, anno)
    cp = parametro_ccnl
    coeff = Decimal(str(tipo_contratto.coefficiente_ore or 1)) if tipo_contratto else Decimal('1')
    giorni_nel_mese = _cal_mod.monthrange(anno, mese)[1]
    anno_pr = int(anno_parametro_ratei) if anno_parametro_ratei is not None else int(anno)

    # ── Calendario ────────────────────────────────────────────────────────────
    calendario_motore_id = get_calendario_motore_id()
    cal_data = get_giorni_lavorativi_mese(azienda, anno, mese)
    _gls_sett = int(getattr(tipo_contratto, 'giorni_lavorativi_settimana', 6) or 6) if tipo_contratto else 6
    cal_giorni_ordinari = count_giorni_ordinari_calendario(
        anno, mese, cal_data, giorni_lavorativi_settimana=_gls_sett,
    )
    cal_griglia = build_griglia_mese(anno, mese, azienda)
    # I festivi NON sono in _non_lav: per FIPE sono giorni lavorativi (con maggiorazione)
    _non_lav = (
        set(cal_data['dates_chiusure_sett']) |
        set(cal_data['dates_chiusure_extra'])
    )
    # Tutte le domeniche del mese, indipendentemente dal giorno di chiusura aziendale
    # (per i ristoranti FIPE la domenica è giorno LAVORATIVO, non è in dates_chiusure_sett)
    cal_domeniche = [
        date(anno, mese, g)
        for g in range(1, giorni_nel_mese + 1)
        if date(anno, mese, g).weekday() == 6
    ]
    # Domeniche effettivamente lavorative (esclude chiusura settimanale/extra)
    cal_domeniche_lav = [d for d in cal_domeniche if d not in _non_lav]
    # Festività nazionali/aziendali che NON cadono di domenica
    # (se una festività cade di domenica viene già contata in domenicali)
    _festivi_set = set(cal_data['dates_festivita'])
    cal_festivi_lav = [d for d in cal_data['dates_festivita'] if d.weekday() != 6]
    # Domeniche che coincidono con una festività (es. Pasqua 2026 = 5 apr = domenica)
    cal_dom_fest_n = sum(1 for d in cal_domeniche if d in _festivi_set)

    # ── Pro-rata ──────────────────────────────────────────────────────────────
    # Proposte / clausole contrattuali: importi tabellari mensili interi (× part-time),
    # senza riduzione per pochi giorni di lavoro nel mese di assunzione/cessazione.
    if mensilita_contrattuale_piena:
        gg_lav = cal_data['giorni_lavorativi']
        frazione = Decimal('1')
    elif data_inizio_rapporto and data_inizio_rapporto.year == anno and data_inizio_rapporto.month == mese:
        gg_cal = (date(anno, mese, giorni_nel_mese) - data_inizio_rapporto).days + 1
        if gg_cal >= 15:
            frazione = Decimal('1')
            gg_lav = cal_data['giorni_lavorativi']
        else:
            gg_lav = sum(1 for g in range(data_inizio_rapporto.day, giorni_nel_mese + 1)
                         if date(anno, mese, g) not in _non_lav)
            tot = cal_data['giorni_lavorativi'] or 1
            frazione = (Decimal(str(gg_lav)) / Decimal(str(tot))).quantize(Q6)
    elif data_fine_rapporto and data_fine_rapporto.year == anno and data_fine_rapporto.month == mese:
        gg_cal = data_fine_rapporto.day
        if gg_cal >= 15:
            frazione = Decimal('1')
            gg_lav = cal_data['giorni_lavorativi']
        else:
            gg_lav = sum(1 for g in range(1, data_fine_rapporto.day + 1)
                         if date(anno, mese, g) not in _non_lav)
            tot = cal_data['giorni_lavorativi'] or 1
            frazione = (Decimal(str(gg_lav)) / Decimal(str(tot))).quantize(Q6)
    else:
        gg_lav = cal_data['giorni_lavorativi']
        frazione = Decimal('1')

    ore_sett_r = (cp.ore_settimanali * coeff).quantize(Q2)
    # Ore mensili contrattuale CCNL (172 FIPE full-time) pro-rata part-time
    # Fallback: 4.3 → 40 × 4.3 = 172 ore/mese (divisore contrattuale FIPE piccoli esercizi)
    _ore_mens_ccnl = Decimal(str(cp.ore_mensili)) if cp.ore_mensili else (ore_sett_r * Decimal('4.3')).quantize(Q2)
    ore_mensili = (_ore_mens_ccnl * coeff).quantize(Q2)
    # Ore «giorno» per maggiorazioni / ore calendariali (allineamento prassi FIPE / foglio Excel INPS):
    # h settimanali contrattuali ÷ 6 (es. PT 90%: 36÷6 = 6), non ore_mensili÷26 (quella resta solo riferimento mensile).
    ore_media_settimanale_su_6gg = (ore_sett_r / Decimal('6')).quantize(Q4) if ore_sett_r else Decimal('0')
    ore_giorn = ore_media_settimanale_su_6gg

    # ── Divisore ──────────────────────────────────────────────────────────────
    div_raw = str(divisore_str).replace(',', '.')
    divisore_dec = Decimal(div_raw) if div_raw else Decimal('26')

    # ── Voci base CCNL (pro-ratate per part-time e periodo) ───────────────────
    def _v(val): return (val * coeff * frazione).quantize(Q2)

    _paga_explicit = Decimal(str(cp.paga_base_mensile or 0))
    _paga_tbl = _paga_explicit
    if _paga_tbl <= 0:
        _paga_tbl = Decimal(str(getattr(cp, 'minimo_tabellare', None) or 0))
    # Numeratore FT per /172 (foglio INPS «minimo livello»): di norma ``minimo_tabellare`` se valorizzato.
    # Se il minimo è **maggiore** della paga base dichiarata, in anagrafica è spesso un importo aggregato
    # (paga + indennità / totale tabellare) copiato nel campo sbagliato: per la €/h «paga base» si usa allora
    # ``paga_base_mensile`` (evita ROEL gonfia es. 6,5182 invece di 5,9389 €/h).
    _min_ft = Decimal(str(getattr(cp, 'minimo_tabellare', None) or 0))
    if _min_ft > 0:
        if _paga_explicit > 0 and _min_ft > _paga_explicit:
            _paga_ft_div = _paga_explicit
        else:
            _paga_ft_div = _min_ft
    else:
        _paga_ft_div = _paga_tbl
    _rof_usa_minimo_tabellare = bool(
        _min_ft > 0 and divisore_dec > Decimal('30') and not (_paga_explicit > 0 and _min_ft > _paga_explicit),
    )
    _c_m = Decimal(str(cp.contingenza_mensile or 0))
    _e_m = Decimal(str(cp.edr_mensile or 0))
    # FIPE (2024+): EDR assorbito in contingenza — non voce distinta in busta né €/h tab. EDR (anche se valorizzato in DB legacy).
    if ccnl_fipe_edr_assorbito_in_contingenza(cp, ccnl_obj):
        _e_m = Decimal('0')
    _i_m = Decimal(str(cp.indennita_mensile or 0))

    # Numeratore FT «solo paga base» per /172 e per busta: se paga+ind sono entrambe in tabella ma la paga
    # importata è (paga+indennità) e l’indennità è anche in colonna, il rapporto paga/cont supera il tipico ~2;
    # togliendo l’indennità FT si rientra nel rapporto tabellare (~1,95 per molti livelli FIPE).
    _paga_ft_tab_solo = _paga_ft_div
    if (
        ccnl_fipe_edr_assorbito_in_contingenza(cp, ccnl_obj)
        and _i_m > 0
        and _c_m > 0
        and _paga_ft_div > _i_m
    ):
        r_bruta = (_paga_ft_div / _c_m).quantize(Q4)
        r_netta = ((_paga_ft_div - _i_m) / _c_m).quantize(Q4)
        if r_bruta > Decimal('2.02') and Decimal('1.82') <= r_netta <= Decimal('2.18'):
            _paga_ft_tab_solo = (_paga_ft_div - _i_m).quantize(Q2)

    paga_base       = _v(_paga_ft_tab_solo)
    contingenza     = _v(cp.contingenza_mensile)
    edr             = _v(_e_m)
    indennita       = _v(cp.indennita_mensile)
    superminimo_r       = (superminimo       * coeff * frazione).quantize(Q2)
    indennita_turno_r   = (indennita_turno   * coeff * frazione).quantize(Q2)
    # Scatto esplicito (contratto o colonna parametro CCNL).
    _scatto_mens_ft = Decimal(str(scatto_anzianita or 0))
    if _scatto_mens_ft <= 0:
        _scatto_mens_ft = Decimal(str(getattr(cp, 'scatto_importo', None) or 0))

    scatto_r            = (_scatto_mens_ft * coeff * frazione).quantize(Q2)
    indennita_extra_r   = (indennita_extra   * coeff * frazione).quantize(Q2)

    # Elementi distinti tabellari (parametro CCNL): in anagrafica come €/ora; mensilità convenzionale = €/h × ore contrattuali.
    _eds_ora = Decimal(str(getattr(cp, 'elemento_distinto_sanita', None) or 0))
    _edb_ora = Decimal(str(getattr(cp, 'elemento_distinto_bilateralita', None) or 0))
    _eds_mens_ft = Decimal('0')
    _edb_mens_ft = Decimal('0')
    eds_r = Decimal('0')
    edb_r = Decimal('0')
    if divisore_dec > Decimal('30'):
        _eds_mens_ft = (_eds_ora * divisore_dec).quantize(Q2)
        _edb_mens_ft = (_edb_ora * divisore_dec).quantize(Q2)
        eds_r = _v(_eds_mens_ft)
        edb_r = _v(_edb_mens_ft)

    lordo_base  = (paga_base + contingenza + edr + indennita
                   + superminimo_r + indennita_turno_r + scatto_r + indennita_extra_r
                   + eds_r + edb_r).quantize(Q2)

    lordo_pieno = (
        (_paga_ft_tab_solo + _c_m + _e_m + _i_m) * coeff
    ).quantize(Q2)

    # Superminimo: ``superminimo`` è la mensilità di riferimento a tempo pieno (misura fissa contrattuale);
    # in busta concorre per ``superminimo * coeff * frazione``. La €/h in rubrica (part-time) è
    # (Sm_ref × coeff × pro-rata) ÷ divisore, es. 4,26 = (7,10 €/h equivalente FT) × 60 %.
    _sm_arg = Decimal(str(superminimo or 0))
    _sm_ref_tab = _sm_arg.quantize(Q6)
    _somma_tabellare_ft = (_paga_ft_tab_solo + _c_m + _e_m + _i_m + _scatto_mens_ft).quantize(Q2)
    if divisore_dec > Decimal('30'):
        _somma_tabellare_ft = (
            _somma_tabellare_ft + _sm_ref_tab + _eds_mens_ft + _edb_mens_ft
        ).quantize(Q2)
    lordo_tabellare_ft_equiv = (_somma_tabellare_ft * frazione).quantize(Q2)
    tabellare_gap_ft = Decimal('0')

    # Retribuzione oraria di fatto = Σ ((voce tabellare FT × frazione mese) ÷ divisore), come Excel (es. 1021,49/172).
    h_oraria_paga_base = Decimal('0')
    h_oraria_contingenza = Decimal('0')
    h_oraria_edr = Decimal('0')
    h_oraria_indennita = Decimal('0')
    h_oraria_scatto = Decimal('0')
    h_oraria_superminimo = Decimal('0')
    h_oraria_el_dis_san = Decimal('0')
    h_oraria_el_dis_bil = Decimal('0')
    retribuzione_oraria_di_fatto = Decimal('0')
    if divisore_dec > Decimal('30'):
        div = divisore_dec
        fr = frazione
        h_oraria_paga_base = ((_paga_ft_tab_solo * fr) / div).quantize(Q4)
        h_oraria_contingenza = ((_c_m * fr) / div).quantize(Q4)
        h_oraria_edr = ((_e_m * fr) / div).quantize(Q4)
        h_oraria_indennita = ((_i_m * fr) / div).quantize(Q4)
        h_oraria_scatto = ((_scatto_mens_ft * fr) / div).quantize(Q4) if _scatto_mens_ft > 0 else Decimal('0')
        # Retribuzione oraria di fatto = Σ (voce tabellare FT × pro-rata giorni ÷ ore contrattuali 172 o 173,33).
        # Include EDR finché distinto (dal 2024 in FIPE è azzerato perché assorbito in contingenza), superminimo,
        # EL.DIS.SAN e EL.DIS.BIL (da €/h tabellare × ore contrattuali ÷ ore = €/h, con pro-rata sul mese).
        _sm_coeff = (coeff if coeff > 0 else Decimal('1'))
        h_oraria_superminimo = (
            ((_sm_ref_tab * _sm_coeff * fr) / div).quantize(Q4) if _sm_ref_tab > 0 else Decimal('0')
        )
        h_oraria_el_dis_san = ((_eds_mens_ft * fr) / div).quantize(Q4) if _eds_mens_ft > 0 else Decimal('0')
        h_oraria_el_dis_bil = ((_edb_mens_ft * fr) / div).quantize(Q4) if _edb_mens_ft > 0 else Decimal('0')
        retribuzione_oraria_di_fatto = (
            h_oraria_paga_base
            + h_oraria_contingenza
            + h_oraria_edr
            + h_oraria_scatto
            + h_oraria_superminimo
            + h_oraria_el_dis_san
            + h_oraria_el_dis_bil
        ).quantize(Q4)

    # ── Paga oraria / giornaliera ─────────────────────────────────────────────
    if modalita_ore_effettive:
        if divisore_dec > Decimal('30'):
            # Divisore orario (172/173,33): ROF = Σ (voce tabellare FT × pro-rata ÷ divisore), come foglio INPS.
            # Con ore da presenze non si sostituisce la ROF con lordo_base ÷ ore_mensili (part-time), altrimenti
            # la colonna €/h tab. non coincide con FT÷divisore (es. 1021,49÷172 = 5,9389).
            paga_oraria = retribuzione_oraria_di_fatto
            paga_giornaliera = (lordo_base / Decimal('26')).quantize(Q4)
        else:
            # Divisore 26 o simile: media da cedolino su ore mensili contrattuali
            paga_oraria = (lordo_base / ore_mensili).quantize(Q4) if ore_mensili else Decimal('0')
            paga_giornaliera = (paga_oraria * ore_giorn).quantize(Q4)
    elif divisore_dec > Decimal('30'):
        # Divisore orario: paga oraria = retribuzione oraria di fatto (somma voci/172); non totale_tabellare unico.
        paga_oraria      = retribuzione_oraria_di_fatto
        paga_giornaliera = (lordo_base / Decimal('26')).quantize(Q4)
    else:
        # Divisore giornaliero (es. 26): paga giornaliera = lordo/26, oraria = lordo/ore_mensili
        paga_giornaliera = (lordo_pieno / divisore_dec).quantize(Q4)
        paga_oraria      = (lordo_pieno / ore_mensili).quantize(Q4) if ore_mensili else Decimal('0')

    # Con divisore giornaliero: «retribuzione oraria di fatto» coincide con l’orario medio da cedolino.
    if divisore_dec <= Decimal('30'):
        retribuzione_oraria_di_fatto = paga_oraria

    _fp = Decimal(str(forza_paga_oraria)) if forza_paga_oraria is not None else Decimal('0')
    if _fp > 0:
        paga_oraria = _fp.quantize(Q4)
        retribuzione_oraria_di_fatto = paga_oraria
        if divisore_dec <= Decimal('30'):
            paga_giornaliera = (paga_oraria * ore_giorn).quantize(Q4) if ore_giorn else Decimal('0')

    # ── Auto ore domenicali da calendario (se non fornite o impostate a zero) ─
    ore_domenicali_auto = False
    if auto_ore_domenicali_da_calendario and Decimal(str(ore_domenicali or 0)) == Decimal('0'):
        ore_domenicali = (Decimal(str(len(cal_domeniche_lav))) * ore_giorn).quantize(Q2)
        ore_domenicali_auto = True

    # ── Maggiorazioni da CCNL e da DB ─────────────────────────────────────────
    # Formule richieste (simulazioni / riconciliazione cedoli):
    # - Straord. feriale:     paga × (1 + magg_straord_feriale) × ore
    # - Lavoro festivo (no str.): paga × magg_lavoro_festivo × ore
    # - Straord. festivo:     paga × (1 + magg_straord_festivo) × ore
    # - Lavoro domenicale (no str.): paga × magg_lavoro_domenicale × ore
    # - Straord. domenicale:  paga × (1 + magg) × ore
    #   con magg = percentuale del tipo «straordinario_domenicale» in ParametroMaggiorazione
    #   (es. 30,00 → 0,30). Se manca a DB: fallback CCNL straord. festivo (tabella livello).
    magg_diur_ccnl = Decimal(str(cp.straordinario_diurno_maggiorazione   or 15)) / 100
    magg_nott_ccnl = Decimal(str(cp.straordinario_notturno_maggiorazione or 30)) / 100
    magg_fest_ccnl = Decimal(str(cp.straordinario_festivo_maggiorazione  or 30)) / 100

    def _magg_db_frac(tipo_magg):
        """Percentuale maggiorazione da ParametroMaggiorazione (come frazione, es. 0.15)."""
        if not ccnl_obj:
            return None
        last_day = date(anno, mese, _cal_mod.monthrange(anno, mese)[1])
        pm = (ParametroMaggiorazione.objects
              .filter(ccnl=ccnl_obj, tipo_maggiorazione=tipo_magg, attivo=True,
                      data_validita_da__lte=last_day)
              .order_by('-data_validita_da')
              .first())
        if pm:
            return (pm.percentuale / 100).quantize(Q4)
        return None

    def _magg_pct(tipo, default_pct):
        v = _magg_db_frac(tipo)
        if v is not None:
            return v
        return Decimal(str(default_pct))

    magg_straord_fer = _magg_db_frac('straordinario_feriale') or magg_diur_ccnl
    magg_straord_nott = _magg_db_frac('straordinario_notturno') or magg_nott_ccnl
    magg_straord_fest = _magg_db_frac('straordinario_festivo') or magg_fest_ccnl
    magg_nf = _magg_db_frac('straordinario_notturno_festivo')
    if magg_nf is None:
        magg_nf = (magg_straord_nott + magg_straord_fest).quantize(Q4)

    magg_dom_p  = _magg_pct('lavoro_domenicale', '0.15')
    magg_fest_p = _magg_pct('lavoro_festivo',    '0.20')

    magg_straord_dom = _magg_db_frac('straordinario_domenicale') or magg_straord_fest

    # ── Straordinari ──────────────────────────────────────────────────────────
    imp_sd  = (ore_straord_diurno    * paga_oraria * (1 + magg_straord_fer)).quantize(Q2)
    imp_sn  = (ore_straord_notturno  * paga_oraria * (1 + magg_straord_nott)).quantize(Q2)
    imp_sf  = (ore_straord_festivo   * paga_oraria * (1 + magg_straord_fest)).quantize(Q2)
    imp_snf = (ore_straord_nott_fest * paga_oraria * (1 + magg_nf)).quantize(Q2)
    imp_sdom = (ore_straord_domenica * paga_oraria * (1 + magg_straord_dom)).quantize(Q2)
    tot_straord = (imp_sd + imp_sn + imp_sf + imp_snf + imp_sdom).quantize(Q2)

    # ── Domenicali / festivi lavorati ────────────────────────────────────────
    # Default simulatore / prassi richiesta: **compenso domenicale completo** = ore × ROEL × (1 + magg.%).
    # Con ``domenicale_compenso_completo=False``: solo maggiorazione (ore × ROEL × magg.%), base già nel lordo tabellare.
    if domenicale_compenso_completo:
        imp_dom_magg = (ore_domenicali * paga_oraria * (1 + magg_dom_p)).quantize(Q2)
    else:
        imp_dom_magg = (ore_domenicali * paga_oraria * magg_dom_p).quantize(Q2)
    if festivo_compenso_completo:
        imp_fest_magg = (ore_festivi * paga_oraria * (1 + magg_fest_p)).quantize(Q2)
    else:
        imp_fest_magg = (ore_festivi * paga_oraria * magg_fest_p).quantize(Q2)
    tot_dom_fest  = (imp_dom_magg + imp_fest_magg).quantize(Q2)

    # ── Assenze ingiustificate ────────────────────────────────────────────────
    # In modalita' ore effettive le assenze sono gia' riflesse nelle ore ordinarie
    # retribuite del mese: evitare doppia decurtazione sul lordo.
    if modalita_ore_effettive and ore_ordinarie_retribuite > 0:
        decurt_assenze = Decimal('0')
    else:
        decurt_assenze = (giorni_assenza_ingiust * paga_giornaliera).quantize(Q2)

    # ── Lordo competenze mensili (rubrica; 13ª/14ª ratei si sommano sotto per base INPS)
    imp_ordinario_ore = (ore_ordinarie_retribuite * paga_oraria).quantize(Q2)
    # Rapporto competenze ÷ ore (dopo arrotondamento importo riga): allineabile alla colonna BASE riga 8001
    # su molti cedolini (Teamsystem / ecc.), spesso diverso dalla sola ROF tabellare Σ÷divisore.
    oraria_ordinario_da_competenza = None
    _ore_ord_dec = Decimal(str(ore_ordinarie_retribuite or 0))
    if _ore_ord_dec > 0:
        oraria_ordinario_da_competenza = (imp_ordinario_ore / _ore_ord_dec).quantize(Q4)
    if modalita_ore_effettive and ore_ordinarie_retribuite > 0:
        # Base su ore effettive: ROF × ore (componenti tabellari incluse nella retrib. oraria di fatto) + voci
        # fuori dalla somma €/h (turno, extra) + maggiorazioni. Superminimo / EL.DIS.* non si sommano di nuovo
        # (già nella €/h); con 0 ore ordinarie restano le sole voci mensili tabellari + accessori.
        if divisore_dec > Decimal('30'):
            # Indennità CCNL tabellare (``indennita``): è in ``lordo_base`` ma **non** nella ROF €/h
            # (``retribuzione_oraria_di_fatto``); in busta resta voce mensile imponibile. Va sommata al lordo
            # competenze quando l’ordinario è solo «ore × ROF», altrimenti l’imponibile INPS/IRPEF resta sotto
            # al cedolino reale.
            _ind_tab_m = indennita.quantize(Q2) if indennita and indennita > 0 else Decimal('0')
            lordo_mensile = (
                imp_ordinario_ore
                + _ind_tab_m
                + indennita_turno_r
                + indennita_extra_r
                + tot_straord
                + tot_dom_fest
            ).quantize(Q2)
        else:
            lordo_mensile = (imp_ordinario_ore + tot_straord + tot_dom_fest).quantize(Q2)
    else:
        lordo_mensile = (lordo_base + tot_straord + tot_dom_fest - decurt_assenze).quantize(Q2)

    # ── Coefficienti ratei (serve prima dei contributi: 13ª/14ª nella base INPS) ─
    c_tfr = Decimal('0.0691')
    c_13 = (Decimal('1') / Decimal('12')).quantize(Q6)
    c_14 = Decimal('0')
    c_fer = Decimal('0.1154')
    if ccnl_obj:
        for tipo_r, transform, attr in [
            ('tfr',             lambda r: r.coefficiente / 100, 'c_tfr'),
            ('tredicesima',     lambda r: r.coefficiente / 12,  'c_13'),
            ('quattordicesima', lambda r: r.coefficiente / 12,  'c_14'),
            ('ferie',           lambda r: r.coefficiente / 100, 'c_fer'),
        ]:
            tipi_lookup = [tipo_r]
            if tipo_r == 'ferie':
                tipi_lookup.append('indennita_ferie')

            pr = ParametroRatei.objects.filter(
                ccnl=ccnl_obj, anno=anno_pr, tipo_rateo__in=tipi_lookup, attivo=True
            ).order_by('tipo_rateo').first()
            if pr:
                val = transform(pr).quantize(Q6)
                if   attr == 'c_tfr': c_tfr = val
                elif attr == 'c_13':  c_13  = val
                elif attr == 'c_14':  c_14  = val
                elif attr == 'c_fer': c_fer = val

    if contratto_esclude_tredicesima:
        c_13 = Decimal('0')
    if contratto_esclude_quattordicesima:
        c_14 = Decimal('0')

    # 13ª/14ª su base contrattuale fissa (pro-rata già incorporato in lordo_base)
    rat13_m = (lordo_base * c_13).quantize(Q2)
    rat14_m = (lordo_base * c_14).quantize(Q2)
    rat13_in_imponibile_m = (rat13_m if rateo_13_mensile_in_imponibile else Decimal('0')).quantize(Q2)
    rat14_in_imponibile_m = (rat14_m if rateo_14_mensile_in_imponibile else Decimal('0')).quantize(Q2)
    # Base previdenziale/IRPEF mensile: competenze + eventuali quote 13ª/14ª erogate in busta
    lordo_imponibile_inps_m = (lordo_mensile + rat13_in_imponibile_m + rat14_in_imponibile_m).quantize(Q2)

    # ── Contributi da DB ──────────────────────────────────────────────────────
    inps_dip_p = Decimal('0.0936')
    inps_az_p  = Decimal('0.2931')
    inail_p    = Decimal('0.0074')
    if ccnl_obj:
        pc = risolvi_parametro_contributi_ccnl(
            ccnl_obj=ccnl_obj, anno=anno, tipo_contributo='inps', azienda=azienda, mese=mese,
        )
        if pc:
            inps_dip_p = (pc.aliquota_dipendente / 100).quantize(Q4)
            inps_az_p  = (pc.aliquota_azienda    / 100).quantize(Q4)
        pc2 = risolvi_parametro_contributi_ccnl(
            ccnl_obj=ccnl_obj, anno=anno, tipo_contributo='inail', azienda=azienda, mese=mese,
        )
        if pc2:
            inail_p = (pc2.aliquota_azienda / 100).quantize(Q4)

    inps_dip = (lordo_imponibile_inps_m * inps_dip_p).quantize(Q2)
    inps_az  = (lordo_imponibile_inps_m * inps_az_p ).quantize(Q2)
    inail_az = (lordo_imponibile_inps_m * inail_p   ).quantize(Q2)

    # ── IRPEF ─────────────────────────────────────────────────────────────────
    imponibile_m   = (lordo_imponibile_inps_m - inps_dip).quantize(Q2)
    imponibile_ann = float(imponibile_m) * 12
    irpef_lorda_m  = Decimal(str(calcola_irpef_lorda(float(imponibile_m), anno=anno))).quantize(Q2)
    detrazioni_m   = Decimal(str(calcola_detrazioni(float(imponibile_m), anno=anno, num_familiari=num_familiari_a_carico))).quantize(Q2)

    # ── Bonus fiscali ─────────────────────────────────────────────────────────
    ti             = Decimal(str(calcola_trattamento_integrativo(imponibile_ann, anno))).quantize(Q2)
    if fiscale_modalita_cedolino and l207_percentuale_imponibile is not None:
        l207 = (imponibile_m * Decimal(str(l207_percentuale_imponibile))).quantize(Q2)
    else:
        l207 = Decimal(str(calcola_bonus_l207_2024(imponibile_ann, anno))).quantize(Q2)

    if ti_l207_non_cumulabili and l207 > 0:
        ti = Decimal('0.00')

    # Modalità cedolino: L207 trattato come detrazione IRPEF (non come credito netto)
    if fiscale_modalita_cedolino and l207_come_detrazione_irpef:
        # L207 in detrazione IRPEF (come molti cedolini): non sommare L207 alla voce
        # «detrazioni» in output — resta solo art. 13 TUIR (+ stima fam. art. 12), così
        # il confronto con la riga cedolino è coerente; L207 resta su riga dedicata in UI.
        detrazioni_per_irpef_m = (detrazioni_m + l207).quantize(Q2)
        irpef_netta_m = max(irpef_lorda_m - detrazioni_per_irpef_m, Decimal('0')).quantize(Q2)
        netto_base = (lordo_imponibile_inps_m - inps_dip - irpef_netta_m).quantize(Q2)
        crediti_imposta = ti.quantize(Q2)
        netto_totale = (netto_base + ti).quantize(Q2)
    else:
        irpef_netta_m = max(irpef_lorda_m - detrazioni_m, Decimal('0')).quantize(Q2)
        netto_base = (lordo_imponibile_inps_m - inps_dip - irpef_netta_m).quantize(Q2)
        crediti_imposta = (ti + l207).quantize(Q2)
        netto_totale = (netto_base + ti + l207).quantize(Q2)

    # Opzione provvisoria: alcuni cedolini mostrano L207 sia in detrazione IRPEF
    # che come competenza separata in corpo cedolino.
    if fiscale_modalita_cedolino and l207_come_detrazione_irpef and l207_anche_come_credito_netto:
        netto_totale = (netto_totale + l207).quantize(Q2)
        crediti_imposta = (crediti_imposta + l207).quantize(Q2)

    # Leve provvisorie di riconciliazione voci non modellate nel motore core
    if competenze_extra_non_imponibili:
        netto_totale = (netto_totale + competenze_extra_non_imponibili).quantize(Q2)
    if trattenute_extra_mese:
        netto_totale = (netto_totale - trattenute_extra_mese).quantize(Q2)

    # ── Addizionali ───────────────────────────────────────────────────────────
    add_reg_ann = Decimal(str(calcola_addizionale_regionale_sicilia(imponibile_ann, anno=anno, regione=regione_residenza))).quantize(Q2)
    _com_res = (comune_residenza or 'Palermo').strip() or 'Palermo'
    _pr_res = ((provincia_residenza or 'PA').strip() or 'PA')[:2]
    add_com_ann = Decimal(str(
        calcola_addizionale_comunale_stima(imponibile_ann, anno=anno, comune=_com_res, provincia=_pr_res),
    )).quantize(Q2)
    add_reg_m   = (add_reg_ann / 12).quantize(Q2)
    add_com_m   = (add_com_ann / 12).quantize(Q2)
    _forza_add = (forza_add_reg_m is not None) or (forza_add_com_m is not None)
    if forza_add_reg_m is not None:
        add_reg_m = Decimal(str(forza_add_reg_m)).quantize(Q2)
    if forza_add_com_m is not None:
        add_com_m = Decimal(str(forza_add_com_m)).quantize(Q2)
    if _forza_add:
        netto_totale = (netto_totale - add_reg_m - add_com_m).quantize(Q2)

    tfr_m     = (lordo_mensile * c_tfr).quantize(Q2)
    rat_fer_m = (lordo_mensile * c_fer).quantize(Q2)
    tot_ratei_lordi = (tfr_m + rat13_m + rat14_m + rat_fer_m).quantize(Q2)

    ratio     = (netto_base / lordo_imponibile_inps_m).quantize(Q6) if lordo_imponibile_inps_m else Decimal('0')
    tfr_n     = (tfr_m     * ratio).quantize(Q2)
    rat13_n   = (rat13_m   * ratio).quantize(Q2)
    rat14_n   = (rat14_m   * ratio).quantize(Q2)
    rat_fer_n = (rat_fer_m * ratio).quantize(Q2)
    tot_ratei_netti = (tfr_n + rat13_n + rat14_n + rat_fer_n).quantize(Q2)

    giorni_m_teorici = (ore_mensili / ore_giorn).quantize(Q2) if ore_giorn else Decimal('26')
    tfr_ora   = (tfr_m   / ore_mensili).quantize(Q4) if ore_mensili else Decimal('0')
    tfr_gg    = (tfr_m   / giorni_m_teorici).quantize(Q2) if giorni_m_teorici else Decimal('0')
    rat13_ora = (rat13_m / ore_mensili).quantize(Q4) if ore_mensili else Decimal('0')
    rat13_gg  = (rat13_m / giorni_m_teorici).quantize(Q2) if giorni_m_teorici else Decimal('0')

    lordo_con_ratei = (lordo_mensile + tot_ratei_lordi).quantize(Q2)
    netto_con_ratei = (netto_totale  + tot_ratei_netti).quantize(Q2)

    # ── F24 ───────────────────────────────────────────────────────────────────
    f24_inps        = (inps_dip + inps_az).quantize(Q2)
    f24_erario_lord = irpef_netta_m
    f24_erario      = max(irpef_netta_m - crediti_imposta, Decimal('0')).quantize(Q2)
    f24_totale      = (f24_inps + f24_erario).quantize(Q2)

    # ── Costo azienda ─────────────────────────────────────────────────────────
    costo_corrente  = (lordo_imponibile_inps_m + inps_az + inail_az).quantize(Q2)
    costo_differito = tot_ratei_lordi
    costo_mensile   = (costo_corrente + costo_differito).quantize(Q2)
    costo_annuo     = (costo_mensile * 12).quantize(Q2)

    magg_dom_pct  = int(magg_dom_p  * 100)
    magg_fest_pct = int(magg_fest_p * 100)
    magg_straord_dom_pct = int(magg_straord_dom * 100)

    # ── Ricognizione voci e aggancio classificazione DB ─────────────────────
    # FIPE (2024+): EDR è nella contingenza tabellare — etichetta e importo voce «CONTINGENZA» senza sommare EDR.
    _edr_assorbito_in_cont = ccnl_fipe_edr_assorbito_in_contingenza(cp, ccnl_obj)
    _descr_contingenza_voce = 'Contingenza' if _edr_assorbito_in_cont else 'Contingenza + EDR'
    _importo_contingenza_voce = (
        contingenza.quantize(Q2) if _edr_assorbito_in_cont else (contingenza + edr).quantize(Q2)
    )
    voci_input = [
        ('MINIMO_TABELLARE', 'Paga base', paga_base),
        ('CONTINGENZA', _descr_contingenza_voce, _importo_contingenza_voce),
        ('IND_FUNZIONE', 'Indennità contrattuali', (indennita + indennita_extra_r).quantize(Q2)),
        ('SUPERMINIMO', 'Superminimo', superminimo_r),
        ('SCATTO_ANZIANITA', 'Scatto anzianità', scatto_r),
        ('EL_DIS_SAN', 'EL.DIS.SAN', eds_r),
        ('EL_DIS_BIL', 'EL.DIS.BIL', edb_r),
        ('IND_TURNO', 'Indennità turno', indennita_turno_r),
        ('STRAORD_DIURNO', 'Straordinario diurno', imp_sd),
        ('STRAORD_NOTTURNO', 'Straordinario notturno', imp_sn),
        ('STRAORD_FESTIVO', 'Straordinario festivo e nott. festivo', (imp_sf + imp_snf).quantize(Q2)),
        ('STRAORD_DOMENICA', 'Straordinario domenicale', imp_sdom),
        ('MAGG_DOM_FEST', 'Maggiorazioni domenicali/festive', (imp_dom_magg + imp_fest_magg).quantize(Q2)),
        ('TI_DL3_2020', 'Trattamento integrativo', ti),
        ('BONUS_L207_2024', 'Bonus L207/2024', l207),
        ('TREDICESIMA', 'Rateo tredicesima', rat13_m),
        ('QUATTORDICESIMA', 'Rateo quattordicesima', rat14_m),
    ]

    codici = {c for c, _, _ in voci_input}
    voci_db = {
        v.codice: v for v in VoceRetributiva.objects.filter(codice__in=codici, attivo=True)
    }
    from .models import MappaturaVoceMotore
    from .motore_paga_schema import applica_trattamento_a_riga_voce, calcola_schema_divisori

    mappature_motore = {
        m.codice_voce: m
        for m in MappaturaVoceMotore.objects.filter(attivo=True, codice_voce__in=codici)
    }

    voci_classificate = []
    for codice, descr, importo in voci_input:
        if importo == Decimal('0'):
            continue
        voce = voci_db.get(codice)
        mappa = mappature_motore.get(codice)
        base_row = {
            'codice': codice,
            'descrizione': descr,
            'importo': importo.quantize(Q2),
            'voce_db_trovata': bool(voce),
            'imponibile_inps': bool(voce.imponibile_inps) if voce else None,
            'imponibile_inail': bool(voce.imponibile_inail) if voce else None,
            'imponibile_irpef': bool(voce.imponibile_irpef) if voce else None,
            'categoria': voce.categoria if voce else None,
        }
        voci_classificate.append(
            applica_trattamento_a_riga_voce(
                base_row,
                voce,
                mappa,
                rateo_13_mensile_in_imponibile=rateo_13_mensile_in_imponibile,
                rateo_14_mensile_in_imponibile=rateo_14_mensile_in_imponibile,
            ),
        )

    schema_divisori = None
    if divisore_dec > Decimal('30'):
        schema_divisori = calcola_schema_divisori(
            divisore_orario=divisore_dec,
            ore_settimanali=ore_sett_r,
        )

    # Ore posizione INPS/INAIL (cedolino): somma ore retributive del mese, non le ore mensili contrattuali × coeff.
    ore_posizione_inps = (
        Decimal(str(ore_ordinarie_retribuite or 0))
        + Decimal(str(ore_domenicali or 0))
        + Decimal(str(ore_festivi or 0))
        + Decimal(str(ore_straord_diurno or 0))
        + Decimal(str(ore_straord_notturno or 0))
        + Decimal(str(ore_straord_festivo or 0))
        + Decimal(str(ore_straord_domenica or 0))
        + Decimal(str(ore_straord_nott_fest or 0))
    ).quantize(Q2)

    return {
        # ── Periodo ───────────────────────────────────────────────────────────
        'anno': anno, 'mese': mese,
        'giorni_nel_mese': giorni_nel_mese, 'giorni_lavorati': gg_lav,
        'frazione': frazione, 'prorata': frazione < Decimal('1'),
        # ── Calendario ────────────────────────────────────────────────────────
        'calendario_motore_id':      calendario_motore_id,
        'cal_giorni_lavorativi':    cal_data['giorni_lavorativi'],
        'cal_giorni_ordinari':      cal_giorni_ordinari,
        'cal_chiusure_settimanali': cal_data['chiusure_settimanali'],
        'cal_festivi':              cal_data['festivi'],
        'cal_chiusure_extra':       cal_data['chiusure_extra'],
        'cal_giorni_conv_26':       cal_data['giorni_conv_26'],
        'cal_festivita':            cal_data['dates_festivita'],
        'cal_domeniche_n':          len(cal_domeniche),
        'cal_domeniche_lav_n':      len(cal_domeniche_lav),
        'cal_dom_fest_n':           cal_dom_fest_n,
        'cal_festivi_lav_n':        len(cal_festivi_lav),
        'cal_griglia':              cal_griglia,
        # ── Ore / divisore ────────────────────────────────────────────────────
        'ore_mensili': ore_mensili, 'ore_giornaliere': ore_giorn,
        'ore_posizione_inps': ore_posizione_inps,
        'ore_posizione_inail': ore_posizione_inps,
        'ore_settimanali_contr': ore_sett_r,
        # Coincide con ore_giorn (h/sett contrattuali ÷ 6); esposto per template/retrocompat.
        'ore_media_settimanale_su_6gg': ore_media_settimanale_su_6gg,
        'giorni_eff_settimana': round(float(ore_sett_r) / float(ore_giorn)) if ore_giorn else 6,
        'divisore': divisore_dec,
        'schema_divisori': schema_divisori,
        'paga_oraria': paga_oraria, 'paga_giornaliera': paga_giornaliera,
        'retribuzione_oraria_di_fatto': retribuzione_oraria_di_fatto,
        'oraria_tabellare_paga_base': h_oraria_paga_base,
        'oraria_tabellare_contingenza': h_oraria_contingenza,
        'oraria_tabellare_edr': h_oraria_edr,
        'oraria_tabellare_indennita': h_oraria_indennita,
        'oraria_tabellare_scatto': h_oraria_scatto,
        'oraria_tabellare_superminimo': h_oraria_superminimo,
        'oraria_tabellare_el_dis_san': h_oraria_el_dis_san,
        'oraria_tabellare_el_dis_bil': h_oraria_el_dis_bil,
        # Somma voci tabellari FT nel mese (× frazione giorni); supermin./turno/extra fuori.
        'lordo_tabellare_ft_equiv': lordo_tabellare_ft_equiv,
        'tabellare_gap_ft': tabellare_gap_ft,  # sempre 0: nessuna inferenza da ``totale_tabellare``
        # Trasparenza foglio INPS (divisore orario): numeratore €/h paga base dopo pro-rata giorni
        'importo_ft_paga_per_div': (_paga_ft_div * frazione).quantize(Q2) if divisore_dec > Decimal('30') else None,
        'rof_usa_minimo_tabellare': _rof_usa_minimo_tabellare,
        # ── Voci base ─────────────────────────────────────────────────────────
        'paga_base': paga_base, 'contingenza': contingenza, 'edr': edr, 'indennita': indennita,
        'superminimo': superminimo_r, 'indennita_turno': indennita_turno_r,
        'scatto': scatto_r, 'indennita_extra': indennita_extra_r,
        'lordo_base': lordo_base,
        # ── Straordinari ──────────────────────────────────────────────────────
        'imp_sd': imp_sd, 'imp_sn': imp_sn, 'imp_sf': imp_sf, 'imp_snf': imp_snf,
        'imp_sdom': imp_sdom,
        'imp_ordinario_ore': imp_ordinario_ore,
        'oraria_ordinario_da_competenza': oraria_ordinario_da_competenza,
        'tot_straord': tot_straord,
        'ore_ordinarie_retribuite': ore_ordinarie_retribuite,
        'modalita_ore_effettive': modalita_ore_effettive,
        'domenicale_compenso_completo': domenicale_compenso_completo,
        'festivo_compenso_completo': festivo_compenso_completo,
        'ore_straord_diurno': ore_straord_diurno, 'ore_straord_notturno': ore_straord_notturno,
        'ore_straord_festivo': ore_straord_festivo, 'ore_straord_domenica': ore_straord_domenica,
        'ore_straord_nott_fest': ore_straord_nott_fest,
        # ── Domenicali / festivi ───────────────────────────────────────────────
        'imp_dom_magg': imp_dom_magg,
        'imp_fest_magg': imp_fest_magg,
        'tot_dom_fest': tot_dom_fest,
        'ore_domenicali_auto': ore_domenicali_auto,
        'ore_domenicali': ore_domenicali, 'ore_festivi': ore_festivi,
        # ── Assenze ───────────────────────────────────────────────────────────
        'decurt_assenze': decurt_assenze,
        'giorni_assenza_ingiust': giorni_assenza_ingiust,
        'gg_ferie_godute': giorni_ferie_godute, 'ore_perm_goduti': ore_permessi_goduti,
        # ── Lordo ─────────────────────────────────────────────────────────────
        'lordo_mensile': lordo_mensile,
        'lordo_imponibile_inps_m': lordo_imponibile_inps_m,
        # ── Contributi ────────────────────────────────────────────────────────
        'inps_dip_perc': (inps_dip_p * 100).quantize(Q2),
        'inps_az_perc':  (inps_az_p  * 100).quantize(Q2),
        'inail_perc':    (inail_p    * 100).quantize(Q2),
        'inps_dip': inps_dip, 'inps_az': inps_az, 'inail_az': inail_az,
        'tot_contrib_dip': inps_dip,
        'tot_contrib_az': (inps_az + inail_az).quantize(Q2),
        # ── IRPEF ─────────────────────────────────────────────────────────────
        'imponibile_m': imponibile_m,
        'imponibile_ann': Decimal(str(round(imponibile_ann, 2))),
        'irpef_lorda': irpef_lorda_m, 'detrazioni': detrazioni_m, 'irpef_netta': irpef_netta_m,
        'add_reg_m': add_reg_m, 'add_com_m': add_com_m,
        'ti': ti, 'l207': l207, 'crediti_imposta': crediti_imposta,
        'fiscale_modalita_cedolino': fiscale_modalita_cedolino,
        'ti_l207_non_cumulabili': ti_l207_non_cumulabili,
        'l207_come_detrazione_irpef': l207_come_detrazione_irpef,
        'l207_anche_come_credito_netto': l207_anche_come_credito_netto,
        'competenze_extra_non_imponibili': competenze_extra_non_imponibili,
        'trattenute_extra_mese': trattenute_extra_mese,
        # ── Netto ─────────────────────────────────────────────────────────────
        'netto_base': netto_base, 'netto_totale': netto_totale,
        # ── Ratei ─────────────────────────────────────────────────────────────
        'c_tfr': c_tfr, 'c_13': c_13, 'c_14': c_14, 'c_fer': c_fer,
        'rateo_13_mensile_in_imponibile': rateo_13_mensile_in_imponibile,
        'rateo_14_mensile_in_imponibile': rateo_14_mensile_in_imponibile,
        'rat13_in_imponibile_m': rat13_in_imponibile_m,
        'rat14_in_imponibile_m': rat14_in_imponibile_m,
        'tfr_m': tfr_m, 'rat13_m': rat13_m, 'rat14_m': rat14_m, 'rat_fer_m': rat_fer_m,
        'tot_ratei_lordi': tot_ratei_lordi,
        'tfr_ora': tfr_ora, 'tfr_gg': tfr_gg, 'rat13_ora': rat13_ora, 'rat13_gg': rat13_gg,
        'ratio': ratio,
        'tfr_n': tfr_n, 'rat13_n': rat13_n, 'rat14_n': rat14_n, 'rat_fer_n': rat_fer_n,
        'tot_ratei_netti': tot_ratei_netti,
        'lordo_con_ratei': lordo_con_ratei, 'netto_con_ratei': netto_con_ratei,
        # ── F24 ───────────────────────────────────────────────────────────────
        'f24_inps': f24_inps, 'f24_erario_lord': f24_erario_lord,
        'f24_erario': f24_erario, 'f24_totale': f24_totale,
        # ── Costo azienda ─────────────────────────────────────────────────────
        'costo_corrente': costo_corrente, 'costo_differito': costo_differito,
        'costo_mensile': costo_mensile, 'costo_annuo': costo_annuo,
        # ── Percentuali maggiorazioni (per display) ───────────────────────────
        'magg_diur_pct': int(magg_straord_fer * 100),
        'magg_nott_pct': int(magg_straord_nott * 100),
        'magg_fest_pct': int(magg_straord_fest * 100),
        'magg_nf_pct':   int(magg_nf * 100),
        'magg_dom_pct':  magg_dom_pct,
        'magg_fest_day_pct': magg_fest_pct,
        'magg_straord_dom_pct': magg_straord_dom_pct,
        # ── Classificazione voci da DB (audit/coerenza cross-processo) ─────
        'voci_classificate': voci_classificate,
    }
