"""
Riconciliazione cedolino (CedolinoMotoreV4 + voci) vs motore busta paga canonico
(:func:`rapporto_di_lavoro.services_simulazione.invoca_calcola_busta_paga_mese`).

Usa ``MappaturaVoceMotore`` (codice_voce + etichetta_riconciliazione) per aggregare
le righe cedolino sulle stesse chiavi del motore.

I cedolini TeamSystem usano spesso **codici riga numerici** (es. ``8001``, ``8010``,
``9824``) mentre il motore espone **codici simbolici** (``MINIMO_TABELLARE``,
``MAGG_DOM_FEST``, …). Per il confronto admin si applicano alias noti sui codici TS
quando non esiste una riga in ``MappaturaVoceMotore`` con lo stesso ``codice_voce``.

Parametri comuni consulente/azienda (contratti, presenze/ore classificate sul ruolo organico,
parametri CCNL) e soglia calendario ruolo in conciliazione: vedi **DOCUMENTAZIONE_UNICA_GESPER.md §3.5**.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape
from typing import Any

from django.utils.safestring import mark_safe

from documenti.cedolini_tolleranze import (
    TOLLERANZA_CONFRONTO_EURO,
    TOLLERANZA_FORMULE_EURO,
    TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
)
from documenti.models import CedolinoMotoreV4, VoceCedolinoMotoreV4

logger = logging.getLogger(__name__)

Q2 = Decimal("0.01")

# Primo giorno **inclusivo** da cui il ``calendario_mensile`` su ``RuoloOrganico2026`` entra nei kwargs
# del motore busta in conciliazione (stesso criterio ``primo_m >= DATA_...`` → marzo 2026 sì).
# Motivo esclusivo della soglia: **prima** del 01/03/2026 il calendario presenze in Gesper non era
# ancora allineato con quello del consulente. Con ROF valorizzato su ``CedolinoMotoreV4`` si omette la
# griglia mensile del ruolo organico (ore da ``allinea_kwargs_calcolo_a_dati_cedolino_v4``). Se il v4
# non ha ROF (buste archivio incomplete), si **ripiega** sulla griglia ruolo come prima della soglia,
# per evitare motore a ore zero e conciliazioni tutte in errore.
DATA_INIZIO_USO_CALENDARIO_RUOLO_CONCILIAZIONE = date(2026, 3, 1)


def cedolino_motore_v4_ha_rof_per_conciliazione(v4: CedolinoMotoreV4) -> bool:
    """True se su ``v4`` c'è ROF / retrib. di fatto usabile per ricavare ore da riga 8001."""
    for attr in ("retr_oraria_att", "retrib_di_fatto"):
        val = getattr(v4, attr, None)
        if val is None:
            continue
        try:
            if Decimal(str(val)) > 0:
                return True
        except Exception:
            continue
    return False


def usa_calendario_ruolo_organico_in_conciliazione(anno: int, mese: int) -> bool:
    """
    True se per la competenza ``(anno, mese)`` il ``calendario_mensile`` del ruolo organico
    concorre ai kwargs del motore busta in conciliazione (dal 01/03/2026 inclusivo).
    """
    return date(int(anno), int(mese), 1) >= DATA_INIZIO_USO_CALENDARIO_RUOLO_CONCILIAZIONE

# Chiavi sintetiche confronto (cedolino TS → grandezze motore non presenti in ``voci_classificate``)
_CED_TS8001_COMPOSITO = "CED_TS8001_COMPOSITO"
_CED_ADDIZ_REGIONALE = "CED_ADDIZ_REGIONALE"
_CED_ADDIZ_COMUNALE = "CED_ADDIZ_COMUNALE"
_CED_TRATTENUTE_EXTRA = "CED_TRATTENUTE_EXTRA"

# Somma voci motore che in busta TeamSystem compaiono tipicamente nella riga «8001 Lavoro ordinario».
_CODICI_MOTORE_COMPOSITO_TS8001 = (
    "MINIMO_TABELLARE",
    "CONTINGENZA",
    "SUPERMINIMO",
    "SCATTO_ANZIANITA",
    "IND_FUNZIONE",
    "EL_DIS_SAN",
    "EL_DIS_BIL",
    "IND_TURNO",
)

# Codice riga cedolino (numerico TeamSystem o equivalente) → codice voce motore / bucket confronto
# 8020/8030: stessa semantica di ``rapporto_di_lavoro.utils_presenze`` (festivo lavorato vs notturno).
# PDF abbreviati possono usare 800/802 al posto di 1800/1802 (vedi ``documenti.motore_cedolino_v4.CODICI``).
_CODICE_TS_A_BUCKET: dict[str, str] = {
    "8010": "MAGG_DOM_FEST",
    "8011": "MAGG_DOM_FEST",
    "8020": "MAGG_DOM_FEST",
    "8030": "STRAORD_NOTTURNO",
    "9824": "BONUS_L207_2024",
    "9825": "BONUS_L207_2024",
    "8001": _CED_TS8001_COMPOSITO,
    "1800": _CED_ADDIZ_REGIONALE,
    "800": _CED_ADDIZ_REGIONALE,
    "1802": _CED_ADDIZ_COMUNALE,
    "802": _CED_ADDIZ_COMUNALE,
    "1812": _CED_ADDIZ_COMUNALE,
    "9250": _CED_TRATTENUTE_EXTRA,
    "9251": _CED_TRATTENUTE_EXTRA,
}


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Q2)
    except Exception:
        return Decimal("0.00")


