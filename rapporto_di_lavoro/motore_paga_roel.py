"""
Logica ROEL (Retribuzione oraria effettiva lorda) e rubrica competenze — riferimento unico per simulatore busta.

Obiettivo: allineare calcolo e presentazione alla catena contratto + calendario descritta sotto.
Il motore numerico resta in ``utils_motore_paga.calcola_busta_paga_mese``; qui costruiamo la **vista logica**
(righe tabella, testi) a partire dal dizionario risultato già calcolato.

────────────────────────────────────────────────────────────────────────────
1) Voci orarie da parametro CCNL (divisore 172 o 173,33; pro-rata giorni mese se attiva)
   • Paga base oraria     = numeratore tabellare paga (minimo tabellare se valorizzato, altrimenti
     paga base mensile FT) × frazione ÷ divisore
   • Contingenza oraria   = contingenza tabellare × frazione ÷ divisore
   • Scatto anzianità     = importo scatto tabellare/contratto × frazione ÷ divisore
   • FIPE (2024+): EDR storico **assorbito in contingenza** — non voce distinta; altri CCNL possono avere EDR/indennità
     come voci **mensili** (€/h tab. eventualmente mostrate ma **non** nella ROEL = paga+cont+scatto).
   • Superminimo e indennità turno restano **voci mensili** in busta (non nella ROEL /172).

2) ROEL tabellare = **solo** €/h paga base + contingenza + scatto (somma al divisore 172 o 173,33).
   (``retribuzione_oraria_di_fatto`` / ``paga_oraria`` con divisore orario; EDR/indennità fuori da questa somma.)

3) Competenze (prima parte cedolino) — modello righe:
   • Lavoro ordinario: con modalità ore effettive = ore inserite × ROEL; altrimenti **gg lav. calendario × ore/gg × ROEL**
     (ore effettive del mese sui giorni lavorativi), non la sola somma mensile tabellare.
   • Lavoro domenicale/festivo: ore × ROEL × maggiorazione (solo maggiorazione, salvo flag compenso completo).
   • Straordinari: ore × ROEL × (1 + maggiorazione).
   • Ratei 13ª/14ª, L207, TI, trattenute/addizionali: fasi successive (INPS, IRPEF) come nel motore.

4) Imponibile INPS = somma competenze imponibili + eventuali ratei in busta; poi INPS, IRPEF.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def roel_tabellare_euro_oraria(r: dict[str, Any]) -> Decimal:
    """
    ROEL tabellare (€/h) = (paga base tab. × frazione ÷ divisore) + (contingenza × frazione ÷ divisore)
    + (scatto × frazione ÷ divisore), con divisore 172 o 173,33.

    Con divisore orario **non** si usa mai ``retribuzione_oraria_di_fatto`` come fallback: quel campo in
    contesti legacy o dict parziali poteva restare la vecchia «ROF» (paga+cont+EDR+ind+scatto) e mostrava
    ad es. 9,7444 invece di 9,1651.
    """
    q = Decimal('0.0001')
    div_raw = r.get('divisore')
    try:
        d = div_raw if isinstance(div_raw, Decimal) else Decimal(str(div_raw))
    except Exception:
        d = Decimal('0')

    def _h(key: str) -> Decimal:
        v = r.get(key)
        if v is None:
            return Decimal('0')
        try:
            return Decimal(str(v)).quantize(q)
        except Exception:
            return Decimal('0')

    if d > Decimal('30'):
        return (
            _h('oraria_tabellare_paga_base')
            + _h('oraria_tabellare_contingenza')
            + _h('oraria_tabellare_scatto')
        ).quantize(q)
    fb = r.get('retribuzione_oraria_di_fatto')
    if fb is None:
        return Decimal('0')
    try:
        return Decimal(str(fb)).quantize(q)
    except Exception:
        return Decimal('0')


def _roel_tabellare_da_dict(r: dict[str, Any]) -> Decimal:
    """Alias interno per compatibilità."""
    return roel_tabellare_euro_oraria(r)


def costruisci_competenze_logica_v1(r: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Righe descrittive per tabella «competenze» nel simulatore (solo lettura del risultato motore).
    Per la ROEL tabellare somma esplicitamente paga base + contingenza + scatto €/h (campi ``oraria_tabellare_*``);
    le altre righe usano gli importi già calcolati dal motore.
    """
    div = r.get('divisore') or Decimal('0')
    if not isinstance(div, Decimal):
        try:
            div = Decimal(str(div))
        except Exception:
            div = Decimal('0')
    if div <= Decimal('30'):
        return []

    roel = _roel_tabellare_da_dict(r)
    ore_ord = r.get('ore_ordinarie_retribuite') or Decimal('0')
    modalita = bool(r.get('modalita_ore_effettive'))
    gg_lav = r.get('cal_giorni_lavorativi') or 0
    gg_ord_cal = r.get('cal_giorni_ordinari')
    ore_mens = r.get('ore_mensili') or Decimal('0')
    ore_gg = r.get('ore_giornaliere') or Decimal('0')

    rows: list[dict[str, Any]] = []

    def _s(v) -> str:
        if v is None:
            return '—'
        try:
            return str(Decimal(str(v)).quantize(Decimal('0.0001')))
        except Exception:
            return str(v)

    parts = [f"paga base {_s(r.get('oraria_tabellare_paga_base'))}", f"contingenza {_s(r.get('oraria_tabellare_contingenza'))}"]
    if r.get('oraria_tabellare_scatto'):
        parts.append(f"scatto {_s(r.get('oraria_tabellare_scatto'))}")
    extra_note = []
    if r.get('oraria_tabellare_edr'):
        extra_note.append(f"EDR tab. {_s(r.get('oraria_tabellare_edr'))} €/h — voce mensile, non in ROEL")
    if r.get('oraria_tabellare_indennita'):
        extra_note.append(f"ind. tab. {_s(r.get('oraria_tabellare_indennita'))} €/h — fuori ROEL")
    nota_comp = 'ROEL = somma: ' + ' + '.join(parts)
    if extra_note:
        nota_comp += '. ' + ' '.join(extra_note)
    rows.append({
        'cod': '—',
        'descrizione': 'Componenti ROEL (÷ divisore)',
        'ore_o_gg': '—',
        'base': None,
        'competenze': None,
        'trattenute': None,
        'nota': nota_comp,
    })
    rows.append({
        'cod': 'ROEL',
        'descrizione': 'Retribuzione oraria effettiva lorda (tabellare)',
        'ore_o_gg': '—',
        'base': roel,
        'competenze': None,
        'trattenute': None,
        'nota': 'Solo paga + contingenza + scatto (€/h); base per straord. e maggiorazioni domenica/festivo.',
    })

    if modalita and ore_ord and ore_ord > 0:
        imp_o = r.get('imp_ordinario_ore')
        rows.append({
            'cod': '1',
            'descrizione': 'Lavoro ordinario',
            'ore_o_gg': ore_ord,
            'base': roel,
            'competenze': imp_o,
            'trattenute': None,
            'nota': 'Ore effettive × ROEL tabellare (presenze / campo ore lav. ord.).',
        })
    else:
        # Ore ordinario su calendario: giorni «ordinari» (no dom., no festivi, no chiusure; sabato solo se 6/7 gg/sett.)
        try:
            n_gg = int(gg_ord_cal) if gg_ord_cal is not None else int(gg_lav)
        except (TypeError, ValueError):
            n_gg = 0
        ore_cal = (
            (Decimal(str(n_gg)) * Decimal(str(ore_gg))).quantize(Decimal('0.01'))
            if n_gg > 0 and Decimal(str(ore_gg)) > 0
            else Decimal('0')
        )
        if ore_cal > 0:
            comp_ord = (ore_cal * roel).quantize(Decimal('0.01'))
            rows.append({
                'cod': '1',
                'descrizione': 'Lavoro ordinario (gg ord. calendario × h/gg × ROEL)',
                'ore_o_gg': ore_cal,
                'base': roel,
                'competenze': comp_ord,
                'trattenute': None,
                'nota': (
                    f'{n_gg} gg ordinari (calendario: escl. domeniche, festivi e chiusure; '
                    f'sabato solo con contratto 6/7 gg/sett.) × {ore_gg} h/gg. '
                    f'Rif. contratto {ore_mens} h/mese. EDR/indennità/superminimo in altre righe se presenti.'
                ),
            })
        else:
            comp_ord = (
                Decimal(str(r.get('paga_base') or 0))
                + Decimal(str(r.get('contingenza') or 0))
                + Decimal(str(r.get('scatto') or 0))
                + Decimal(str(r.get('edr') or 0))
                + Decimal(str(r.get('indennita') or 0))
            ).quantize(Decimal('0.01'))
            rows.append({
                'cod': '1',
                'descrizione': 'Lavoro ordinario (mensilità tabellare in busta)',
                'ore_o_gg': f"{gg_lav} gg lav. · ref. {ore_mens} h/mese",
                'base': roel,
                'competenze': comp_ord,
                'trattenute': None,
                'nota': 'Calendario senza ore/gg: somma voci tabellari mensili in busta.',
            })

    if r.get('ore_domenicali') and Decimal(str(r['ore_domenicali'])) > 0:
        comp_full = bool(r.get('domenicale_compenso_completo'))
        magg_pct = Decimal(str(r.get('magg_dom_pct', 15))) / Decimal('100')
        base_dom = (roel * (Decimal('1') + magg_pct)).quantize(Decimal('0.0001')) if comp_full else roel
        rows.append({
            'cod': '4',
            'descrizione': 'Lavoro domenicale',
            'ore_o_gg': r['ore_domenicali'],
            'base': base_dom,
            'competenze': r.get('imp_dom_magg'),
            'trattenute': None,
            'nota': (
                'Compenso completo ore × ROEL × (1 + magg.)' if comp_full else 'Solo maggiorazione: ore × ROEL × magg. %'
            ),
        })
    if r.get('ore_festivi') and Decimal(str(r['ore_festivi'])) > 0:
        rows.append({
            'cod': '4b',
            'descrizione': 'Lavoro festivo',
            'ore_o_gg': r['ore_festivi'],
            'base': roel,
            'competenze': r.get('imp_fest_magg'),
            'trattenute': None,
            'nota': 'Ore × ROEL × maggiorazione festivo.',
        })

    if r.get('superminimo') and Decimal(str(r['superminimo'])) > 0:
        rows.append({
            'cod': '—',
            'descrizione': 'Superminimo',
            'ore_o_gg': '—',
            'base': None,
            'competenze': r['superminimo'],
            'trattenute': None,
            'nota': 'Voce mensile contrattuale (non inclusa nella ROEL tabellare /172).',
        })
    if r.get('indennita_turno') and Decimal(str(r['indennita_turno'])) > 0:
        rows.append({
            'cod': '—',
            'descrizione': 'Indennità turno',
            'ore_o_gg': '—',
            'base': None,
            'competenze': r['indennita_turno'],
            'trattenute': None,
            'nota': 'Voce mensile contrattuale.',
        })
    if r.get('indennita_extra') and Decimal(str(r['indennita_extra'])) > 0:
        rows.append({
            'cod': '—',
            'descrizione': 'Indennità extra / altre indennità contrattuali',
            'ore_o_gg': '—',
            'base': None,
            'competenze': r['indennita_extra'],
            'trattenute': None,
            'nota': 'Voce mensile (non nella ROEL tabellare /172).',
        })

    tot_s = r.get('tot_straord')
    if tot_s and Decimal(str(tot_s)) > 0:
        ore_s = (
            Decimal(str(r.get('ore_straord_diurno') or 0))
            + Decimal(str(r.get('ore_straord_notturno') or 0))
            + Decimal(str(r.get('ore_straord_festivo') or 0))
            + Decimal(str(r.get('ore_straord_nott_fest') or 0))
            + Decimal(str(r.get('ore_straord_domenica') or 0))
        )
        rows.append({
            'cod': 'S',
            'descrizione': 'Straordinari (somma tipologie)',
            'ore_o_gg': f"{ore_s} h",
            'base': roel,
            'competenze': tot_s,
            'trattenute': None,
            'nota': 'Ore × ROEL × (1 + magg. CCNL/parametri) per diurno, notturno, festivo, n+f, domenica.',
        })

    l207 = r.get('l207')
    if l207 and Decimal(str(l207)) != 0:
        rows.append({
            'cod': '6',
            'descrizione': 'Somma art. 1 c.4 L. 207/2024 (bonus in cedolino)',
            'ore_o_gg': '—',
            'base': None,
            'competenze': l207,
            'trattenute': None,
            'nota': 'Come da motore fiscale (può essere detrazione IRPEF o credito netto).',
        })

    ti = r.get('ti')
    if ti and Decimal(str(ti)) != 0:
        rows.append({
            'cod': '5',
            'descrizione': 'Trattamento integrativo (DL 3/2020)',
            'ore_o_gg': '—',
            'base': None,
            'competenze': ti,
            'trattenute': None,
            'nota': '',
        })

    if r.get('rat13_m') and Decimal(str(r['rat13_m'])) > 0:
        rows.append({
            'cod': '7',
            'descrizione': 'Rateo 13ª mensile',
            'ore_o_gg': '—',
            'base': None,
            'competenze': r['rat13_m'],
            'trattenute': None,
            'nota': 'In imponibile INPS' if r.get('rateo_13_mensile_in_imponibile') else 'Fuori imponibile mensile (accantonamento)',
        })
    if r.get('rat14_m') and Decimal(str(r['rat14_m'])) > 0:
        rows.append({
            'cod': '7',
            'descrizione': 'Rateo 14ª mensile',
            'ore_o_gg': '—',
            'base': None,
            'competenze': r['rat14_m'],
            'trattenute': None,
            'nota': 'In imponibile INPS' if r.get('rateo_14_mensile_in_imponibile') else 'Fuori imponibile mensile (accantonamento)',
        })

    add_r = r.get('add_reg_m')
    add_c = r.get('add_com_m')
    if (add_r and Decimal(str(add_r)) != 0) or (add_c and Decimal(str(add_c)) != 0):
        rows.append({
            'cod': '8',
            'descrizione': 'Addizionali regionali e comunali (cedolino)',
            'ore_o_gg': '—',
            'base': None,
            'competenze': None,
            'trattenute': (Decimal(str(add_r or 0)) + Decimal(str(add_c or 0))).quantize(Decimal('0.01')),
            'nota': f"Reg. € {add_r or 0} · Com. € {add_c or 0}",
        })

    tex = r.get('trattenute_extra_mese')
    if tex and Decimal(str(tex)) != 0:
        rows.append({
            'cod': '10',
            'descrizione': 'Altre trattenute (scenario)',
            'ore_o_gg': '—',
            'base': None,
            'competenze': None,
            'trattenute': tex,
            'nota': '',
        })

    rows.append({
        'cod': 'Σ',
        'descrizione': 'Lordo competenze mese (rubrica motore)',
        'ore_o_gg': '—',
        'base': None,
        'competenze': r.get('lordo_mensile'),
        'trattenute': None,
        'nota': 'Include straordinari e maggiorazioni oltre alle righe sopra, come da ``calcola_busta_paga_mese``.',
    })
    return rows
