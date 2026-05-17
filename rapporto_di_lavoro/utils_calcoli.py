"""
Utility per calcoli economici CCNL - Costo azienda e netto dipendente
Implementa calcoli INPS, IRPEF, TFR, ratei secondo normativa italiana

ATTENZIONE: queste utility sono ora considerati fallback di compatibilità.
Per il motore busta paga completo usare `rapporto_di_lavoro.motore_unico.MotoreRetributivo`
associato a `rapporto_di_lavoro.openfisca_adapter.OpenFiscaAdapter`.

Riferimenti normativi 2024-2026:
- Scaglioni IRPEF: art. 11 TUIR modificato da L. 213/2023 (Legge Bilancio 2024),
  confermati per 2025 e 2026 da L. 207/2024 (LdB 2025):
    23% fino a €28.000 | 35% €28.001-€50.000 | 43% oltre €50.000
- Detrazioni lavoro dipendente: art. 13 TUIR come modificato da L. 213/2023,
  confermato da L. 207/2024 — Circ. AE n. 2/E 2024:
    ≤€15.000: €1.955  | €15.001-€28.000: decrescente da ~€3.100 a €1.910
    €28.001-€50.000: decrescente a €0    | >€50.000: €0
- Trattamento Integrativo: art. 1 L. 21/2020 (provvedimento permanente),
  €100/mese per imponibile ≤€15.000; decrescente fino a €28.000; confermato 2026.
- Bonus art. 1 c. 4 L. 207/2024: dal 2025 importo tabellare mensile (es. € 70,82) in fascia
  se record ``BonusFiscale`` valido. Dal **2026** stima a **fasce percentuali** sul reddito
  annuo (5,3% / 4,8% oltre € 8.500 fino a € 20.000), coerente con cedolini commerciali;
  prevale ``formula_calcolo`` su ``BonusFiscale`` se valorizzata.
- INPS CCNL FIPE: Circ. INPS n. 15/2025 — aliquota dipendente 9,36%
  (IVS 9,19% + EBT/FSBT 0,17%); azienda ~29,31% totale oneri previdenziali.

Per busta mensile completa, simulazioni e conciliazione lato «nostro» calcolo
usare :mod:`rapporto_di_lavoro.utils_motore_paga` (vedi :mod:`rapporto_di_lavoro.motori_canonici`);
questo modulo resta utilità fiscale/contributiva a grana più grossa o richiamata dal motore busta.
"""
from datetime import date as _date
from decimal import Decimal

# ============================================================
# PARAMETRI CONTRIBUTIVI E FISCALI
# ============================================================

# Aliquote contributive CCNL FIPE 2025-2026
# Dipendente: IVS 9,19% + EBT/FSBT 0,17% = 9,36% (Circ. INPS 15/2025)
ALIQUOTA_INPS_AZIENDA = 0.30      # Contributi azienda ~30% (valore medio legacy)
ALIQUOTA_INPS_DIP = 0.0936        # Contributi dipendente CCNL FIPE: 9,36%
ALIQUOTA_TFR = 1 / 13.5           # Quota TFR mensile (art. 2120 c.c.)

# Ratei mensilità aggiuntive
RATEO_MENSILITA = 1 / 12          # Rateo 13a e 14a
RATEO_FERIE_PERMESSI = 0.12       # 12% del lordo per ferie/permessi


# ============================================================
# FUNZIONI DI CALCOLO CONTRIBUTI E IMPOSTE
# ============================================================

def calcola_inps_dipendente(lordo):
    """
    Calcola contributi INPS a carico dipendente CCNL FIPE.
    Aliquota 9,36% = IVS 9,19% + EBT/FSBT 0,17% (Circ. INPS 15/2025).
    """
    return round(lordo * ALIQUOTA_INPS_DIP, 2)


def calcola_imponibile_fiscale(lordo, inps_dip):
    """Calcola imponibile fiscale (lordo - contributi)"""
    return round(lordo - inps_dip, 2)