# Tipi da escludere quando si sommano importi «competenza» da righe TS (8001, 801x, 8020, …).
_TIPI_ESCLUSI_DA_COMPETENZA_IMPORTO = frozenset({"TRATTENUTA", "PREVIDENZA"})


def _somma_importi_voci_codici(
    voci: list,
    codici: set[str] | frozenset[str],
) -> Decimal:
    """Somma ``importo`` delle righe il cui ``codice`` è in ``codici`` (mai TRATTENUTA/PREVIDENZA)."""
    tot = Decimal("0")
    want = {str(c).strip() for c in codici}
    for voce in voci:
        c = (voce.codice or "").strip()
        if c not in want:
            continue
        t = (voce.tipo or "").strip().upper()
        if t in _TIPI_ESCLUSI_DA_COMPETENZA_IMPORTO:
            continue
        tot += _d(voce.importo)
    return tot.quantize(Q2)


def _fmt_eur_it(d: Decimal) -> str:
    s = f"{d:.2f}"
    intp, dec = s.split(".")
    out = []
    for i, c in enumerate(reversed(intp)):
        if i and i % 3 == 0:
            out.append(".")
        out.append(c)
    return "".join(reversed(out)) + "," + dec


def _carica_mappature_attive():
    from rapporto_di_lavoro.models import MappaturaVoceMotore

    return list(
        MappaturaVoceMotore.objects.filter(attivo=True).order_by(
            "ordine_calcolo",
            "codice_voce",
        )
    )


def _codice_motore_per_voce_cedolino(
    voce: VoceCedolinoMotoreV4,
    mappature: list[Any],
) -> str | None:
    cod = (voce.codice or "").strip()
    if not cod:
        return None
    cod_u = cod.upper()
    for m in mappature:
        cv = (m.codice_voce or "").strip()
        if cv and cod_u == cv.upper():
            return cv
    # Alias codici riga TeamSystem / paghe → chiavi motore (dopo match esplicito su Mappatura)
    ts_bucket = _CODICE_TS_A_BUCKET.get(cod.strip())
    if ts_bucket:
        return ts_bucket
    desc_l = (voce.descrizione or "").strip().lower()
    for m in mappature:
        eti = (m.etichetta_riconciliazione or "").strip()
        if not eti:
            continue
        etil = eti.lower()
        if etil and etil in desc_l:
            return (m.codice_voce or "").strip() or None
        if etil and cod.lower() == etil:
            return (m.codice_voce or "").strip() or None
    return None


