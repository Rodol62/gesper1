"""
Riconciliazione cedolino (CedolinoMotoreV4 + voci) vs motore busta paga canonico
(:func:`rapporto_di_lavoro.services_simulazione.invoca_calcola_busta_paga_mese`).

Usa ``MappaturaVoceMotore`` (codice_voce + etichetta_riconciliazione) per aggregare
le righe cedolino TeamSystem sulle stesse chiavi del motore.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape
from typing import Any

from django.utils.safestring import mark_safe

from documenti.cedolini_tolleranze import TOLLERANZA_CONFRONTO_EURO, TOLLERANZA_FORMULE_EURO
from documenti.models import CedolinoMotoreV4, VoceCedolinoMotoreV4

logger = logging.getLogger(__name__)

Q2 = Decimal("0.01")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Q2)
    except Exception:
        return Decimal("0.00")


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


def risolvi_contesto_calcolo_motore_paga(
    v4: CedolinoMotoreV4,
    *,
    divisore_raw: str | None = None,
    percorso_fiscale: str | None = "ced_l207_det",
) -> dict[str, Any]:
    """
    Prepara kwargs per ``invoca_calcola_busta_paga_mese`` da dipendente + periodo cedolino v4.

    Allineamento a ``presenze.views._build_rows_scostamento_fiscale`` dove possibile
    (ruolo organico, calendario mensile, scatti tabellari).
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
    if ruolo is not None:
        raw_cal = ruolo.calendario_mensile or {}
        cal_m = raw_cal.get(str(mese), raw_cal.get(mese, {})) or {}

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
    }


def confronto_cedolino_motore_paga(
    v4: CedolinoMotoreV4,
    *,
    divisore_raw: str | None = None,
    percorso_fiscale: str | None = "ced_l207_det",
) -> dict[str, Any]:
    """
    Esegue il motore paga e confronta voci (via mappatura) e totali lordo/netto con ``v4``.

    Returns:
        ``{'ok': bool, 'errore': str|None, ...}`` con chiavi ``righe_voci``, ``voci_non_mappate``,
        ``totali``, ``meta`` se ok.
    """
    ctx = risolvi_contesto_calcolo_motore_paga(
        v4,
        divisore_raw=divisore_raw,
        percorso_fiscale=percorso_fiscale,
    )
    if not ctx.get("ok"):
        return ctx

    from rapporto_di_lavoro.services_simulazione import invoca_calcola_busta_paga_mese

    try:
        sim = invoca_calcola_busta_paga_mese(
            log_prefix="ADMIN_CEDOLINO_V4_MOTORE_PAGA",
            **ctx["kwargs_calcolo"],
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

    voci_qs = (
        VoceCedolinoMotoreV4.objects.filter(cedolino=v4)
        if v4.pk
        else VoceCedolinoMotoreV4.objects.none()
    )
    for voce in voci_qs:
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
        },
        "righe_voci": righe_voci,
        "voci_non_mappate": non_mappate,
        "totali": {
            "lordo_motore": lordo_m,
            "lordo_cedolino": lordo_c,
            "lordo_delta": (lordo_m - lordo_c).quantize(Q2),
            "lordo_ok": abs(lordo_m - lordo_c) <= TOLLERANZA_FORMULE_EURO,
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

    meta_bits = [
        f"CCNL: <code>{escape(str(meta.get('fonte_parametro_ccnl', '—')))}</code>",
        f"divisore <code>{escape(str(meta.get('divisore_str', '—')))}</code>",
        f"fiscale <code>{escape(str(meta.get('percorso_fiscale', '—')))}</code>",
        f"livello <code>{escape(str(meta.get('livello_eff', '—')))}</code>",
    ]
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