def calcola_irpef_lorda(imponibile, anno=None):
    """
    Calcola IRPEF lorda mensile con scaglioni progressivi.
    Priorità: tabella DB ScaglioneIRPEF (se presente e valida per l'anno),
    altrimenti fallback hardcoded 2024-2026.
    Scaglioni ex art. 11 TUIR (L. 213/2023 — Riforma IRPEF, confermati L. 207/2024):
      - 0 – 28.000 €:   23%
      - 28.001 – 50.000 €: 35%
      - Oltre 50.000 €:    43%
    """
    reddito_annuo = Decimal(str(imponibile)) * Decimal('12')

    # --- Lookup DB (ScaglioneIRPEF) ---
    anno_ref = int(anno) if anno else _date.today().year
    try:
        from django.db.models import Q as _Q
        from .models import ScaglioneIRPEF  # noqa: PLC0415

        check_date = _date(anno_ref, 6, 1)
        scaglioni = list(
            ScaglioneIRPEF.objects.filter(
                attivo=True,
                data_validita_da__lte=check_date,
            ).filter(
                _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
            ).order_by('scaglione_numero')
        )

        if scaglioni:
            irpef_annua = Decimal('0')
            for s in scaglioni:
                limite_inf = Decimal(str(s.reddito_da or 0))
                limite_sup = Decimal(str(s.reddito_a)) if s.reddito_a is not None else None
                aliquota = Decimal(str(s.aliquota or 0)) / Decimal('100')

                if reddito_annuo <= limite_inf:
                    continue

                imponibile_scaglione = (
                    min(reddito_annuo, limite_sup) - limite_inf
                ) if limite_sup is not None else (reddito_annuo - limite_inf)

                if imponibile_scaglione > 0:
                    irpef_annua += imponibile_scaglione * aliquota

            return round(float(irpef_annua / Decimal('12')), 2)
    except Exception:
        pass

    # --- Fallback hardcoded ---
    reddito_annuo = float(reddito_annuo)

    if reddito_annuo <= 28000:
        irpef_annua = reddito_annuo * 0.23
    elif reddito_annuo <= 50000:
        irpef_annua = 28000 * 0.23 + (reddito_annuo - 28000) * 0.35
    else:
        irpef_annua = (
            28000 * 0.23 +
            22000 * 0.35 +
            (reddito_annuo - 50000) * 0.43
        )

    return round(irpef_annua / 12, 2)  # IRPEF mensile


def calcola_detrazioni(imponibile, anno=None, num_familiari: int = 0):
    """
    Calcola detrazioni fiscali mensili per lavoro dipendente + familiari a carico.

    Art. 13 TUIR (lavoro dipendente):
      Priorità: tabella DB `DetrazioneLavoroDipendente` (se presente e valida per anno),
      altrimenti fallback hardcoded 2024-2026.
      L. 213/2023 (Legge Bilancio 2024), confermato Circ. AE n. 2/E del 15/01/2024.

      - Reddito ≤ €15.000: detrazione fissa €1.955
      - Reddito €15.001 – €28.000: 1.910 + 1.190 × (28.000 − reddito) / 13.000
      - Reddito €28.001 – €50.000: 1.910 × (50.000 − reddito) / 22.000
      - Reddito > €50.000: €0

    Art. 12 TUIR (familiari a carico) — stima simulazione:
      €950/anno per familiare, riduzione proporzionale: detraz × (95.000 − reddito) / 95.000
      Se reddito ≥ €95.000 → €0.  Trattamento uniforme (non distingue coniuge/figli/età).

    Restituisce la detrazione mensile totale (€/mese).
    """
    reddito_annuo = Decimal(str(imponibile)) * Decimal('12')

    # --- Lookup DB (DetrazioneLavoroDipendente) ---
    anno_ref = int(anno) if anno else _date.today().year
    detrazioni_lav_annue = None
    try:
        from django.db.models import Q as _Q
        from .models import DetrazioneLavoroDipendente  # noqa: PLC0415

        check_date = _date(anno_ref, 6, 1)
        fasce = DetrazioneLavoroDipendente.objects.filter(
            anno=anno_ref,
            attivo=True,
            data_validita_da__lte=check_date,
        ).filter(
            _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
        ).order_by('reddito_da')

        for f in fasce:
            da = Decimal(str(f.reddito_da or 0))
            a = Decimal(str(f.reddito_a)) if f.reddito_a is not None else None
            if reddito_annuo < da:
                continue
            if a is not None and reddito_annuo > a:
                continue

            base = Decimal(str(f.importo_base_annuo or 0))
            coeff = Decimal(str(f.coefficiente_variabile_annuo)) if f.coefficiente_variabile_annuo is not None else None
            rif = Decimal(str(f.reddito_riferimento)) if f.reddito_riferimento is not None else None
            div = Decimal(str(f.divisore_fascia)) if f.divisore_fascia is not None else None

            if coeff is not None and rif is not None and div not in (None, Decimal('0')):
                val = base + coeff * (rif - reddito_annuo) / div
            else:
                val = base

            detrazioni_lav_annue = float(max(val, Decimal('0')))
            break
    except Exception:
        pass

    # --- Fallback hardcoded art. 13 TUIR ---
    if detrazioni_lav_annue is None:
        reddito_annuo_f = float(reddito_annuo)
        if reddito_annuo_f <= 15000:
            detrazioni_lav_annue = 1955.0
        elif reddito_annuo_f <= 28000:
            detrazioni_lav_annue = 1910 + 1190 * (28000 - reddito_annuo_f) / 13000
        elif reddito_annuo_f <= 50000:
            detrazioni_lav_annue = 1910 * (50000 - reddito_annuo_f) / 22000
        else:
            detrazioni_lav_annue = 0.0

    # --- Art. 12 TUIR: familiari a carico (stima simulazione) ---
    # €950/anno per familiare, riduzione proporzionale: detraz × (95.000 − reddito) / 95.000
    det_familiari_annue = 0.0
    if num_familiari and num_familiari > 0:
        reddito_annuo_f = float(reddito_annuo)
        if reddito_annuo_f < 95000:
            coeff_rid = (95000 - reddito_annuo_f) / 95000
            det_familiari_annue = 950.0 * num_familiari * coeff_rid
        # reddito ≥ 95.000 → detrazione = 0

    return round((detrazioni_lav_annue + det_familiari_annue) / 12, 2)