def allinea_kwargs_calcolo_a_dati_cedolino_v4(
    v4: CedolinoMotoreV4,
    kwargs_calcolo: dict[str, Any],
    *,
    voci_prefetched: list[VoceCedolinoMotoreV4] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Copia ``kwargs_calcolo`` del motore busta paga e lo avvicina ai numeri già presenti sul cedolino v4.

    Obiettivo: stessa ROF/orario di busta, trattenute non presenti sul ruolo, stima L207 coerente con
    la riga cedolino, trattenute addizionali da righe 1800/1802 (e varianti abbreviate PDF 800/802,
    acconto comunale 1812), ore festivo/notturno da importi 8020/8030 quando c’è ROF, forzando nel motore
    ``forza_add_reg_m`` / ``forza_add_com_m`` (con adeguamento del netto erogato).

    Per competenze **prima** di ``DATA_INIZIO_USO_CALENDARIO_RUOLO_CONCILIAZIONE`` questo passo è la
    principale fonte ore/ROF rispetto al PDF, perché il calendario ruolo organico non alimenta i kwargs.

    Restituisce ``(kwargs_allineati, meta_pannello)`` — ``meta_pannello`` è solo informativa (chiavi applicate).
    """
    kw = dict(kwargs_calcolo)
    meta: dict[str, Any] = {"attivo": False, "chiavi": []}

    rof = getattr(v4, "retr_oraria_att", None) or getattr(v4, "retrib_di_fatto", None)
    try:
        rof_d = Decimal(str(rof)) if rof is not None else Decimal("0")
    except Exception:
        rof_d = Decimal("0")

    voci = voci_prefetched
    if voci is None and v4.pk:
        voci = list(
            VoceCedolinoMotoreV4.objects.filter(cedolino=v4).only(
                "codice", "tipo", "importo", "descrizione"
            )
        )
    elif voci is None:
        voci = []

    if rof_d > 0:
        kw["forza_paga_oraria"] = rof_d.quantize(Q2)
        kw["superminimo"] = Decimal("0")
        meta["attivo"] = True
        meta["chiavi"].append("forza_paga_oraria")
        meta["chiavi"].append("superminimo_a_zero_con_rof_cedolino")

    imp8001 = _somma_importi_voci_codici(voci, frozenset({"8001"}))
    imp8010 = _somma_importi_voci_codici(voci, frozenset({"8010", "8011"}))
    imp8020 = _somma_importi_voci_codici(voci, frozenset({"8020"}))
    imp8030 = _somma_importi_voci_codici(voci, frozenset({"8030"}))
    if rof_d > 0 and imp8001 > 0:
        ore_ord_ced = (imp8001 / rof_d).quantize(Q2)
        if ore_ord_ced > 0:
            kw["ore_ordinarie_retribuite"] = ore_ord_ced
            kw["modalita_ore_effettive"] = True
            kw["auto_ore_domenicali_da_calendario"] = False
            meta["attivo"] = True
            meta["chiavi"].append("ore_ordinarie_retribuite_da_ced8001_div_rof")
    if rof_d > 0 and imp8010 > 0:
        # Allineamento a riga «lavoro domenicale» con compenso completo (1 + magg. domenicale 15 %).
        magg_dom = Decimal("0.15")
        den = (rof_d * (Decimal("1") + magg_dom)).quantize(Q2)
        if den > 0:
            kw["ore_domenicali"] = (imp8010 / den).quantize(Q2)
            kw["domenicale_compenso_completo"] = True
            meta["attivo"] = True
            meta["chiavi"].append("ore_domenicali_da_ced801x_div_rof")
    if rof_d > 0 and imp8020 > 0:
        # Riga TS 8020: stesso uso di ``utils_presenze`` (festivo lavorato). Assumiamo compenso «pieno»
        # ore × ROF × (1 + 20 %) come su molti cedolini FIPE (allineato a ``festivo_compenso_completo``).
        magg_fest = Decimal("0.20")
        den_f = (rof_d * (Decimal("1") + magg_fest)).quantize(Q2)
        if den_f > 0:
            kw["ore_festivi"] = (imp8020 / den_f).quantize(Q2)
            kw["festivo_compenso_completo"] = True
            kw["auto_ore_domenicali_da_calendario"] = False
            meta["attivo"] = True
            meta["chiavi"].append("ore_festivi_da_ced8020_div_rof")
    if rof_d > 0 and imp8030 > 0:
        magg_nott = Decimal("0.30")
        den_n = (rof_d * (Decimal("1") + magg_nott)).quantize(Q2)
        if den_n > 0:
            kw["ore_straord_notturno"] = (imp8030 / den_n).quantize(Q2)
            kw["auto_ore_domenicali_da_calendario"] = False
            meta["attivo"] = True
            meta["chiavi"].append("ore_straord_notturno_da_ced8030_div_rof")

    tratt_extra = Decimal("0")
    for voce in voci:
        cod = (voce.codice or "").strip()
        if cod in ("9250", "9251") and (voce.tipo or "").upper() == "TRATTENUTA":
            tratt_extra += _d(voce.importo)
    if tratt_extra > 0:
        prev = _d(kw.get("trattenute_extra_mese"))
        if tratt_extra != prev:
            kw["trattenute_extra_mese"] = tratt_extra.quantize(Q2)
            meta["attivo"] = True
            meta["chiavi"].append("trattenute_extra_da_voci_925x")

    imp_ced = getattr(v4, "imp_irpef_mese", None)
    l207_imp = Decimal("0")
    for voce in voci:
        if (voce.codice or "").strip() in ("9824", "9825"):
            l207_imp += _d(voce.importo)
    try:
        imp_ced_d = Decimal(str(imp_ced)) if imp_ced is not None else Decimal("0")
    except Exception:
        imp_ced_d = Decimal("0")
    if l207_imp > 0 and imp_ced_d > 0:
        pct = (l207_imp / imp_ced_d).quantize(Decimal("0.000001"))
        kw["fiscale_modalita_cedolino"] = True
        kw["l207_come_detrazione_irpef"] = True
        kw["l207_percentuale_imponibile"] = pct
        meta["attivo"] = True
        meta["chiavi"].append("l207_percentuale_da_riga_cedolino")

    add_reg_ced = Decimal("0")
    add_com_ced = Decimal("0")
    for voce in voci:
        cod = (voce.codice or "").strip()
        if (voce.tipo or "").upper() != "TRATTENUTA":
            continue
        if cod in ("1800", "800"):
            add_reg_ced += _d(voce.importo)
        elif cod in ("1802", "802", "1812"):
            add_com_ced += _d(voce.importo)
    if add_reg_ced <= 0:
        try:
            v4_ar = getattr(v4, "addiz_regionale", None)
            if v4_ar is not None and _d(v4_ar) > 0:
                add_reg_ced = _d(v4_ar)
        except Exception:
            pass
    if add_com_ced <= 0:
        try:
            v4_ac = getattr(v4, "addiz_comunale", None)
            if v4_ac is not None and _d(v4_ac) > 0:
                add_com_ced = _d(v4_ac)
        except Exception:
            pass
    if add_reg_ced > 0:
        kw["forza_add_reg_m"] = add_reg_ced.quantize(Q2)
        meta["attivo"] = True
        meta["chiavi"].append("forza_add_reg_da_cedolino")
    if add_com_ced > 0:
        kw["forza_add_com_m"] = add_com_ced.quantize(Q2)
        meta["attivo"] = True
        meta["chiavi"].append("forza_add_com_da_cedolino")

    return kw, meta


def _kwargs_residenza_motore_da_dipendente(dip) -> dict[str, str]:
    """Comune/provincia/regione per addizionali IRPEF (stesso ordine usato da ``calcola_busta_paga_mese``)."""
    if getattr(dip, "domicilio_uguale_residenza", True):
        comune = (getattr(dip, "citta", None) or "").strip()
        prov = (getattr(dip, "provincia", None) or "").strip()
        regione = (getattr(dip, "regione_residenza", None) or "").strip()
    else:
        comune = (getattr(dip, "domicilio_comune", None) or "").strip()
        prov = (getattr(dip, "domicilio_provincia", None) or "").strip()
        regione = (getattr(dip, "domicilio_regione", None) or "").strip()
    if not regione:
        regione = "Sicilia"
    if not comune:
        comune = "Palermo"
    if not prov:
        prov = "PA"
    return {
        "regione_residenza": regione,
        "comune_residenza": comune,
        "provincia_residenza": prov[:2],
    }


def risolvi_contesto_calcolo_motore_paga(
    v4: CedolinoMotoreV4,
    *,
    divisore_raw: str | None = None,
    percorso_fiscale: str | None = "ced_l207_det",
) -> dict[str, Any]:
    """
    Prepara kwargs per ``invoca_calcola_busta_paga_mese`` da dipendente + periodo cedolino v4.

    Allineamento a ``presenze.views._build_rows_scostamento_fiscale`` dove possibile
    (ruolo organico, calendario mensile, scatti tabellari). Passa anche comune/provincia/regione da
    anagrafica dipendente (residenza o domicilio se diverso) per le addizionali IRPEF del motore.

    Per competenze **prima** del 01/03/2026: se il cedolino v4 espone **ROF** si **omette** la griglia
    mensile del ruolo organico (presenze non allineate al consulente; ore da PDF via
    ``allinea_kwargs_calcolo_a_dati_cedolino_v4``). Se **manca** ROF su v4 (archivio incompleto) si
    **ripiega** sulla griglia ruolo come prima, per non lasciare il motore senza ore. Dal **01/03/2026
    inclusivo** la griglia ruolo è sempre la fonte operativa quando presente.
    """
    from anagrafiche.models import Dipendente

    from rapporto_di_lavoro.models import CCNL, RuoloOrganico2026, TipoContratto
    from rapporto_di_lavoro.risoluzione_contratto_motore import (
        anni_di_servizio,
        build_scatti_db,
        calcola_scatto_totale_maturato,
        divisore_str_da_parametro_get,
        kwargs_percorso_fiscale_sim,
        rapporto_sottoscritto_attivo_nel_mese,
        risolvi_parametro_ccnl_per_mese,
        superminimo_da_rapporto_o_ruolo,
    )

    if (v4.natura_busta or "ORDINARIA") != "ORDINARIA":
        return {
            "ok": False,
            "errore": (
                f"Confronto motore paga disponibile solo per buste ORDINARIE "
                f"(natura attuale: {v4.natura_busta})."
            ),
        }

    dip = v4.dipendente
    if isinstance(dip, int):
        try:
            dip = Dipendente.objects.select_related("azienda").get(pk=dip)
        except Dipendente.DoesNotExist:
            return {"ok": False, "errore": "Dipendente non trovato."}

    az = getattr(dip, "azienda", None)
    if az is None:
        return {"ok": False, "errore": "Dipendente senza azienda: impossibile risolvere il contratto."}

    anno, mese = int(v4.anno), int(v4.mese)
    primo_m = date(anno, mese, 1)

    rapporto = rapporto_sottoscritto_attivo_nel_mese(
        dipendente=dip,
        azienda=az,
        anno=anno,
        mese=mese,
    )
    livello_fb = (getattr(dip, "livello", None) or "").strip()
    parametro, fonte_parametro = risolvi_parametro_ccnl_per_mese(
        rapporto=rapporto,
        data_primo_giorno_mese=primo_m,
        livello_fallback=livello_fb,
    )
    if not parametro:
        return {
            "ok": False,
            "errore": (
                "Nessun ParametroCCNLTurismo risolvibile per questo dipendente e mese "
                "(verifica contratto sottoscritto, proposta con CCNL o livello tabellare)."
            ),
        }

    tc = None
    if rapporto is not None and rapporto.tipo_contratto_id:
        tc = rapporto.tipo_contratto

    ruolo = (
        RuoloOrganico2026.objects.filter(azienda=az, dipendente=dip)
        .order_by("-data_modifica")
        .first()
    )
    if tc is None and ruolo is not None and getattr(ruolo, "tipo_contratto_id", None):
        try:
            tc = TipoContratto.objects.get(pk=int(ruolo.tipo_contratto_id))
        except Exception:
            tc = None
    if tc is None:
        tc = TipoContratto.objects.filter(attivo=True).order_by("id").first()
    if not tc:
        return {"ok": False, "errore": "Nessun TipoContratto disponibile per il calcolo."}

    ccnl = CCNL.objects.filter(sigla__icontains="FIPE").first()
    scatti_db = build_scatti_db(ccnl, anno) if ccnl else {}

    livello_eff = ""
    if rapporto and (rapporto.livello_ccnl or "").strip():
        livello_eff = (rapporto.livello_ccnl or "").strip()
    elif ruolo and (ruolo.livello or ""):
        livello_eff = str(ruolo.livello or "").strip()
    else:
        livello_eff = (parametro.livello or "").strip()

    if rapporto and rapporto.data_inizio_rapporto:
        anni = anni_di_servizio(rapporto.data_inizio_rapporto, primo_m)
    elif ruolo is not None:
        anni = int(ruolo.anni_anzianita or 0)
    else:
        anni = 0

    scatto = calcola_scatto_totale_maturato(livello_eff, anni, scatti_db)

    data_inizio_eff = rapporto.data_inizio_rapporto if rapporto else None
    data_fine_eff = rapporto.data_fine_rapporto if rapporto else None
    if data_inizio_eff is None and ruolo is not None:
        data_inizio_eff = ruolo.data_inizio
    if data_fine_eff is None and ruolo is not None:
        data_fine_eff = ruolo.data_fine

    cal_m: dict = {}
    cal_m_full: dict = {}
    if ruolo is not None:
        raw_cal = ruolo.calendario_mensile or {}
        cal_m_full = raw_cal.get(str(mese), raw_cal.get(mese, {})) or {}

    # modalita_griglia_ruolo: diagnostica admin (vedi ``render_confronto_motore_paga_html``).
    modalita_griglia_ruolo = "nessuna"
    if ruolo is not None:
        if usa_calendario_ruolo_organico_in_conciliazione(anno, mese):
            cal_m = cal_m_full
            modalita_griglia_ruolo = "piena" if cal_m else "nessuna"
        elif cedolino_motore_v4_ha_rof_per_conciliazione(v4):
            cal_m = {}
            modalita_griglia_ruolo = "omessa_cedolino_rof"
        elif cal_m_full:
            cal_m = cal_m_full
            modalita_griglia_ruolo = "fallback_senza_rof"
        else:
            cal_m = {}

    ore_ord = _d(cal_m.get("ore_ordinarie_retribuite", 0))
    superminimo_eff = superminimo_da_rapporto_o_ruolo(
        rapporto=rapporto,
        ruolo_superminimo=getattr(ruolo, "superminimo", None) if ruolo else None,
    )
    premio_extra = Decimal("0")
    if rapporto is not None:
        try:
            premio_extra = Decimal(str(rapporto.premio_obiettivi or 0)).quantize(Q2)
        except Exception:
            premio_extra = Decimal("0.00")

    indennita_turno = Decimal("0")
    if ruolo is not None:
        try:
            indennita_turno = Decimal(str(ruolo.indennita_turno or 0)).quantize(Q2)
        except Exception:
            indennita_turno = Decimal("0.00")

    divisore_str = divisore_str_da_parametro_get(divisore_raw)
    fiscal_kw = kwargs_percorso_fiscale_sim(percorso_fiscale)
    residenza_kw = _kwargs_residenza_motore_da_dipendente(dip)

    kwargs_calcolo = {
        "parametro_ccnl": parametro,
        "tipo_contratto": tc,
        "anno": anno,
        "mese": mese,
        "azienda": az,
        "data_inizio_rapporto": data_inizio_eff,
        "data_fine_rapporto": data_fine_eff,
        "divisore_str": divisore_str,
        "superminimo": superminimo_eff,
        "indennita_turno": indennita_turno,
        "scatto_anzianita": scatto,
        "indennita_extra": premio_extra,
        "ore_straord_diurno": _d(cal_m.get("ore_straord_diurno", 0)),
        "ore_straord_notturno": _d(cal_m.get("ore_straord_notturno", 0)),
        "ore_straord_festivo": _d(cal_m.get("ore_straord_festivo", 0)),
        "ore_straord_domenica": _d(cal_m.get("ore_straord_domenica", 0)),
        "ore_straord_nott_fest": _d(cal_m.get("ore_straord_nott_fest", 0)),
        "ore_ordinarie_retribuite": ore_ord,
        "ore_domenicali": _d(cal_m.get("ore_domenicali", 0)),
        "ore_festivi": _d(cal_m.get("giorni_festivi", 0)),
        "giorni_assenza_ingiust": _d(cal_m.get("giorni_assenza", 0)),
        "trattenute_extra_mese": _d(cal_m.get("trattenute_extra_mese", 0)),
        "competenze_extra_non_imponibili": _d(cal_m.get("competenze_extra_non_imponibili", 0)),
        "modalita_ore_effettive": ore_ord > 0,
        "auto_ore_domenicali_da_calendario": not (ore_ord > 0),
        "ccnl_obj": ccnl,
        "contratto_esclude_tredicesima": bool(rapporto is not None and rapporto.tredicesima is False),
        "contratto_esclude_quattordicesima": bool(
            rapporto is not None and rapporto.quattordicesima is False
        ),
        "rateo_13_mensile_in_imponibile": bool(
            rapporto is not None and getattr(rapporto, "tredicesima_rateo_mensile_in_imponibile", False)
        ),
        "rateo_14_mensile_in_imponibile": bool(
            rapporto is not None and getattr(rapporto, "quattordicesima_rateo_mensile_in_imponibile", False)
        ),
        **residenza_kw,
        **fiscal_kw,
    }

    return {
        "ok": True,
        "fonte_parametro_ccnl": fonte_parametro,
        "divisore_str": divisore_str,
        "percorso_fiscale": percorso_fiscale or "standard",
        "kwargs_calcolo": kwargs_calcolo,
        "rapporto_id": getattr(rapporto, "pk", None),
        "ruolo_organico_id": getattr(ruolo, "pk", None),
        "livello_eff": livello_eff,
        "usa_calendario_ruolo_organico": usa_calendario_ruolo_organico_in_conciliazione(anno, mese),
        "modalita_griglia_ruolo": modalita_griglia_ruolo,
    }


def confronto_cedolino_motore_paga(
    v4: CedolinoMotoreV4,
    *,
    divisore_raw: str | None = None,
    percorso_fiscale: str | None = "ced_l207_det",
    allinea_input_a_cedolino: bool = True,
) -> dict[str, Any]:
    """
    Esegue il motore paga e confronta voci (via mappatura) e totali lordo/netto con ``v4``.

    Returns:
        ``{'ok': bool, 'errore': str|None, ...}`` con chiavi ``righe_voci``, ``voci_non_mappate``,
        ``totali``, ``meta`` se ok.

    Con ``allinea_input_a_cedolino=True`` (default), i kwargs del motore busta paga vengono avvicinati
    ai dati estratti sul cedolino v4 (ROF, ore da 8001/801x, trattenute 925x, addiz. 1800/1802, quota L207
    da 9824). La riga TI del motore non viene mostrata se il cedolino non ha voce omologa (TI spesso solo
    a credito / in detrazioni in TS).
    """
    ctx = risolvi_contesto_calcolo_motore_paga(
        v4,
        divisore_raw=divisore_raw,
        percorso_fiscale=percorso_fiscale,
    )
    if not ctx.get("ok"):
        return ctx

    from rapporto_di_lavoro.services_simulazione import invoca_calcola_busta_paga_mese

    voci_qs_list = (
        list(VoceCedolinoMotoreV4.objects.filter(cedolino=v4))
        if v4.pk
        else []
    )
    if allinea_input_a_cedolino:
        kwargs_motore, meta_allinea = allinea_kwargs_calcolo_a_dati_cedolino_v4(
            v4,
            ctx["kwargs_calcolo"],
            voci_prefetched=voci_qs_list,
        )
    else:
        kwargs_motore = dict(ctx["kwargs_calcolo"])
        meta_allinea = {"attivo": False, "chiavi": []}

    try:
        sim = invoca_calcola_busta_paga_mese(
            log_prefix="ADMIN_CEDOLINO_V4_MOTORE_PAGA",
            **kwargs_motore,
        )
    except Exception:
        logger.exception(
            "confronto_cedolino_motore_paga: calcolo fallito (cedolino_id=%s)",
            getattr(v4, "pk", None),
        )
        return {
            "ok": False,
            "errore": "Errore durante calcola_busta_paga_mese (vedi log server).",
        }

    mappature = _carica_mappature_attive()
    ced_by_motor: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    non_mappate: list[dict[str, Any]] = []

    for voce in voci_qs_list:
        mc = _codice_motore_per_voce_cedolino(voce, mappature)
        imp = _d(voce.importo)
        if mc:
            ced_by_motor[mc] = (ced_by_motor[mc] + imp).quantize(Q2)
        else:
            non_mappate.append(
                {
                    "codice": voce.codice,
                    "descrizione": voce.descrizione,
                    "tipo": voce.tipo,
                    "importo": imp,
                }
            )

    mot_map: dict[str, Decimal] = {}
    descr_mot: dict[str, str] = {}
    for row in sim.get("voci_classificate") or []:
        c = str(row.get("codice") or "").strip()
        if not c:
            continue
        mot_map[c] = _d(row.get("importo"))
        descr_mot[c] = str(row.get("descrizione") or "")

    # Riga TS 8001: competenza unica in cedolino vs ordinario a ore o somma tabellare nel motore
    if ced_by_motor.get(_CED_TS8001_COMPOSITO):
        imp_o = _d(sim.get("imp_ordinario_ore") or 0)
        ore_o = _d(sim.get("ore_ordinarie_retribuite") or 0)
        if sim.get("modalita_ore_effettive") and ore_o > 0 and imp_o > 0:
            mot_map[_CED_TS8001_COMPOSITO] = imp_o
            descr_mot[_CED_TS8001_COMPOSITO] = "Importo ordinario (ore × ROF; ced. 8001)"
        else:
            somma = sum((mot_map.get(c) or Decimal("0")) for c in _CODICI_MOTORE_COMPOSITO_TS8001).quantize(Q2)
            mot_map[_CED_TS8001_COMPOSITO] = somma
            descr_mot[_CED_TS8001_COMPOSITO] = "Somma voci tabellari motore (ced. 8001 ordinario)"
        for c in _CODICI_MOTORE_COMPOSITO_TS8001:
            mot_map.pop(c, None)
            descr_mot.pop(c, None)

    if ced_by_motor.get(_CED_ADDIZ_REGIONALE):
        mot_map[_CED_ADDIZ_REGIONALE] = _d(sim.get("add_reg_m"))
        descr_mot[_CED_ADDIZ_REGIONALE] = "Addizionale regionale IRPEF (mese, motore)"

    if ced_by_motor.get(_CED_ADDIZ_COMUNALE):
        mot_map[_CED_ADDIZ_COMUNALE] = _d(sim.get("add_com_m"))
        descr_mot[_CED_ADDIZ_COMUNALE] = "Addizionale comunale IRPEF (mese, motore)"

    if ced_by_motor.get(_CED_TRATTENUTE_EXTRA):
        mot_map[_CED_TRATTENUTE_EXTRA] = _d(sim.get("trattenute_extra_mese"))
        descr_mot[_CED_TRATTENUTE_EXTRA] = "Trattenute extra mese (motore; es. pignoramento da ruolo organico)"

    tutti_codici = sorted(set(mot_map.keys()) | set(ced_by_motor.keys()))
    righe_voci: list[dict[str, Any]] = []
    for cod in tutti_codici:
        im = mot_map.get(cod, Decimal("0"))
        ic = ced_by_motor.get(cod, Decimal("0"))
        delta = (im - ic).quantize(Q2)
        ok = abs(delta) <= TOLLERANZA_CONFRONTO_EURO
        if im == Decimal("0") and ic == Decimal("0"):
            continue
        righe_voci.append(
            {
                "codice_motore": cod,
                "descrizione": descr_mot.get(cod, cod),
                "importo_motore": im,
                "importo_cedolino": ic,
                "delta": delta,
                "ok": bool(ok),
            }
        )

    # Il TI spesso non compare come competenza numerata in TS: nascondi la riga se il cedolino non ha voce.
    righe_voci = [
        r
        for r in righe_voci
        if not (
            r.get("codice_motore") == "TI_DL3_2020"
            and _d(r.get("importo_cedolino")) == Decimal("0")
        )
    ]

    lordo_m = _d(sim.get("lordo_mensile"))
    netto_m = _d(sim.get("netto_totale"))
    lordo_c = _d(v4.totale_lordo)
    netto_c = _d(v4.netto_busta)

    return {
        "ok": True,
        "errore": None,
        "meta": {
            "fonte_parametro_ccnl": ctx["fonte_parametro_ccnl"],
            "divisore_str": ctx["divisore_str"],
            "percorso_fiscale": ctx["percorso_fiscale"],
            "rapporto_id": ctx.get("rapporto_id"),
            "ruolo_organico_id": ctx.get("ruolo_organico_id"),
            "livello_eff": ctx.get("livello_eff"),
            "usa_calendario_ruolo_organico": ctx.get("usa_calendario_ruolo_organico"),
            "modalita_griglia_ruolo": ctx.get("modalita_griglia_ruolo"),
            "allineamento_input_cedolino_v4": meta_allinea,
        },
        "righe_voci": righe_voci,
        "voci_non_mappate": non_mappate,
        "totali": {
            "lordo_motore": lordo_m,
            "lordo_cedolino": lordo_c,
            "lordo_delta": (lordo_m - lordo_c).quantize(Q2),
            "lordo_ok": abs(lordo_m - lordo_c) <= TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
            "netto_motore": netto_m,
            "netto_cedolino": netto_c,
            "netto_delta": (netto_m - netto_c).quantize(Q2),
            "netto_ok": abs(netto_m - netto_c) <= TOLLERANZA_FORMULE_EURO,
        },
    }


def render_confronto_motore_paga_html(data: dict[str, Any]) -> str:
    """HTML compatto per campo readonly admin."""
    if not data.get("ok"):
        msg = escape(str(data.get("errore") or "Dati non disponibili."))
        return mark_safe(f'<p class="help">{msg}</p>')

    meta = data.get("meta") or {}
    tot = data.get("totali") or {}
    righe = data.get("righe_voci") or []
    nm = data.get("voci_non_mappate") or []

    al = meta.get("allineamento_input_cedolino_v4") or {}
    meta_bits = [
        f"CCNL: <code>{escape(str(meta.get('fonte_parametro_ccnl', '—')))}</code>",
        f"divisore <code>{escape(str(meta.get('divisore_str', '—')))}</code>",
        f"fiscale <code>{escape(str(meta.get('percorso_fiscale', '—')))}</code>",
        f"livello <code>{escape(str(meta.get('livello_eff', '—')))}</code>",
    ]
    if isinstance(al, dict) and al.get("attivo") and al.get("chiavi"):
        meta_bits.append(
            "allinea input cedolino: <code>"
            + escape(", ".join(str(x) for x in al["chiavi"]))
            + "</code>"
        )
    mgr = meta.get("modalita_griglia_ruolo") or ""
    if meta.get("usa_calendario_ruolo_organico") is True:
        meta_bits.append(
            "griglia mensile ruolo organico: <strong>usata</strong> "
            "(dal 01/03/2026 inclusivo; presenze considerate allineabili al consulente)"
        )
    elif mgr == "omessa_cedolino_rof":
        meta_bits.append(
            "griglia mensile ruolo organico: <strong>omessa</strong> "
            "(pre 01/03/2026; ROF su cedolino v4 — ore da PDF / allineamento, non da simulazione organico)"
        )
    elif mgr == "fallback_senza_rof":
        meta_bits.append(
            "griglia mensile ruolo organico: <strong>fallback</strong> "
            "(pre 01/03/2026 ma ROF assente su v4 — si riusa la griglia storica per evitare motore a ore zero)"
        )
    elif meta.get("usa_calendario_ruolo_organico") is False and mgr == "nessuna":
        meta_bits.append(
            "griglia mensile ruolo organico: <strong>non disponibile</strong> (nessun dato mese su ruolo organico)"
        )
    head = "<p class='help'>" + " · ".join(meta_bits) + "</p>"

    def row_t(ok: bool, cells: list[str]) -> str:
        cls = "motpaga-ok" if ok else "motpaga-ko"
        tds = "".join(f"<td>{c}</td>" for c in cells)
        return f"<tr class='{cls}'>{tds}</tr>"

    tot_rows = ""
    for label, km, kc, kd, ko in (
        (
            "Lordo mensile",
            "lordo_motore",
            "lordo_cedolino",
            "lordo_delta",
            "lordo_ok",
        ),
        (
            "Netto totale",
            "netto_motore",
            "netto_cedolino",
            "netto_delta",
            "netto_ok",
        ),
    ):
        ok = bool(tot.get(ko))
        tot_rows += row_t(
            ok,
            [
                escape(label),
                _fmt_eur_it(_d(tot.get(km))),
                _fmt_eur_it(_d(tot.get(kc))),
                _fmt_eur_it(_d(tot.get(kd))),
                "OK" if ok else "Δ",
            ],
        )

    voc_rows = ""
    for r in righe:
        ok = r.get("ok")
        voc_rows += row_t(
            bool(ok),
            [
                f"<code>{escape(str(r.get('codice_motore', '')))}</code>",
                escape(str(r.get("descrizione", ""))[:80]),
                _fmt_eur_it(_d(r.get("importo_motore"))),
                _fmt_eur_it(_d(r.get("importo_cedolino"))),
                _fmt_eur_it(_d(r.get("delta"))),
                "OK" if ok else "Δ",
            ],
        )

    nm_html = ""
    if nm:
        lis = "".join(
            f"<li><code>{escape(str(x.get('codice')))}</code> "
            f"{escape(str(x.get('descrizione', ''))[:120])} — "
            f"{_fmt_eur_it(_d(x.get('importo')))} € "
            f"({escape(str(x.get('tipo', '')))})</li>"
            for x in nm[:40]
        )
        extra = f" … (+{len(nm) - 40} altre)" if len(nm) > 40 else ""
        nm_html = (
            f"<p class='help'><strong>Righe cedolino senza mappatura motore</strong> "
            f"(configura <em>Mappatura voce motore paga</em> in admin rapporto_di_lavoro):</p>"
            f"<ul class='messagelist'>{lis}</ul>{escape(extra)}"
        )

    style = """
<style>
.motpaga-wrap table { border-collapse: collapse; width: 100%; max-width: 960px; margin: 0.5em 0; }
.motpaga-wrap th, .motpaga-wrap td { border: 1px solid #ccc; padding: 4px 8px; font-size: 12px; text-align: right; }
.motpaga-wrap th:first-child, .motpaga-wrap td:first-child { text-align: left; }
.motpaga-wrap tr.motpaga-ok { background: #e8f5e9; }
.motpaga-wrap tr.motpaga-ko { background: #fff3e0; }
</style>
"""
    table_tot = (
        "<h3>Totali (motore vs cedolino v4)</h3>"
        "<table><thead><tr>"
        "<th></th><th>Motore</th><th>Cedolino v4</th><th>Δ (motore − ced.)</th><th></th>"
        f"</tr></thead><tbody>{tot_rows}</tbody></table>"
    )
    table_voc = (
        "<h3>Voci classificate motore vs somma cedolino (per codice)</h3>"
        "<table><thead><tr>"
        "<th>Codice</th><th>Descrizione</th><th>Motore €</th><th>Cedolino €</th><th>Δ €</th><th></th>"
        f"</tr></thead><tbody>{voc_rows}</tbody></table>"
    )

    return mark_safe(
        f"{style}<div class='motpaga-wrap'>{head}{table_tot}{table_voc}{nm_html}</div>"
    )


def confronto_cedolino_motore_paga_html(v4: CedolinoMotoreV4 | None) -> str:
    if v4 is None or not v4.pk:
        return mark_safe('<p class="help">Salva il cedolino per calcolare il confronto.</p>')
    data = confronto_cedolino_motore_paga(v4)
    return render_confronto_motore_paga_html(data)