def calcola_netto_dipendente(
    lordo,
    *,
    anno=None,
    num_familiari: int = 0,
    aliquota_inps_dip=None,
):
    """
    Calcola il netto mensile per il dipendente (schema semplificato rispetto al motore busta completo).

    DEPRECATO: usare `rapporto_di_lavoro.motore_unico.MotoreRetributivo`
    per le elaborazioni ufficiali della busta paga.

    Args:
        lordo: stipendio lordo mensile imponibile INPS/IRPEF (stesso ordine di grandezza del motore).
        anno: anno fiscale di riferimento (scaglioni IRPEF e detrazioni da DB); default anno di sistema.
        num_familiari: detrazioni art. 12 TUIR (stima).
        aliquota_inps_dip: quota INPS dip. (es. 0.0936); se None usa ``ALIQUOTA_INPS_DIP`` (FIPE).

    Returns:
        dict con inps_dipendente, imponibile, irpef_lorda, detrazioni, irpef_netta, netto.
    """
    lordo_float = float(lordo)
    aliq = float(aliquota_inps_dip) if aliquota_inps_dip is not None else ALIQUOTA_INPS_DIP
    inps_dip = round(lordo_float * aliq, 2)
    imponibile = calcola_imponibile_fiscale(lordo_float, inps_dip)
    irpef_lorda = calcola_irpef_lorda(imponibile, anno=anno)
    detrazioni = calcola_detrazioni(imponibile, anno=anno, num_familiari=num_familiari)
    irpef_netta = max(irpef_lorda - detrazioni, 0)

    netto = lordo_float - inps_dip - irpef_netta

    return {
        "inps_dipendente": round(inps_dip, 2),
        "imponibile": round(imponibile, 2),
        "irpef_lorda": round(irpef_lorda, 2),
        "detrazioni": round(detrazioni, 2),
        "irpef_netta": round(irpef_netta, 2),
        "netto": round(netto, 2),
    }


def calcola_costo_azienda(lordo):
    """
    Calcola il costo totale mensile e annuo per l'azienda (schema semplificato, aliquote legacy in modulo).

    DEPRECATO: usando il nuovo motore reale, preferire `MotoreRetributivo`
    + `OpenFiscaAdapter` per il costo azienda completo.

    Non sostituisce ``calcola_busta_paga_mese``: mancano ParametroContributi da DB, decontribuzioni,
    part-time, maggiorazioni e voci del motore. Usare solo per stime rapide o proprietà di comodo su tabellare.

    Include:
    - Lordo dipendente
    - Contributi INPS azienda (30%)
    - TFR (1/13.5)
    - Rateo 13a mensilità (1/12)
    - Rateo 14a mensilità (1/12)
    - Rateo ferie/permessi (12%)
    
    Args:
        lordo (Decimal): Stipendio lordo mensile
        
    Returns:
        dict: Dizionario con dettaglio costi
            - inps_azienda: Contributi INPS azienda
            - tfr: Accantonamento TFR mensile
            - rateo_13: Rateo 13a mensilità
            - rateo_14: Rateo 14a mensilità
            - rateo_ferie_permessi: Rateo ferie/permessi
            - costo_totale_mensile: Costo mensile totale
            - costo_totale_annuo: Costo annuo totale
    """
    lordo_float = float(lordo)
    
    inps_azienda = lordo_float * ALIQUOTA_INPS_AZIENDA
    tfr = lordo_float * ALIQUOTA_TFR
    rateo_13 = lordo_float * RATEO_MENSILITA
    rateo_14 = lordo_float * RATEO_MENSILITA
    rateo_fp = lordo_float * RATEO_FERIE_PERMESSI

    costo_totale = lordo_float + inps_azienda + tfr + rateo_13 + rateo_14 + rateo_fp

    return {
        "inps_azienda": round(inps_azienda, 2),
        "tfr": round(tfr, 2),
        "rateo_13": round(rateo_13, 2),
        "rateo_14": round(rateo_14, 2),
        "rateo_ferie_permessi": round(rateo_fp, 2),
        "costo_totale_mensile": round(costo_totale, 2),
        "costo_totale_annuo": round(costo_totale * 12, 2)
    }


def calcola_completo(lordo):
    """
    Combina ``calcola_netto_dipendente`` e ``calcola_costo_azienda`` su un solo lordo tabellare.

    DEPRECATO: non usare come sostituto del motore busta completa.
    Per il calcolo ufficiale della busta paga usare `MotoreRetributivo`.

    Vietato usarlo come sostituto del motore busta per: buste ufficiali, simulazione annua,
    conciliazione cedolino, proposte HR — in quei flussi usare sempre
    ``rapporto_di_lavoro.utils_motore_paga.calcola_busta_paga_mese`` (eventualmente via
    ``invoca_calcola_busta_paga_mese``). Chiamata residua in codice: solo convenienza su
    ``ParametroCCNLTurismo`` (admin/indicatori).

    Args:
        lordo: stipendio lordo mensile tabellare

    Returns:
        dict con lordo_mensile, netto (dettaglio), costo_azienda (dettaglio).
    """
    netto = calcola_netto_dipendente(lordo)
    costo = calcola_costo_azienda(lordo)

    return {
        "lordo_mensile": float(lordo),
        "netto": netto,
        "costo_azienda": costo
    }


# ============================================================
# TRATTAMENTO INTEGRATIVO DL 3/2020
# ============================================================

def calcola_trattamento_integrativo(imponibile_annuo, anno=2025):
    """
    Calcola il Trattamento Integrativo DL 3/2020 (ex Bonus Renzi).

    NATURA DEL BONUS:
    - NON concorre all'imponibile INPS (non è base contributiva)
    - NON concorre all'imponibile IRPEF (non è base fiscale)
    - NON concorre alla base di calcolo 13ª, 14ª e TFR
    - Viene aggiunto direttamente al netto in busta paga
    - L'azienda lo anticipa e lo recupera tramite F24 (nessun costo netto)

    Provvedimento permanente (art. 1 L. 21/2020): attivo per 2024, 2025 e 2026.
    - Imponibile annuo ≤ soglia_max (default €15.000): €100/mese
    - Imponibile annuo tra soglia_max e €28.000: importo decrescente proporzionale
    - Imponibile annuo > €28.000: €0

    Il record DB (BonusFiscale codice='TI_DL3_2020') viene cercato con verifica
    date di validità per l'anno richiesto. Fallback hardcoded se assente (provvedimento
    permanente — diversamente dal Bonus L207 che era misura temporanea).

    Args:
        imponibile_annuo (Decimal | float): Imponibile fiscale annualizzato
        anno (int): Anno di riferimento

    Returns:
        Decimal: Importo mensile del TI spettante (€0 se non spetta)
    """
    from datetime import date as _date
    from django.db.models import Q as _Q

    imponibile_annuo = Decimal(str(imponibile_annuo))
    SOGLIA_ESCLUSIONE = Decimal('28000')  # Soglia assoluta oltre cui non spetta

    # --- Recupera parametri dal DB con verifica validità per l'anno ---
    importo_mensile_pieno = Decimal('100.00')
    soglia_max = Decimal('15000')
    try:
        from .models import BonusFiscale  # noqa: PLC0415
        check_date = _date(anno, 6, 1)
        bonus = BonusFiscale.objects.filter(
            codice='TI_DL3_2020',
            attivo=True,
            data_validita_da__lte=check_date,
        ).filter(
            _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
        ).first()
        if bonus:
            if bonus.importo_mensile:
                importo_mensile_pieno = Decimal(str(bonus.importo_mensile))
            if bonus.soglia_reddito_max:
                soglia_max = Decimal(str(bonus.soglia_reddito_max))
    except Exception:
        pass  # Fallback ai valori hardcoded (TI è provvedimento permanente)

    # --- Calcolo ---
    if imponibile_annuo > SOGLIA_ESCLUSIONE:
        return Decimal('0')

    if imponibile_annuo <= soglia_max:
        # Importo pieno (fascia bassa)
        return importo_mensile_pieno

    # Fascia progressiva decrescente tra soglia_max e €28.000
    # Formula: TI = importo_pieno × (28.000 - reddito) / (28.000 - soglia_max)
    importo_annuale_pieno = importo_mensile_pieno * 12
    quota = (SOGLIA_ESCLUSIONE - imponibile_annuo) / (SOGLIA_ESCLUSIONE - soglia_max)
    importo_annuale = importo_annuale_pieno * quota
    return (importo_annuale / 12).quantize(Decimal('0.01'))


# ============================================================
# BONUS ART. 1 COMMA 4 L. 207/2024
# ============================================================

def _l207_mensile_cuneo_percentuale_2026(reddito_annuo: Decimal) -> Decimal:
    """
    Dal 2026 il beneficio in busta (cuneo art. 1 c.4 L.207/2024) segue fasce sul reddito
    annuo di lavoro dipendente (stima coerente con cedolini tipo TeamSystem / Zucchetti).

    - fino a € 8.500 inclusi: nessun accredito mensile in questa stima
    - oltre € 8.500 fino a € 15.000: 5,3% dell'anno ÷ 12
    - oltre € 15.000 fino a € 20.000: 4,8% dell'anno ÷ 12
    - oltre € 20.000: € 0
    """
    R = Decimal(str(reddito_annuo))
    if R <= Decimal('8500') or R > Decimal('20000'):
        return Decimal('0')
    if R <= Decimal('15000'):
        return (R * Decimal('0.053') / Decimal('12')).quantize(Decimal('0.01'))
    return (R * Decimal('0.048') / Decimal('12')).quantize(Decimal('0.01'))


def calcola_bonus_l207_2024(imponibile_annuo, anno=2025):
    """
    Calcola il Bonus Art. 1 comma 4 Legge 207/2024 ("ulteriore detrazione temporanea").

    NATURA DEL BONUS:
    - NON concorre all'imponibile INPS (non è base contributiva)
    - NON concorre all'imponibile IRPEF (erogato come sgravio fiscale)
    - NON concorre alla base di calcolo 13ª, 14ª e TFR
    - Viene aggiunto direttamente al netto in busta paga
    - L'azienda lo anticipa e lo recupera tramite F24 (nessun costo netto)

    Dal **2026** (con record ``BonusFiscale`` attivo e valido in data, senza ``formula_calcolo``):
    importo mensile = fasce percentuali sul reddito annuo (vedi ``_l207_mensile_cuneo_percentuale_2026``),
    allineato ai cedolini che non usano più il solo importo fisso € 70,82.

    Se su ``BonusFiscale`` è valorizzata ``formula_calcolo``, prevale ``calcola_importo`` del modello.

    Args:
        imponibile_annuo (Decimal | float): Imponibile fiscale annualizzato (stima reddito annuo)
        anno (int): Anno di riferimento

    Returns:
        Decimal: Importo mensile del bonus spettante (€0 se non spetta o anno non coperto)
    """
    from datetime import date as _date
    from django.db.models import Q as _Q

    R = Decimal(str(imponibile_annuo))
    check_date = _date(int(anno), 6, 1)

    try:
        from .models import BonusFiscale  # noqa: PLC0415

        bonus = (
            BonusFiscale.objects.filter(
                codice='BONUS_L207_2024',
                attivo=True,
                data_validita_da__lte=check_date,
            )
            .filter(
                _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
            )
            .order_by('-anno')
            .first()
        )
    except Exception:
        bonus = None

    if not bonus:
        return Decimal('0')

    if (bonus.formula_calcolo or '').strip():
        try:
            return bonus.calcola_importo(R).quantize(Decimal('0.01'))
        except Exception:
            return Decimal('0')

    if int(anno) >= 2026:
        return _l207_mensile_cuneo_percentuale_2026(R)

    # --- Anni fino al 2025: importo mensile fisso da tabella se in fascia ---
    importo_mensile_pieno = (
        Decimal(str(bonus.importo_mensile)) if bonus.importo_mensile else Decimal('70.82')
    )
    soglia_min = Decimal(str(bonus.soglia_reddito_min)) if bonus.soglia_reddito_min else Decimal('8500')
    soglia_max = Decimal(str(bonus.soglia_reddito_max)) if bonus.soglia_reddito_max else Decimal('20000')

    if soglia_min and R < soglia_min:
        return Decimal('0')
    if soglia_max and R > soglia_max:
        return Decimal('0')

    return importo_mensile_pieno


# ============================================================
# ADDIZIONALI IRPEF REGIONALI/COMUNALI — STIMA SIMULAZIONE
# ============================================================

def calcola_addizionale_regionale_sicilia(imponibile_annuo, anno=None, regione='Sicilia') -> Decimal:
    """
    Stima addizionale IRPEF regionale sull'imponibile annuo.
    Priorità: tabella DB AddizionaleRegionale (aliquota flat per schema attuale).
    Fallback: progressiva Sicilia (legacy).
    Restituisce l'importo ANNUO stimato.
    Le addizionali sono versate nell'anno successivo (saldo) — questa è una stima
    per la pianificazione finanziaria.
    """
    imp = Decimal(str(imponibile_annuo))
    anno_ref = int(anno) if anno else _date.today().year

    # --- Lookup DB (AddizionaleRegionale) ---
    try:
        from django.db.models import Q as _Q
        from .models import AddizionaleRegionale  # noqa: PLC0415

        check_date = _date(anno_ref, 6, 1)
        reg = AddizionaleRegionale.objects.filter(
            attivo=True,
            regione__iexact=str(regione),
            data_validita_da__lte=check_date,
        ).filter(
            _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
        ).first()

        if reg:
            soglia = Decimal(str(reg.soglia_esenzione)) if reg.soglia_esenzione is not None else None
            if soglia is not None and imp <= soglia:
                return Decimal('0')
            aliq = Decimal(str(reg.aliquota)) / Decimal('100')
            return (imp * aliq).quantize(Decimal('0.01'))
    except Exception:
        pass

    # --- Fallback legacy Sicilia progressiva ---
    if imp <= Decimal('0'):
        return Decimal('0')
    elif imp <= Decimal('15000'):
        return (imp * Decimal('0.0123')).quantize(Decimal('0.01'))
    elif imp <= Decimal('28000'):
        return (
            Decimal('15000') * Decimal('0.0123') +
            (imp - Decimal('15000')) * Decimal('0.0173')
        ).quantize(Decimal('0.01'))
    else:
        return (
            Decimal('15000') * Decimal('0.0123') +
            Decimal('13000') * Decimal('0.0173') +
            (imp - Decimal('28000')) * Decimal('0.0223')
        ).quantize(Decimal('0.01'))


def calcola_addizionale_comunale_stima(
    imponibile_annuo,
    aliquota=Decimal('0.0080'),
    anno=None,
    comune='Palermo',
    provincia='PA',
) -> Decimal:
    """
    Stima addizionale IRPEF comunale sull'imponibile annuo.
    Priorità: tabella DB AddizionaleComunale per comune/provincia e anno.
    Fallback: aliquota passata (default 0,80%).
    Restituisce l'importo ANNUO stimato.
    """
    imp = Decimal(str(imponibile_annuo))
    aliq = Decimal(str(aliquota))
    anno_ref = int(anno) if anno else _date.today().year

    # --- Lookup DB (AddizionaleComunale) ---
    try:
        from django.db.models import Q as _Q
        from .models import AddizionaleComunale  # noqa: PLC0415

        check_date = _date(anno_ref, 6, 1)
        com = AddizionaleComunale.objects.filter(
            attivo=True,
            comune__iexact=str(comune),
            provincia__iexact=str(provincia),
            data_validita_da__lte=check_date,
        ).filter(
            _Q(data_validita_a__isnull=True) | _Q(data_validita_a__gte=check_date)
        ).first()

        if com:
            soglia = Decimal(str(com.soglia_esenzione)) if com.soglia_esenzione is not None else None
            if soglia is not None and imp <= soglia:
                return Decimal('0')
            aliq = Decimal(str(com.aliquota)) / Decimal('100')
    except Exception:
        pass

    return max(Decimal('0'), (imp * aliq).quantize(Decimal('0.01')))
