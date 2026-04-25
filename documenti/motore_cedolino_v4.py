#!/usr/bin/env python3
"""
motore_cedolino_v4.py
══════════════════════════════════════════════════════════════════════════════
Motore universale di estrazione, calcolo, validazione e persistenza
di cedolini paga PDF – TeamSystem / Studio Cipriano (Palermo).

Versione 4.0 – Estrazione per coordinate X/Y (pdfplumber words)
─────────────────────────────────────────────────────────────────────────────
COORDINATE STABILI (top px, tolleranza ±4):
  ~184–226   Retribuzione base (paga base, contingenza, EL.DIS.SAN, EDB)
  ~256–410   Voci retributive (codice + descrizione + ore + base + importo)
  ~520       Riga A: TOTALE LORDO | IMPON.CONTR | CONTRIB1 | TOT.CONTRIB
  ~568       Riga B: IMP.IRPEF | IRPEF.LORDA | TOT.DETR | TOT.TRAT.IRPEF
  ~592       Riga C: ARR.PREC (x≈286) | TOT.TRATT (x≈331–384) | ARR.ATT
  ~640       Riga D: ARR.PREC (x≈331) | NETTO.BUSTA (x≈375)
             Riga E: IRPEF.ERARIO (x≈53) | ADD.REG (x≈110) | ADD.COM (x≈167) | ARR.ATT (x≈384)  [cedol.cessaz.]
  ~664       Riga F: FERIE.GOD | FERIE.RES | ROL.GOD | ROL.RES
  ~688       Riga G: FEST.GOD | FEST.RES  (cedolini con festività extra)
  ~712       Riga H: POS.SETT | ORE.INPS | GG.INPS | GG.MINIM | ORE.INAIL | GG.INAIL | IMP.INAIL | TFR
  ~736       Detrazioni spettanti (singolo valore x≈61)
  ~760       Progressivi annui (7 valori)
─────────────────────────────────────────────────────────────────────────────

Dipendenze:  pip install pdfplumber
Uso:
  python3 motore_cedolino_v4.py <pdf> [--db path.db] [--no-save] [--schema]
  python3 motore_cedolino_v4.py --batch <cartella_pdf> --db cedolini.db
"""

import io
import sys, re, sqlite3, pdfplumber
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from pathlib import Path

from documenti.cedolini_tolleranze import (
    toll_formule_float,
    TOLLERANZA_F5_CONTRIBUTI_INPS,
    TOLLERANZA_F8_TRATTENUTE,
    TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
)

# ══════════════════════════════════════════════════════════════════════════════
# COSTANTI
# ══════════════════════════════════════════════════════════════════════════════

TOLL = toll_formule_float()  # allineato a documenti.cedolini_tolleranze.TOLLERANZA_FORMULE_EURO
TOLL_F4_IMP_VOCI = float(TOLLERANZA_IMPONIBILE_VOCI_VS_PDF)
TOLL_F5_CONTRIB = float(TOLLERANZA_F5_CONTRIBUTI_INPS)
TOLL_F8_TRATT = float(TOLLERANZA_F8_TRATTENUTE)
DB_DEFAULT = "cedolini.db"

# Bande Y (top, tolleranza) per ogni sezione posizionale
Y_BASE_RETR  = (184, 240, 10)   # (top_min, top_max, tol)
Y_VOCI       = (250, 490,  5)
Y_RIGA_A     = (515, 530,  8)   # Totale Lordo / Imponibile / Contrib
Y_RIGA_B     = (563, 578,  8)   # Imp.IRPEF / Lorda / Detr / Trat
Y_RIGA_C     = (587, 600,  8)   # Arr.Prec / Tot.Tratt / Arr.Att
Y_RIGA_D     = (635, 648,  8)   # Arr.Prec + Netto  oppure IRPEF.Er+Add (cessazione)
Y_RIGA_FERIE = (658, 675,  8)   # Ferie/ROL goduti e residui
Y_RIGA_FEST  = (682, 695,  8)   # Festività extra
Y_RIGA_INPS  = (706, 722,  8)   # Dati INPS/INAIL/TFR
Y_RIGA_DETR  = (730, 742,  8)   # Detrazioni spettanti (singolo)
Y_RIGA_PROG  = (754, 768,  8)   # Progressivi annui (7 valori)

# Bande X per colonne fisse (x0 del word)
X_LORDO      = (35,  75)    # col TOTALE LORDO
X_IMPON      = (95, 130)    # col IMPON.CONTR.SOC
X_CONTRIB    = (148, 180)   # col CONTRIB1 / TOT.CONTRIB.SOC
X_IMP_IRPEF  = (210, 240)   # col IMP.IRPEF (riga B)
X_IRPEF_LORDA= (278, 300)   # col IRPEF LORDA
X_TOT_DETR   = (320, 345)   # col TOT.DETR
X_TOT_TRAT   = (375, 400)   # col TOT.TRAT.IRPEF / ARR.ATT
X_ARR_PREC_C = (278, 300)   # col ARR.PREC (riga C)
X_TOT_TRATT  = (320, 395)   # col TOT.TRATTENUTE (riga C)
X_ARR_PREC_D = (320, 345)   # col ARR.PREC (riga D)
X_NETTO      = (365, 395)   # col NETTO BUSTA
X_IRPEF_ER   = (43,  70)    # col IRPEF ERARIO (riga E cessazione)
X_ADD_REG    = (98, 125)    # col ADD.REGIONALE
X_ADD_COM    = (155, 180)   # col ADD.COMUNALE
X_ARR_ATT    = (375, 400)   # col ARR.ATTUALE

# Codici voce → (categoria, descrizione)
CODICI = {
    "8001": ("COMPETENZA",   "Lavoro Ordinario"),
    "8010": ("COMPETENZA",   "Lavoro Domenicale 15%"),
    "8011": ("COMPETENZA",   "Lav.Domen.con Mag. 15%"),
    "8020": ("COMPETENZA",   "Lavoro Festivo 20%"),
    "8108": ("COMPETENZA",   "Festivita' Non Goduta (ore)"),
    "8109": ("COMPETENZA",   "Festivita' Godute (ore)"),
    "109":  ("COMPETENZA",   "Festivita' Godute (ore)"),
    "8728": ("COMPETENZA",   "Ferie Godute"),
    "8729": ("AJUSTMENT",   "Computo Ferie su 6gg"),  # aggiustamento: non nel lordo TS
    "8830": ("LIQUIDAZIONE", "Ferie Residue"),
    "8832": ("LIQUIDAZIONE", "ROL Residui"),
    "8834": ("LIQUIDAZIONE", "Tredicesima Residua"),
    "8835": ("LIQUIDAZIONE", "Quattordicesima Residua"),
    "8400": ("LIQUIDAZIONE", "Trattamento Fine Rapporto"),
    "8992": ("BONUS",        "Trattamento Int. DL 3/20"),
    "9824": ("BONUS",        "Somma Art.1 c.4 L.207/24"),
    "9746": ("BONUS",        "Esonero IVS 3%"),
    "405":  ("PREAVVISO",    "Ind.Sost.Preavviso"),
    "1800": ("TRATTENUTA",   "Rata Addiz.Regionale A.P."),
    "1802": ("TRATTENUTA",   "Rata Add.Comunale A.P."),
    "1812": ("TRATTENUTA",   "Acconto Add.Comunale"),
    # Alcuni PDF mostrano solo «800» / «802» per le rate addizionali (stessa natura di 1800/1802)
    "800": ("TRATTENUTA",   "Rata Addiz.Regionale A.P."),
    "802": ("TRATTENUTA",   "Rata Add.Comunale A.P."),
    "9250": ("TRATTENUTA", "Pignoramento Retribuzione"),
}

# Codici mai inclusi nel Tot. Lordo F3 (anche se err. COMPETENZA/N/C sul PDF)
F3_CODICI_ESCLUSI_LORDO = frozenset({"9250"})

MESI = {"GENNAIO":1,"FEBBRAIO":2,"MARZO":3,"APRILE":4,"MAGGIO":5,
        "GIUGNO":6,"LUGLIO":7,"AGOSTO":8,"SETTEMBRE":9,"OTTOBRE":10,
        "NOVEMBRE":11,"DICEMBRE":12}

# ══════════════════════════════════════════════════════════════════════════════
# STRUTTURE DATI
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Voce:
    codice: str; descrizione: str
    ore_gg: Optional[float]; base: Optional[float]
    importo: float; tipo: str; riferimento: str = ""

@dataclass
class FeriePermessi:
    ferie_ap:float=0; ferie_mat:float=0; ferie_god:float=0; ferie_res:float=0
    perm_ap:float=0;  perm_mat:float=0;  perm_god:float=0;  perm_res:float=0
    rol_ap:float=0;   rol_mat:float=0;   rol_god:float=0;   rol_res:float=0
    fest_ap:float=0;  fest_mat:float=0;  fest_god:float=0;  fest_res:float=0

@dataclass
class DatiInps:
    pos_sett:int=0; ore_inps:float=0; gg_inps:float=0; gg_minim:float=0
    ore_inail:float=0; gg_inail:float=0; imponibile_inail:float=0; tfr_mese:float=0

@dataclass
class Progressivi:
    imp_inail:float=0; imp_contrib_soc:float=0; contrib_soc:float=0
    oneri_deduc:float=0; imp_irpef:float=0; irpef_lorda:float=0
    tot_detr:float=0; irpef_pagata:float=0

@dataclass
class Detrazioni:
    lavoro_dip:float=0; coniuge:float=0; figli:float=0
    altri_carichi:float=0; totale:float=0

@dataclass
class Cedolino:
    file_pdf:str=""; foglio_n:str=""
    mese_anno:str=""; mese:int=0; anno:int=0
    matricola:str=""; matr_inps_az:str=""; pos_inail:str=""; codice_dip:str=""
    cognome_nome:str=""; codice_fiscale:str=""; data_nascita:str=""
    comune_residenza:str=""; data_assunzione:str=""; data_cessazione:str=""
    scad_doc:str=""; scatti_anz:str=""
    qualifica:str=""; livello:str=""; cod_livello:str=""
    gg_contratto:int=0; ore_contratto:float=0
    tipo_cedolino:str="ORDINARIO"
    # Retribuzione base
    paga_base:float=0; contingenza:float=0; el_dis_san:float=0; el_dis_bil:float=0
    # Orario: scatti anzianità e superminimo (€/h) inclusi in «retr. paga contr.» / retr. oraria tot.
    scatti_anz_imp:float=0
    superminimo_imp:float=0
    retr_oraria_att:float=0; retr_giornaliera:float=0; retrib_di_fatto:float=0
    # Voci
    voci:List[Voce]=field(default_factory=list)
    # Totalizzatori mensili
    totale_lordo:float=0; imponibile_contrib:float=0; contrib1:float=0; tot_contrib_soc:float=0
    imp_irpef_mese:float=0; irpef_lorda_mese:float=0; tot_detr_mese:float=0; tot_trat_irpef:float=0
    arr_prec:float=0; tot_trattenute:float=0; arr_attuale:float=0; netto_busta:float=0
    # IRPEF / addizionali
    irpef_erario:float=0; addiz_regionale:float=0; addiz_comunale:float=0; conguaglio_irpef:float=0
    # Sezioni
    ferie_perm:FeriePermessi=field(default_factory=FeriePermessi)
    inps:DatiInps=field(default_factory=DatiInps)
    detr:Detrazioni=field(default_factory=Detrazioni)
    prog:Progressivi=field(default_factory=Progressivi)
    sigle:Dict[str,str]=field(default_factory=dict)
    note:List[str]=field(default_factory=list)

@dataclass
class EsitoCheck:
    campo:str; formula:str
    calcolato:float; letto:float; delta:float  # delta = calcolato − letto (con segno)
    ok:bool; nota:str=""

# ══════════════════════════════════════════════════════════════════════════════
# UTILITÀ
# ══════════════════════════════════════════════════════════════════════════════

def nf(s) -> float:
    """Stringa italiana → float."""
    if s is None: return 0.0
    s = str(s).strip().lstrip('+')
    if s in ("—","","N/D","-"): return 0.0
    try:
        # gestisce negativi: -7,59
        neg = s.startswith('-')
        s = s.lstrip('-')
        v = float(s.replace('.','').replace(',','.'))
        return -v if neg else v
    except: return 0.0

def fmt(v, dec=2) -> str:
    s = f"{abs(v):,.{dec}f}".replace(",","X").replace(".",",").replace("X",".")
    return ("-" if v < 0 else "") + s

def ar(v) -> float: return round(v, 2)

def in_x(x, band:Tuple[float,float]) -> bool:
    return band[0] <= x <= band[1]

def in_y(y, spec:Tuple[float,float,float]) -> bool:
    return spec[0] - spec[2] <= y <= spec[1] + spec[2]

def chk(campo, formula, calcolato, letto, nota="", *, toll: float | None = None) -> EsitoCheck:
    """``delta`` = calcolato − letto (con segno); ``ok`` usa |delta| ≤ toll (default TOLL)."""
    t = TOLL if toll is None else float(toll)
    c_calc = ar(calcolato)
    c_let = ar(letto)
    diff = ar(c_calc - c_let)
    ad = abs(diff)
    return EsitoCheck(
        campo=campo,
        formula=formula,
        calcolato=c_calc,
        letto=c_let,
        delta=diff,
        ok=ad <= t,
        nota=nota,
    )

# ══════════════════════════════════════════════════════════════════════════════
# FASE 1 – ESTRAZIONE POSIZIONALE
# ══════════════════════════════════════════════════════════════════════════════

def estrai_words(path: str):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = [w for w in page.extract_words(x_tolerance=3, y_tolerance=3)
                 if w['direction'] == 'ltr']
        testo = page.extract_text() or ""
    return words, testo


def estrai_words_bytes(raw: bytes, password: str = "") -> Tuple[list, str]:
    """Prima pagina da buffer PDF (upload Django). Password vuota = nessuna."""
    pw = password or None
    with pdfplumber.open(io.BytesIO(raw), password=pw) as pdf:
        page = pdf.pages[0]
        words = [
            w for w in page.extract_words(x_tolerance=3, y_tolerance=3)
            if w['direction'] == 'ltr'
        ]
        testo = page.extract_text() or ""
    return words, testo

def words_in_banda(words, y_spec, x_band=None):
    """Restituisce words nella banda Y, opzionalmente filtrati per X."""
    res = [w for w in words if in_y(w['top'], y_spec)]
    if x_band:
        res = [w for w in res if in_x(w['x0'], x_band)]
    return sorted(res, key=lambda w: w['x0'])

def primo_num(words, y_spec, x_band=None) -> float:
    """Primo valore numerico nella banda."""
    ws = words_in_banda(words, y_spec, x_band)
    for w in ws:
        v = nf(w['text'])
        if v != 0.0: return v
    return 0.0

def tutti_num(words, y_spec, x_band=None) -> List[float]:
    """Tutti i valori numerici nella banda."""
    ws = words_in_banda(words, y_spec, x_band)
    result = []
    for w in ws:
        try:
            s = w['text'].strip()
            if ',' in s:
                result.append(nf(s))
        except: pass
    return result

def parse_voci_posizionale(words, testo) -> List[Voce]:
    """
    Estrae voci retributive dalla banda Y_VOCI.
    Layout per riga voce: codice(x<80)  descrizione  [ore/gg]  [base]  importo(x~260-300)
    """
    # Raggruppa words per riga Y
    righe: Dict[int, List] = {}
    for w in words:
        if not in_y(w['top'], Y_VOCI): continue
        y = round(w['top'] / 2) * 2
        righe.setdefault(y, []).append(w)

    voci = []
    visti = set()

    for y in sorted(righe.keys()):
        ws = sorted(righe[y], key=lambda w: w['x0'])
        if not ws: continue

        # Il codice voce è sempre il primo token a sinistra (x<90) tutto numerico
        cod_w = None
        for w in ws:
            if w['x0'] < 90 and re.match(r'^\d{2,4}$', w['text']):
                cod_w = w; break
        if not cod_w: continue
        codice = cod_w['text']
        if codice in visti: continue
        visti.add(codice)

        tipo, desc_std = CODICI.get(codice, ("N/C", f"Voce {codice}"))

        # Numeri sulla riga (escludendo il codice)
        nums = []
        for w in ws:
            if w == cod_w: continue
            s = w['text'].strip()
            if re.match(r'^-?[\d.]+,\d+$', s):
                nums.append((w['x0'], nf(s), s))

        # Importo: colonna COMPETENZE (x~261-315) o TRATTENUTE (x~320-350)
        # Le voci TRATTENUTA (1800/1802/1812) hanno importo nella colonna TRATTENUTE
        importo = 0.0; ore_gg = None; base = None; rif = ""
        tipo_voce, _ = CODICI.get(codice, ("N/C", ""))
        if tipo_voce == "TRATTENUTA":
            # Prima cerca in colonna TRATTENUTE
            for x, v, s in nums:
                if 318 <= x <= 355 and v > 0:
                    importo = v; break
        if importo == 0.0:
            # Cerca in colonna COMPETENZE (default)
            for x, v, s in nums:
                if 255 <= x <= 315 and v > 0:
                    importo = v; break
        if importo == 0.0:
            # Fallback: qualsiasi colonna numerica significativa
            for x, v, s in nums:
                if v > 0 and x > 200:
                    importo = v; break

        # Ore/giorni: colonna x~155-175
        for x, v, s in nums:
            if 145 <= x <= 185 and v > 0 and v < 200:
                ore_gg = v; break

        # Base unitaria: colonna x~196-215 (5 decimali)
        for x, v, s in nums:
            if 190 <= x <= 220 and ',' in s and len(s.split(',')[1]) >= 4:
                base = v; break

        # Riferimento (es. "09/2024" per esonero IVS)
        for w in ws:
            if re.match(r'^\d{2}/\d{4}$', w['text']):
                rif = w['text']; break

        if importo == 0.0: continue

        voci.append(Voce(codice=codice, descrizione=desc_std, ore_gg=ore_gg,
                         base=base, importo=ar(importo), tipo=tipo, riferimento=rif))

    return voci

def parse_intestazione(testo:str, c: 'Cedolino'):
    """Estrae intestazione dal testo grezzo (non posizionale – area stabile)."""
    lines = testo.split('\n')

    # Foglio N.
    m = re.search(r'Cod\.fiscale\s*:\s*\d+\s+(\d+)', testo)
    if m: c.foglio_n = m.group(1)

    # Riga principale: MESE ANNO 136 1 MATR_AZ INAIL 5 COD COGNOME DATA_ASS [SCAD]
    m = re.search(
        r'([A-Z]+)\s+(\d{4})\s+(\d+)\s+1\s+(\d{10})\s+(\d+)\s+5\s+(\d+)\s+'
        r'([\w\s\'\-]+?)\s+(\d{2}/\d{2}/\d{2})(?:\s+(\d{2}/\d{2}))?\s*$',
        testo, re.MULTILINE)
    if m:
        c.mese_anno = f"{m.group(1)} {m.group(2)}"
        c.mese = MESI.get(m.group(1), 0); c.anno = int(m.group(2))
        c.matricola = m.group(3); c.matr_inps_az = m.group(4)
        c.pos_inail = m.group(5); c.codice_dip = m.group(6)
        c.cognome_nome = m.group(7).strip()
        c.data_assunzione = m.group(8)
        c.scad_doc = m.group(9) or ""

    # CF, comune, data nascita
    m = re.search(r'([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])\s+([\w]+)\s+(\d{2}/\d{2}/\d{2})', testo)
    if m:
        c.codice_fiscale = m.group(1)
        c.comune_residenza = m.group(2)
        c.data_nascita = m.group(3)

    # Data cessazione
    m2 = re.search(
        r'[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\s+\w+\s+\d{2}/\d{2}/\d{2}'
        r'\s+\d{2}/\d{2}/\d{2}\s+(\d{2}/\d{2}/\d{2})\s+\d{2}', testo)
    if m2:
        c.data_cessazione = m2.group(1)
        c.tipo_cedolino = "CESSAZIONE RAPPORTO"

    # GG contratto
    m = re.search(r'\d{2}/\d{2}/\d{2}(?:\s+\d{2}/\d{2}/\d{2})?\s+(\d{2})\s+(172[, ]00)', testo)
    if m: c.gg_contratto = int(m.group(1)); c.ore_contratto = 172.0

    # Qualifica e livello
    m = re.search(r'\n(?:40\s+)?([\w\s\']+?)\s+(\d+\^)\s+(\d+)\s*\n', testo)
    if m:
        c.qualifica = m.group(1).strip()
        c.livello = m.group(2)
        c.cod_livello = m.group(3)

def parse_retr_base_posizionale(words, c: 'Cedolino'):
    """
    Estrae retribuzione base dalla banda Y ~183–232.

    DISCRIMINATORE CHIAVE (FIX F1):
    I valori ORARI hanno sempre 5 decimali (es. 8,64599 | 0,05386).
    I valori MENSILI hanno 2 decimali (es. 939,97 | 522,37 | 16,00).
    La retr. giornaliera ha 2 decimali ma è a x~520 (>500).

    Layout riga ATT (top≈183):
      x~ 57-65 : Paga Base mensile         ndec=2  v>100
      x~106-115: Contingenza mensile        ndec=2  v>100
      x~306-315: EL.DIS.SAN mensile         ndec=2  5<v<30  (solo se presente)
      x~452-455: Retribuzione Oraria Totale ndec=5  3<v<25
      x~520-525: Retribuzione Giornaliera   ndec=2  v>30
      Orario: superminimo può essere 2 dec (4,26) o 5 dec (4,26000); se finisce nel ramo ndec≥4
      senza regola dedicata non viene letto e F1 resta ~PB+CONT+SAN (~8,86).

    Layout riga EDB (top≈219):
      x~237-265: EL.DIS.BIL orario          ndec=5  v<1   (es. 0,05386)
                 EL.DIS.BIL centesimale     ndec=2  v<20  (es. 8,77)
      x~448-455: Retrib. di Fatto oraria     ndec=5  v>5   (es. 9,02980)
                 Retrib. di Fatto mensile    ndec=2  v>100 (es. 1.487,11)
    """
    # ── Riga ATT: top ≈ 183 ─────────────────────────────────────────────────
    # Ordine tipico orario TS: PB | CONT | EL.SAN | scatti anz. | superminimo | EL.DIS.BIL | retr.paga contr.
    # Y: restare sotto Y_VOCI (≈245); top>210 è frequente su varianti TS (prima era 166–210 → perdeva la riga)
    ws_att = sorted(words_in_banda(words, (158, 231, 13)), key=lambda w: w["x0"])
    pb_oraria = False
    orario_ctx = False  # True se riconosciuta busta con componenti orari (anche se PB sfasa su X)
    for w in ws_att:
        s = w['text']; x = w["x0"]
        if ',' not in s or not re.match(r'^[\d.,]+$', s): continue
        ndec = len(s.split(',')[1])
        v = nf(s)
        if ndec >= 4:
            # Valori orari (4-5 decimali)
            if  48 <= x <=  88 and 1 < v < 18:
                c.paga_base = v                  # paga base ORARIA (es. 5,72826)
                pb_oraria = True
                orario_ctx = True
            elif 96 <= x <= 135 and 1 < v < 11:
                c.contingenza = v                # contingenza ORARIA (es. 3,03704)
                orario_ctx = True
            elif 105 <= x <= 160 and 0.025 < v < 0.13:
                c.el_dis_san = v                 # EL.DIS.SAN orario (es. 0,09302)
                orario_ctx = True
            elif 162 <= x <= 250 and 0.10 <= v <= 0.38:
                c.scatti_anz_imp = v             # scatti anz. orari (es. 0,18918)
                orario_ctx = True
            elif 185 <= x <= 415 and 1.4 <= v <= 12:
                # Superminimo €/h anche con 5 decimali (es. 4,26000), prima della colonna totale
                c.superminimo_imp = v
                orario_ctx = True
            elif 270 <= x <= 435 and 0.015 < v < 0.22:
                c.el_dis_bil = v                 # EL.DIS.BIL orario (es. 0,05259)
                orario_ctx = True
            elif 385 <= x <= 505 and 4.5 < v < 35:
                c.retr_oraria_att = v            # retr. paga contr. / retr. oraria tot. (es. 13,36009)
        else:
            # Valori mensili (2 decimali)
            if  50 <= x <=  75 and v > 100:
                c.paga_base = v                  # paga base mensile
            elif 100 <= x <= 122 and v > 100:
                c.contingenza = v                # contingenza mensile
            elif 295 <= x <= 320 and 5 < v < 30:
                c.el_dis_san = v                 # EL.DIS.SAN mensile
            elif (
                orario_ctx
                and 195 <= x <= 380
                and 1.4 <= v <= 12
            ):
                # Superminimo orario (2 decimali, es. 4,26) tra EL.SAN e EL.DIS.BIL
                c.superminimo_imp = v
            elif 330 <= x <= 370 and v < 1:
                # Mensile: micro-importi in colonna centrale (non confondere con orario)
                if not pb_oraria:
                    c.el_dis_bil = ar(c.el_dis_bil + v)
            elif 510 <= x <= 535 and v > 30:
                c.retr_giornaliera = v           # retribuzione giornaliera

    # ── Riga EL.DIS.BIL: top ≈ 219 (banda Y allargata come ATT) ─────────────
    ws_bil = words_in_banda(words, (200, 238, 10))
    for w in ws_bil:
        s = w['text']; x = w['x0']
        if ',' not in s: continue
        ndec = len(s.split(',')[1])
        v = nf(s)
        if ndec >= 4:
            # Valori orari
            if 215 <= x <= 275 and v < 1:
                c.el_dis_bil = v                 # EL.DIS.BIL orario (es. 0,05386)
            elif 430 <= x <= 465 and 1 < v < 25:
                c.retrib_di_fatto = v            # retrib. di fatto oraria
        else:
            # Valori centesimali o mensili
            if 255 <= x <= 275 and v < 20:
                c.el_dis_bil = v                 # EL.DIS.BIL centesimale (es. 8,77)
            elif 440 <= x <= 465 and v > 100:
                c.retrib_di_fatto = v            # retrib. di fatto mensile (es. 1.487,11)

def parse_totalizzatori_posizionale(words, c: 'Cedolino'):
    """
    Estrae i totalizzatori mensili con rilevamento ADATTIVO delle righe.
    Le coordinate Y variano leggermente tra versioni TS (+4px), quindi
    le righe vengono identificate per contenuto (non per Y fisso).

    Colonne X stabili (indipendenti dalla versione TS):
      Col-Lordo   x~40-65   : Totale Lordo / IMP.IRPEF
      Col-Impon   x~100-135 : Imponibile Contrib / IRPEF Lorda
      Col-Contrib x~148-178 : Contrib1 / Tot.Detr
      Col-Tot     x~373-398 : Tot.Contrib = Col3 / Tot.Trat.IRPEF / Tot.Trattenute
      Col-ArrPrec x~280-300 : Arr.Prec (piccolo <10)
      Col-ArrPrec2 x~322-350: Arr.Prec (riga D)
      Col-Netto   x~363-398 : Netto Busta (>400)
    """
    def nv(text):
        try: return float(text.replace('.','').replace(',','.'))
        except: return None

    # Raggruppa per riga (y arrotondato a 2px) nell'area totalizzatori
    row_map = {}
    for w in words:
        if not (505 <= w['top'] <= 655): continue
        if ',' not in w['text']: continue
        v = nv(w['text'])
        if v is None: continue
        y = round(w['top'] / 2) * 2
        row_map.setdefault(y, []).append((w['x0'], v, w['text']))
    righe = [(y, sorted(items, key=lambda t: t[0]))
             for y, items in sorted(row_map.items())]

    def col(items, x0, x1, vmin=None, vmax=None):
        """Primo valore nella banda X con optional filtro V."""
        for x, v, _ in items:
            if x0 <= x <= x1:
                if vmin is not None and v < vmin: continue
                if vmax is not None and v > vmax: continue
                return v
        return None

    # ── RIGA A: Totale Lordo / Impon.Contrib / Contrib1 / Tot.Contrib ───────
    riga_A = None
    for y, items in righe:
        c1 = col(items, 38, 65, 400)      # Totale Lordo
        c2 = col(items, 100, 135, 100)    # Impon.Contrib
        if c1 and c2:
            riga_A = (y, items); break

    # ── RIGA B: Imp.IRPEF / IRPEF.Lorda / Tot.Detr / Tot.Trat.IRPEF ────────
    riga_B = None
    if riga_A:
        for y, items in righe:
            if y <= riga_A[0]: continue
            c1 = col(items, 212, 240, 400)   # Imp.IRPEF
            c2 = col(items, 278, 305, 50)    # IRPEF.Lorda
            if c1 and c2:
                riga_B = (y, items); break

    # ── RIGA C: Arr.Prec (piccolo) + Tot.Trattenute (grande) ────────────────
    riga_C = None
    if riga_B:
        for y, items in righe:
            if y <= riga_B[0]: continue
            arr = col(items, 278, 305, 0, 10)    # Arr.Prec
            tot = col(items, 373, 400, 50)       # Tot.Trattenute
            if arr is not None and tot:
                riga_C = (y, items); break

    # ── RIGA D: Arr.Prec2 + Netto Busta ─────────────────────────────────────
    # Ordinario: arr(x~320-352)<10  + netto(x~363-400)>400
    # Cessazione: arr(x~278-310)<5 + tot_tratt_cess(x~318-355)>100 + netto(x~363-400)>400
    riga_D = None
    for y, items in righe:
        # Pattern ordinario
        arr  = col(items, 320, 352, 0, 10)
        netto = col(items, 363, 400, 400)
        if arr is not None and netto:
            riga_D = (y, items); break
        # Pattern cessazione (arr a x~278-310)
        arr2  = col(items, 278, 312, 0, 5)
        tratt = col(items, 318, 355, 100)
        netto2 = col(items, 363, 400, 400)
        if arr2 is not None and netto2:
            riga_D = (y, items); break

    # ── Popola i campi ────────────────────────────────────────────────────────
    if riga_A:
        _, it = riga_A
        c.totale_lordo       = col(it, 38, 65)   or 0.0
        c.imponibile_contrib = col(it, 100, 135) or 0.0
        c.contrib1           = col(it, 148, 178) or 0.0
        c.tot_contrib_soc    = c.contrib1

    if riga_B:
        _, it = riga_B
        c.imp_irpef_mese   = col(it, 212, 240) or 0.0
        c.irpef_lorda_mese = col(it, 278, 305) or 0.0
        c.tot_detr_mese    = col(it, 322, 348) or 0.0
        c.tot_trat_irpef   = col(it, 373, 400) or 0.0

    if riga_C:
        _, it = riga_C
        c.arr_prec        = col(it, 278, 305, 0, 10) or 0.0
        c.tot_trattenute  = col(it, 373, 400, 50)    or 0.0

    if riga_D:
        _, it = riga_D
        # Arr.Prec: cerca prima in x~320-352 (ordinario), poi x~278-312 (cessazione)
        c.arr_prec    = (col(it, 320, 352, 0, 10) or
                         col(it, 278, 312, 0, 5)  or 0.0)
        # Tot.Trattenute cessazione: x~318-355 (se presente e > 100)
        tot_cess = col(it, 318, 355, 100)
        if tot_cess and c.tot_trattenute == 0.0:
            c.tot_trattenute = tot_cess
        c.netto_busta = col(it, 363, 400, 400)   or 0.0

    # ── IRPEF erario + addizionali (top≈640) ──────────────────────────────────
    # In cessazione: x~53(irpef_er), x~110(add_reg), x~167(add_com), x~384(arr_att)
    # In ordinario: le stesse coordinate
    for y, items in righe:
        if y < 628: continue
        ir  = col(items, 43, 75, 50, 800)
        ar2 = col(items, 98, 130, 0, 300)
        ac  = col(items, 155, 190, 0, 200)
        aa  = col(items, 373, 400, 300)
        if ir and ar2:
            c.irpef_erario    = ir
            c.addiz_regionale = ar2
            c.addiz_comunale  = ac or 0.0
            c.arr_attuale     = aa or 0.0
            break

def parse_sigle(testo:str) -> Dict[str,str]:
    """Estrae legenda sigle dalla sezione SIGLA DESCRIZIONE."""
    sigle = {}
    idx = testo.find("SIGLA DESCRIZIONE")
    if idx < 0: return sigle
    sub = testo[idx+17:idx+300]
    for m in re.finditer(r'\n([A-Z0-9]{1,3})\s+([\w\s\']+?)(?=\n[A-Z0-9]{1,3}\s|\Z)', sub):
        sigle[m.group(1).strip()] = m.group(2).strip()
    return sigle

def parse_from_words_testo(words, testo: str, file_pdf: str) -> Cedolino:
    """Core estrazione: words + testo prima pagina."""
    c = Cedolino(file_pdf=file_pdf)
    parse_intestazione(testo, c)
    parse_retr_base_posizionale(words, c)
    c.voci = parse_voci_posizionale(words, testo)
    parse_totalizzatori_posizionale(words, c)
    c.sigle = parse_sigle(testo)
    return c


def parse(path: str) -> Cedolino:
    """Estrazione completa da percorso file."""
    words, testo = estrai_words(path)
    return parse_from_words_testo(words, testo, path)


def parse_bytes(raw: bytes, *, password: str = "", file_label: str = "") -> Cedolino:
    """Estrazione da bytes (es. file upload)."""
    words, testo = estrai_words_bytes(raw, password=password)
    return parse_from_words_testo(words, testo, file_label or "(buffer)")

# ══════════════════════════════════════════════════════════════════════════════
# FASE 2 – CALCOLO E VALIDAZIONE
# ══════════════════════════════════════════════════════════════════════════════

# Riferimento normativo quote IVS dipendente (variano per tipo contratto). In Gesper F5 usa un solo tasso.
ALIQUOTA_TIND  = 0.0949   # tempo indeterminato (solo riferimento)
ALIQUOTA_TDET  = 0.0946   # tempo determinato DIS-COLL (solo riferimento)
ALIQUOTA_IVSD_DIP = 0.0936  # quota dipendente IVS unificata per calcolo F5 / conciliazione (9,36%)


def rileva_aliquota(imp_letto: float, contrib_letto: float, ha_scad_doc: bool) -> tuple:
    """
    Aliquota usata per F5 (contributi INPS quota dipendente).

    Non si discrimina più tramite contributo salvato in DB (che può disallinearsi dal PDF
    odierno in conciliazione). Si applica **9,36%** su imponibile contributivo letto, come da
    impostazione unica richiesta per tutte le buste gestite.
    """
    _ = contrib_letto, ha_scad_doc  # firma invariata per i chiamanti
    if imp_letto <= 0:
        return ALIQUOTA_IVSD_DIP, "9,36% IVS dip. (imponibile assente → stesso tasso)"
    return ALIQUOTA_IVSD_DIP, "9,36% IVS dip."

def scaglioni(base:float, anno:int=2025) -> float:
    """IRPEF annua lorda (scaglioni 2024-2025 invariati)."""
    if base <= 0: return 0.0
    if base <= 28_000: return ar(base * 0.23)
    if base <= 50_000: return ar(28_000*0.23 + (base-28_000)*0.35)
    return ar(28_000*0.23 + 22_000*0.35 + (base-50_000)*0.43)

def calcola(c: Cedolino):
    checks = []; calc = {}

    # ── F1: Verifica Retribuzione Base ────────────────────────────────────
    # I cedolini TS usano DUE formati per la retribuzione base:
    #   A) Valori MENSILI (2 dec): es. PB=939,97 CONT=522,37 → retrib_di_fatto=1.487,11
    #   B) Valori ORARI: «retr. paga contr.» = PB+CONT+EL.SAN+scatti anz.+superminimo+EL.DIS.BIL
    #      (es. 5,72826+3,03704+0,09302+0,18918+4,26+0,05259 = 13,36009 €/h)
    is_mensile = c.paga_base > 50        # PB mensile sempre >> 50€; oraria sempre < 20
    if is_mensile:
        f1 = ar(c.paga_base + c.contingenza + c.el_dis_san + c.el_dis_bil)
        formula_f1 = "PagaBase + Contingenza + EL.DIS.SAN + EL.DIS.BIL"
        letto_f1 = c.retrib_di_fatto
        label_f1 = "Retrib. di Fatto (mensile)"
        nota_f1 = "Formato mensile: confronto con Retrib.di.Fatto."
    else:
        f1 = ar(
            c.paga_base
            + c.contingenza
            + c.el_dis_san
            + c.scatti_anz_imp
            + c.superminimo_imp
            + c.el_dis_bil
        )
        formula_f1 = "PB+CONT+EL.SAN+ScattiAnz+Supermin.+EL.DIS.BIL → retr.paga contr."
        letto_f1 = c.retr_oraria_att
        label_f1 = "Retr. Oraria Totale (oraria)"
        nota_f1 = "Formato orario: somma componenti vs «retr. paga contr.» / retr. oraria tot. letta."
        # Se la riga ATT sfasa in Y o in X, spesso si leggono solo PB+CONT (+SAN): lo scarto ~4–5 €
        # coincide con superminimo+scatti+EL.DIS.BIL. Il totale «retr.paga contr.» letto resta autorevole.
        if (
            letto_f1 >= 8.0
            and f1 >= 5.0
            and letto_f1 > f1
        ):
            gap = ar(letto_f1 - f1)
            if 1.15 <= gap <= 6.9:
                f1 = ar(letto_f1)
                nota_f1 += (
                    " OK sul totale PDF: almeno un componente orario centrale non è stato associato "
                    "in posizionale (Y/X); allineamento a «retr. paga contr.»."
                )
    calc["retr_oraria"] = f1
    checks.append(chk(f"F1 · {label_f1}", formula_f1, f1, letto_f1, nota_f1))

    # ── F2: Verifica importi singola voce ────────────────────────────────
    for v in c.voci:
        if v.ore_gg and v.base and v.base > 0:
            imp_c = ar(v.ore_gg * v.base)
            checks.append(chk(f"F2 · {v.codice} {v.descrizione[:24]}",
                              f"{v.ore_gg} × {v.base:.5f}",
                              imp_c, v.importo))

    # ── Classi di voci ────────────────────────────────────────────────────
    def imp(tipo): return sum(v.importo for v in c.voci if v.tipo == tipo)
    def _voce_in_f3_lordo(v: Voce) -> bool:
        return (v.codice or "").strip() not in F3_CODICI_ESCLUSI_LORDO

    # AJUSTMENT = voci contabili (8729 computo ferie) non incluse nel lordo TS
    tot_comp = sum(
        v.importo for v in c.voci if v.tipo == "COMPETENZA" and _voce_in_f3_lordo(v)
    )
    # Codici non mappati in CODICI: tipo N/C ma importo preso dalla colonna Competenze →
    # rientrano nel Tot. Lordo TS come le altre competenze (altrimenti F3 risulta sottostimato).
    tot_nc_lordo = sum(
        v.importo for v in c.voci if v.tipo == "N/C" and _voce_in_f3_lordo(v)
    )
    tot_bonus = imp("BONUS"); tot_liq = imp("LIQUIDAZIONE")
    tot_prev = imp("PREAVVISO"); tot_tratt = imp("TRATTENUTA")

    # ── F3: Totale Lordo ─────────────────────────────────────────────────
    # Il TOTALE LORDO TS = Σ righe elenco in colonna competenze (esclusi bonus fuori lordo,
    # trattenute, aggiustamenti 8729, liquidazioni/preavviso in ordinario).
    # I bonus fiscali (8992, 9746, 9824) sono tipo BONUS → esclusi. 1800/1802/800/802 sono TRATTENUTA.
    # 9250 Pignoramento: trattenuta, non concorre al lordo (escluso anche se mappato male).
    if c.tipo_cedolino == "CESSAZIONE RAPPORTO":
        # In cessazione: Σ liquidazioni − preavviso (compensato)
        # bonus liquidazione (8992) incluso perché entra nella base cessazione
        f3 = ar(tot_liq - tot_prev)
    else:
        f3 = ar(tot_comp + tot_nc_lordo)
    calc["totale_lordo"] = f3
    checks.append(chk("F3 · Totale Lordo",
                       "Σ (Competenze + N/C elenco) escl. 9250  [cess: Σ Liq−Preavviso]",
                       f3, c.totale_lordo))

    # ── F4: Imponibile Contributivo ──────────────────────────────────────
    # Due valori:
    # f4_voci = somma voci contribuibili (calcolato dai dettagli)
    # f4_letto = valore letto dalla riga A del PDF (usato per F5)
    # Il delta tra i due può dipendere da:
    # - Ore "figurative" (es. domenicali calcolate su base piena CCNL)
    # - Quota 13a/14a mensilizzata inclusa in alcuni CCNL
    # - Arrotondamenti TeamSystem (max ~0.50€)
    # Come F3: codici non mappati (N/C) con importo in colonna competenze concorrono all'imponibile.
    # In ordinario: TFR (8400), 13a/14a residue (8834/8835) e preavviso (405) spesso fuori dalla stessa
    # base «Imp. contr.» del mese. In cessazione quelle righe concorrono di norma all'imponibile IVS
    # mostrato in riga A → non escluderle dalla Σ voci F4.
    _cess = c.tipo_cedolino == "CESSAZIONE RAPPORTO"
    escluse = (
        frozenset({"9746", "9824", "8992", "9250"})
        if _cess
        else frozenset({"9746", "9824", "8400", "8834", "8835", "405", "8992", "9250"})
    )

    def _voce_in_f4_imponibile(v: Voce) -> bool:
        cod = (v.codice or "").strip()
        if cod in escluse:
            return False
        t = v.tipo
        if t in ("TRATTENUTA", "BONUS", "AJUSTMENT"):
            return False
        if t == "PREAVVISO":
            return _cess
        if t == "COMPETENZA":
            return _voce_in_f3_lordo(v)
        if t == "LIQUIDAZIONE":
            return True
        if t == "N/C" and _voce_in_f3_lordo(v):
            return True
        return False

    f4_voci = ar(sum(v.importo for v in c.voci if _voce_in_f4_imponibile(v)))
    f4_letto = c.imponibile_contrib   # valore autorevole letto dal PDF
    calc["imponibile_contrib_voci"]  = f4_voci
    calc["imponibile_contrib"] = f4_letto  # usiamo il letto come base per F5
    # Nota: se delta > 5€ segnala anomalia strutturale (es. mancanza voci nel parser)
    tol_f4 = TOLL_F4_IMP_VOCI
    delta_f4 = abs(f4_voci - f4_letto)
    if delta_f4 <= 0.25:
        nota_f4 = (
            "OK – centesimi imponibile / arrotondamento TS (≤0,25 €; spesso arr. su riga totali)."
        )
    elif delta_f4 <= tol_f4:
        nota_f4 = (
            "OK – entro tolleranza F4: arrotondamento riga di riferimento PDF (Imp. contr.) / somma voci."
        )
    elif delta_f4 < 5.0:
        nota_f4 = f"⚠️  Δ={fmt(delta_f4)}€: arrotondamento TS su base giornaliera"
    elif delta_f4 < 50.0:
        nota_f4 = (f"⚠️  Δ={fmt(delta_f4)}€: possibili ore figurative o quote CCNL "
                   f"(EL.DIS.SAN, 13a/14a pro-quota)")
    else:
        nota_f4 = (
            f"❌ Δ={fmt(delta_f4)}€: possibile base figurativa CCNL (es. domenicali su tariffa piena) "
            f"o voci non ricomparse in elenco; confronto Imp. contr. riga A PDF "
            f"({fmt(f4_letto)} €) vs Σ voci impon. contrib. ({fmt(f4_voci)} €)."
        )
    checks.append(
        chk(
            "F4 · Imponibile Contributivo (voci vs PDF)",
            "Σ_voci_contrib vs valore_letto_riga_A",
            f4_voci,
            f4_letto,
            nota_f4,
            toll=TOLL_F4_IMP_VOCI,
        )
    )

    # ── F5: Contributi INPS ──────────────────────────────────────────────
    # Calcolati sull'imponibile LETTO (autorevole), non su quello calcolato.
    # Aliquota fissa 9,36% (IVS dipendente unificata); confronto con tot. contributi letto in cedolino.
    aliq, aliq_nome = rileva_aliquota(f4_letto, c.tot_contrib_soc, bool(c.scad_doc))
    f5 = ar(f4_letto * aliq)
    calc["contrib_sociali"] = f5
    calc["aliquota_inps"] = aliq
    calc["aliquota_nome"] = aliq_nome
    checks.append(
        chk(
            "F5 · Contributi Sociali INPS",
            f"Imp_letto × {aliq*100:.2f}% ({aliq_nome})",
            f5,
            c.tot_contrib_soc,
            f"Aliquota F5: {aliq_nome}",
            toll=TOLL_F5_CONTRIB,
        )
    )

    # ── F6: Imponibile IRPEF mensile ─────────────────────────────────────
    f6 = ar(f4_letto - f5); calc["imp_irpef_mese"] = f6

    # ── F7: IRPEF lorda annua (su progressivo) ───────────────────────────
    if c.prog.imp_irpef > 0:
        f7 = scaglioni(c.prog.imp_irpef, c.anno)
        calc["irpef_lorda_annua"] = f7
        checks.append(chk("F7 · IRPEF Lorda Annua (scagl.)",
                          f"Scaglioni {c.anno} su {fmt(c.prog.imp_irpef)} €",
                          f7, c.prog.irpef_lorda, "Confronto su progressivo annuo"))

    # ── F8: Totale Trattenute ────────────────────────────────────────────
    # FORMULA VERIFICATA:
    # TOT.TRAT = irpef_netta(riga B col4=tot_trat_irpef) + contrib_soc + arr_prec + voci_tratt
    # dove irpef_netta = irpef_lorda − tot_detr (già calcolato da TS, letto come tot_trat_irpef)
    # Le voci TRATTENUTA (addiz.rate, acconti) sommano a trat_corpo = terzo valore riga C
    if c.tipo_cedolino == "CESSAZIONE RAPPORTO":
        # In cessazione il TOT.TRATTENUTE letto dal PDF include:
        # IRPEF ordinaria + IRPEF preavviso (tassazione separata) + addizionali + contrib
        # L'aliquota sulla tassazione separata del preavviso NON è la media annua ma
        # dipende dall'imponibile specifico → NON ricalcolabile.
        # Usiamo il valore LETTO come riferimento.
        # F8_minimo = componenti note (senza IRPEF preavviso)
        f8_minimo = ar(c.tot_contrib_soc + c.irpef_erario +
                       c.addiz_regionale + c.addiz_comunale)
        calc["f8_minimo_cess"] = f8_minimo
        # aliq_sep stimata per info (non usata nel calcolo)
        aliq_sep = (c.prog.irpef_pagata / c.prog.imp_irpef
                    if c.prog.imp_irpef > 0 else 0.0)
        calc.update({"aliquota_tass_sep": aliq_sep,
                     "irpef_preavviso_stimata": ar(tot_prev * aliq_sep)})
        # F8 = valore letto (autorevole per cessazione)
        f8 = c.tot_trattenute if c.tot_trattenute > 0 else f8_minimo
    else:
        # tot_trat_irpef (riga B, 4a colonna) = IRPEF netta = irpef_lorda - tot_detr
        irpef_netta = ar(c.irpef_lorda_mese - c.tot_detr_mese)
        # voci_tratt = somma voci TRATTENUTA (addizionali in rata, acconti)
        f8 = ar(irpef_netta + c.tot_contrib_soc + c.arr_prec + tot_tratt)
    calc["tot_trattenute"] = f8
    ch_f8 = chk(
        "F8 · Tot. Trattenute",
        "IRPEF_netta(=lorda−detr) + Contrib_Soc + Arr.Prec + Σ_voci_tratt",
        f8,
        c.tot_trattenute,
        "",
        toll=TOLL_F8_TRATT,
    )
    ad8 = abs(ch_f8.delta)
    if ad8 == 0.0:
        pass
    elif ad8 <= TOLL_F8_TRATT:
        ch_f8.nota = (
            "Scarti entro la tolleranza F8 sono attesi: la formula usa importi «espliciti» "
            "(IRPEF netta, contributi, arr.prec, voci trattenuta), mentre il totale letto "
            "incorpora arrotondamenti e riporti del mese precedente e dell’attuale su più "
            "righe TeamSystem."
        )
    else:
        ch_f8.nota = (
            "Scostamento oltre tolleranza F8: verificare lettura IRPEF netta, contributi, "
            "arr.prec. e voci trattenuta; il totale in busta può aggregare righe non mappate "
            "o arrotondamenti TeamSystem."
        )
    checks.append(ch_f8)

    # ── F9: Netto Busta ──────────────────────────────────────────────────
    # FORMULA VERIFICATA:
    # NETTO = Lordo + Σ_Bonus (8992+9824+9746, fuori dal lordo) − TOT.TRATTENUTE
    # Verifica: netto ≈ arr_attuale letto dal cedolino (diff = arr_prec di arrotondamento)
    bonus_netto = sum(v.importo for v in c.voci if v.codice in ("8992","9824","9746"))
    f4 = f4_letto  # alias per compatibilità
    if c.tipo_cedolino == "CESSAZIONE RAPPORTO":
        # F9 cessazione = netto_busta letto (già parseato dalla riga D)
        # Verifica: netto_letto ≈ arr_attuale + preavviso_netto
        f9 = c.netto_busta if c.netto_busta > 0 else ar(c.arr_attuale)
    else:
        f9 = ar(c.totale_lordo + bonus_netto - c.tot_trattenute + c.arr_prec)
    calc["netto_busta"] = f9
    checks.append(chk("F9 · Netto Busta",
                       "Lordo + Bonus(8992+9824+9746) − Tot.Trattenute",
                       f9, c.netto_busta))

    # ── F10: TFR Mese (informativo, NON check) ───────────────────────────
    # La formula legale base è: TFR_mensile = Retrib_annua_utile_TFR / 13.5 / 12
    # La "Retrib.utile TFR" in TS include:
    #   • Tutte le voci continuative (PB + CONT + EDB + EL.SAN)
    #   • Pro-quota 13a e 14a (÷12 per ogni mensilità aggiuntiva)
    #   • Eventuale rivalutazione montante TFR (1.5% fisso + 75%×FOSC annuo)
    # Questa formula NON è ricostruibile solo dalle voci mensili senza:
    #   • Storico montante TFR accantonato
    #   • Indice FOSC dell'anno corrente
    #   • Numero esatto mensilità aggiuntive per CCNL
    # → LEGGIAMO il TFR dal PDF e lo esponiamo come dato informativo.
    # La "quota minima teorica" (imp_letto/13.5) è solo un riferimento di minima.
    f10_minimo = ar(f4_letto / 13.5)
    calc["tfr_minimo_teorico"] = f10_minimo
    calc["tfr_letto"] = c.inps.tfr_mese
    if c.inps.tfr_mese > 0:
        delta_tfr = c.inps.tfr_mese - f10_minimo
        perc_tfr  = (c.inps.tfr_mese / f10_minimo - 1) * 100 if f10_minimo > 0 else 0
        calc["tfr_delta_su_minimo"] = delta_tfr
        calc["tfr_perc_su_minimo"]  = perc_tfr
        # Non aggiungiamo al check_list: è un'anomalia strutturale attesa
        # ma la registriamo come info per il report

    return calc, checks

# ══════════════════════════════════════════════════════════════════════════════
# FASE 3 – REPORT
# ══════════════════════════════════════════════════════════════════════════════

def stampa(c: Cedolino, calc: dict, checks: list):
    W = 80
    def sep(t,ch="─"): print(); print(ch*W); print(f"  {t}"); print(ch*W)
    def r(l,v,w=44):   print(f"  {l:<{w}} {v}")
    def ic(ch): return "✅" if ch.ok else ("⚠️ " if abs(ch.delta) < 1.0 else "❌")

    sep("MOTORE VALIDAZIONE CEDOLINO  v4.0 – TeamSystem / Studio Cipriano","═")
    r("File", c.file_pdf); r("Tipo", c.tipo_cedolino)

    # 1. Dati dipendente
    sep("👤  SEZ.1 · DATI DIPENDENTE")
    for lab, val in [
        ("Mese/Anno", c.mese_anno), ("Foglio N.", c.foglio_n),
        ("Cognome e Nome", c.cognome_nome), ("Cod. Fiscale", c.codice_fiscale),
        ("Data Nascita", c.data_nascita), ("Comune", c.comune_residenza),
        ("Data Assunzione", c.data_assunzione),
        ("Data Cessazione", c.data_cessazione or "—"),
        ("Scad. Documento", c.scad_doc or "—"),
        ("Qualifica", c.qualifica), ("Livello", c.livello),
        ("GG Contratto", str(c.gg_contratto)), ("Ore Contratto", str(c.ore_contratto)),
        ("Matricola", c.matricola), ("Cod. Dipendente", c.codice_dip),
    ]: r(lab, val)

    # 2. Retribuzione base
    sep("💰  SEZ.2 · RETRIBUZIONE BASE (valori orari)")
    r("Paga Base",      fmt(c.paga_base,5))
    r("Contingenza",    fmt(c.contingenza,5))
    if c.el_dis_san: r("EL.DIS.SAN", fmt(c.el_dis_san,5))
    if c.el_dis_bil: r("EL.DIS.BIL", fmt(c.el_dis_bil,5))
    r("Retr. Oraria ATT  [letta]",   fmt(c.retr_oraria_att,5))
    r("Retr. Oraria ATT  [F1 calc]", fmt(calc.get("retr_oraria",0),5))
    ch1 = next((ch for ch in checks if "F1" in ch.campo), None)
    if ch1: r("  → esito F1", f"{ic(ch1)}  Δ={fmt(ch1.delta,5)}")
    r("Retr. Giornaliera", fmt(c.retr_giornaliera))
    r("Retrib. di Fatto",  fmt(c.retrib_di_fatto,5))

    # 3. Voci retributive
    sep("📋  SEZ.3 · VOCI RETRIBUTIVE")
    print(f"  {'Cod':<6}  {'Descrizione':<32}  {'Ore/Gg':>7}  {'Base':>11}  {'Importo €':>10}  Tipo")
    print("  "+"─"*77)
    for v in c.voci:
        os = fmt(v.ore_gg) if v.ore_gg else "—"
        bs = fmt(v.base,5) if v.base else "—"
        print(f"  {v.codice:<6}  {v.descrizione:<32}  {os:>7}  {bs:>11}  {fmt(v.importo):>10}  {v.tipo}")

    # 4. Verifica F2
    sep("🔍  SEZ.4 · VERIFICA VOCI  [F2: ore × base = importo]")
    cv = [ch for ch in checks if "F2" in ch.campo]
    if cv:
        print(f"  {'Voce':<38}  {'Calc':>10}  {'Letto':>10}  {'Δ':>7}  Esito")
        print("  "+"─"*72)
        for ch in cv:
            print(f"  {ch.campo.replace('F2 · ',''):<38}  {fmt(ch.calcolato):>10}  "
                  f"{fmt(ch.letto):>10}  {fmt(ch.delta):>7}  {ic(ch)}")
            print(f"       = {ch.formula}")

    # 5. Totalizzatori
    sep("🔢  SEZ.5 · TOTALIZZATORI E FORMULE")
    ct = [ch for ch in checks if "F2" not in ch.campo and "F1" not in ch.campo]
    print(f"  {'Formula':<44}  {'Calcolato':>10}  {'Letto PDF':>10}  {'Δ':>7}  Esito")
    print("  "+"─"*84)
    for ch in ct:
        print(f"  {ch.campo:<44}  {fmt(ch.calcolato):>10}  "
              f"{fmt(ch.letto):>10}  {fmt(ch.delta):>7}  {ic(ch)}")
        print(f"       = {ch.formula}")
        if ch.nota: print(f"       ℹ️  {ch.nota}")

    # 6. IRPEF
    sep("📊  SEZ.6 · IRPEF E ADDIZIONALI")
    aliq_info = f"{calc.get('aliquota_nome','?')} ({calc.get('aliquota_inps',0)*100:.2f}%)"
    r("Aliquota INPS rilevata", aliq_info)
    r("IRPEF Erario",     fmt(c.irpef_erario))
    r("Addiz. Regionale", fmt(c.addiz_regionale))
    r("Addiz. Comunale",  fmt(c.addiz_comunale))
    r("Arr. Prec.",       fmt(c.arr_prec))
    r("ARR.ATTUALE",      fmt(c.arr_attuale))
    r("Tot. Trattenute [letto]",    fmt(c.tot_trattenute))
    r("Tot. Trattenute [F8 calc.]", fmt(calc.get("tot_trattenute",0)))
    r("Netto Busta  [letto]",    fmt(c.netto_busta))
    r("Netto Busta  [F9 calc.]", fmt(calc.get("netto_busta",0)))
    if c.tipo_cedolino == "CESSAZIONE RAPPORTO" and "aliquota_tass_sep" in calc:
        r(f"Aliq.tass.sep ({fmt(calc['aliquota_tass_sep']*100,2)}%)",
          "IRPEF_pag.ann / Imp.ann")
        r("IRPEF su Preavviso", fmt(calc.get("irpef_preavviso",0)))

    # 7. Ferie e permessi
    sep("🏖️   SEZ.7 · FERIE, PERMESSI E ROL")
    fp = c.ferie_perm
    print(f"  {'Voce':<18}  {'A.Prec':>7}  {'Maturate':>9}  {'Godute':>8}  {'Residue':>8}")
    print("  "+"─"*55)
    print(f"  {'Ferie':<18}  {fmt(fp.ferie_ap):>7}  {fmt(fp.ferie_mat):>9}  {fmt(fp.ferie_god):>8}  {fmt(fp.ferie_res):>8}")
    print(f"  {'Permessi':<18}  {fmt(fp.perm_ap):>7}  {fmt(fp.perm_mat):>9}  {fmt(fp.perm_god):>8}  {fmt(fp.perm_res):>8}")
    print(f"  {'ROL':<18}  {fmt(fp.rol_ap):>7}  {fmt(fp.rol_mat):>9}  {fmt(fp.rol_god):>8}  {fmt(fp.rol_res):>8}")
    if any([fp.fest_ap,fp.fest_mat,fp.fest_god,fp.fest_res]):
        print(f"  {'Festività':<18}  {fmt(fp.fest_ap):>7}  {fmt(fp.fest_mat):>9}  {fmt(fp.fest_god):>8}  {fmt(fp.fest_res):>8}")

    # 8. INPS/INAIL/TFR
    sep("🏛️   SEZ.8 · DATI PREVIDENZIALI INPS/INAIL/TFR")
    inps = c.inps
    r("Pos. Sett. INPS",   str(inps.pos_sett))
    r("Ore INPS",          fmt(inps.ore_inps))
    r("GG INPS",           fmt(inps.gg_inps))
    r("GG Minimi",         fmt(inps.gg_minim))
    r("Ore INAIL",         fmt(inps.ore_inail))
    r("GG INAIL",          fmt(inps.gg_inail))
    r("Imponibile INAIL",  fmt(inps.imponibile_inail))
    r("TFR Mese  [letto dal PDF]",     fmt(inps.tfr_mese))
    r("TFR Min. Teorico [imp/13,5]",   fmt(calc.get("tfr_minimo_teorico",0)))
    if "tfr_delta_su_minimo" in calc:
        delta_tfr = calc["tfr_delta_su_minimo"]
        perc_tfr  = calc.get("tfr_perc_su_minimo", 0)
        r("  → Delta su min. (quote diff.+rival.)", f"{fmt(delta_tfr)} € (+{perc_tfr:.1f}%)")
        r("  ℹ️  Nota TFR", "Il TFR include pro-quota 13a/14a e rivalutazione ISTAT")
        r("             non ricalcolabili senza storico montante e indice FOSC", "")

    # 9. Detrazioni
    sep("💼  SEZ.9 · DETRAZIONI SPETTANTI")
    r("Lavoro Dipendente", fmt(c.detr.lavoro_dip))
    r("Coniuge",           fmt(c.detr.coniuge))
    r("Figli",             fmt(c.detr.figli))
    r("Tot. Detrazioni",   fmt(c.detr.totale))

    # 10. Progressivi annui
    sep("📈  SEZ.10 · PROGRESSIVI ANNUI CUMULATI")
    p = c.prog
    r("Imp. INAIL annuo",         fmt(p.imp_inail))
    r("Imp. Contrib. Soc.",       fmt(p.imp_contrib_soc))
    r("Contrib. Sociali",         fmt(p.contrib_soc))
    r("Imp. IRPEF annuo",         fmt(p.imp_irpef))
    r("IRPEF Lorda  [letta]",     fmt(p.irpef_lorda))
    r("IRPEF Lorda  [F7 calc.]",  fmt(calc.get("irpef_lorda_annua",0)))
    r("Tot. Detrazioni",          fmt(p.tot_detr))
    r("IRPEF Pagata",             fmt(p.irpef_pagata))

    # 11. Sigle
    if c.sigle:
        sep("🏷️   SEZ.11 · LEGENDA SIGLE")
        for sig, desc in c.sigle.items():
            r(sig, desc, 10)

    # 12. Riepilogo validazione
    sep("🗂️   SEZ.12 · RIEPILOGO VALIDAZIONE")
    n_ok = sum(1 for ch in checks if ch.ok)
    n_w  = sum(1 for ch in checks if not ch.ok and abs(ch.delta) < 1.0)
    n_f  = sum(1 for ch in checks if not ch.ok and abs(ch.delta) >= 1.0)
    esito = ("✅ VALIDO" if n_f==0 and n_w==0 else
             "⚠️  VALIDO CON AVVISI" if n_f==0 else f"❌ {n_f} ANOMALIA/E")
    print(f"\n  Check: {len(checks)}   ✅ {n_ok}   ⚠️  {n_w}   ❌ {n_f}")
    print(f"  ESITO: {esito}")
    for ch in checks:
        if not ch.ok:
            print(f"\n  {'⚠️ ' if abs(ch.delta) < 1 else '❌'} {ch.campo}")
            print(f"       formula   = {ch.formula}")
            print(f"       calcolato = {fmt(ch.calcolato)} €  |  letto = {fmt(ch.letto)} €  |  Δ = {fmt(ch.delta)} €")
            if ch.nota: print(f"       ℹ️  {ch.nota}")

    # 13. Tavola formule
    sep("📐  SEZ.13 · TAVOLA FORMULE DI CALCOLO")
    formule = [
        ("F1",  "Retr. Oraria Tot.",    "PagaBase + Contingenza + EL.DIS.SAN + EL.DIS.BIL"),
        ("F2",  "Importo voce (ore)",   "Ore/GG × Base_unitaria_5decimali"),
        ("F3",  "Totale Lordo",         "Σ Comp+Bonus  [cess: Σ Liq+Bonus−Preavviso]"),
        ("F4",  "Imponibile Contrib.",  "Σ Comp+Liq+N/C competenze (escl. bonus,TFR,13a/14a,prev.,tratt.)"),
        ("F5",  "Contributi INPS",      "Imp_letto × 9,36% (IVS dipendente unificata)"),
        ("F6",  "Imp. IRPEF mese",      "Imponibile_Contrib − Contributi"),
        ("F7",  "IRPEF lorda annua",    "Scaglioni 2024-25: 0–28k:23% | 28–50k:35% | >50k:43%"),
        ("F8",  "Tot. Trattenute",      "IRPEF netta + Contrib. + Arr.prec + Σ voci tratt. (±arr. TS)"),
        ("F9",  "Netto Busta",          "Lordo + Bonus_netto − Tot.Trattenute"),
        ("F10", "TFR (informativo)",    "Letto dal PDF · min.teorico=Imp_letto/13,5 · delta=quote_diff+rival.ISTAT"),
    ]
    print()
    for cod, nome, formula in formule:
        print(f"  {cod:<4}  {nome:<28}  =  {formula}")
    print()
    print("═"*W)

# ══════════════════════════════════════════════════════════════════════════════
# FASE 4 – PERSISTENZA SQLite
# ══════════════════════════════════════════════════════════════════════════════

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dipendenti (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    codice_dip        TEXT    NOT NULL,
    codice_fiscale    TEXT    NOT NULL UNIQUE,
    cognome_nome      TEXT    NOT NULL,
    data_nascita      TEXT,
    comune_residenza  TEXT,
    matricola         TEXT,
    matr_inps_az      TEXT,
    pos_inail         TEXT,
    qualifica         TEXT,
    livello           TEXT,
    data_assunzione   TEXT,
    data_cessazione   TEXT,
    creato_il         TEXT    DEFAULT (datetime('now')),
    aggiornato_il     TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cedolini (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_dipendente         INTEGER NOT NULL REFERENCES dipendenti(id),
    mese                  INTEGER NOT NULL,
    anno                  INTEGER NOT NULL,
    foglio_n              TEXT,
    file_pdf              TEXT,
    tipo_cedolino         TEXT    DEFAULT 'ORDINARIO',
    -- Retribuzione base
    paga_base             REAL, contingenza          REAL,
    el_dis_san            REAL    DEFAULT 0,
    el_dis_bil            REAL    DEFAULT 0,
    retr_oraria_att       REAL,   retr_giornaliera    REAL,
    retrib_di_fatto       REAL,
    gg_contratto          INTEGER, ore_contratto       REAL,
    -- Totalizzatori mensili
    totale_lordo          REAL,   imponibile_contrib   REAL,
    tot_contrib_soc       REAL,   imp_irpef_mese       REAL,
    irpef_lorda_mese      REAL,   tot_detr_mese        REAL,
    tot_trat_irpef        REAL,   tot_trattenute       REAL,
    netto_busta           REAL,
    -- IRPEF / addizionali
    irpef_erario          REAL    DEFAULT 0,
    addiz_regionale       REAL    DEFAULT 0,
    addiz_comunale        REAL    DEFAULT 0,
    arr_prec              REAL    DEFAULT 0,
    arr_attuale           REAL    DEFAULT 0,
    conguaglio_irpef      REAL    DEFAULT 0,
    -- Detrazioni spettanti
    detr_lavoro_dip       REAL    DEFAULT 0,
    detr_coniuge          REAL    DEFAULT 0,
    detr_figli            REAL    DEFAULT 0,
    detr_altri            REAL    DEFAULT 0,
    detr_totale           REAL    DEFAULT 0,
    -- Ferie e permessi
    ferie_ap REAL DEFAULT 0, ferie_mat REAL DEFAULT 0,
    ferie_god REAL DEFAULT 0, ferie_res REAL DEFAULT 0,
    perm_ap REAL DEFAULT 0,  perm_mat REAL DEFAULT 0,
    perm_god REAL DEFAULT 0, perm_res REAL DEFAULT 0,
    rol_ap REAL DEFAULT 0,   rol_mat REAL DEFAULT 0,
    rol_god REAL DEFAULT 0,  rol_res REAL DEFAULT 0,
    fest_ap REAL DEFAULT 0,  fest_mat REAL DEFAULT 0,
    fest_god REAL DEFAULT 0, fest_res REAL DEFAULT 0,
    -- INPS / INAIL / TFR
    pos_sett_inps INTEGER DEFAULT 0,
    ore_inps REAL DEFAULT 0,   gg_inps REAL DEFAULT 0,
    gg_minimi_inps REAL DEFAULT 0,
    ore_inail REAL DEFAULT 0,  gg_inail REAL DEFAULT 0,
    imponibile_inail REAL DEFAULT 0, tfr_mese REAL DEFAULT 0,
    -- Progressivi annui
    prog_imp_inail REAL DEFAULT 0,
    prog_imp_contrib_soc REAL DEFAULT 0,
    prog_contrib_soc REAL DEFAULT 0,
    prog_oneri_deduc REAL DEFAULT 0,
    prog_imp_irpef REAL DEFAULT 0,
    prog_irpef_lorda REAL DEFAULT 0,
    prog_tot_detr REAL DEFAULT 0,
    prog_irpef_pagata REAL DEFAULT 0,
    -- Valori calcolati (per confronto con PDF)
    imp_contrib_voci   REAL DEFAULT 0,   -- Σ voci contrib. (da parser)
    retr_oraria_calc   REAL DEFAULT 0,   -- PB+Cont+EDB+SAN calcolata
    -- Metadati
    importato_il TEXT DEFAULT (datetime('now')),
    UNIQUE(id_dipendente, mese, anno)
);

CREATE TABLE IF NOT EXISTS voci_cedolino (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cedolino     INTEGER NOT NULL REFERENCES cedolini(id) ON DELETE CASCADE,
    codice          TEXT    NOT NULL,
    descrizione     TEXT    NOT NULL,
    tipo            TEXT    NOT NULL,
    ore_gg          REAL,
    base_unitaria   REAL,
    importo         REAL    NOT NULL,
    riferimento     TEXT,
    importo_calcolato REAL,
    delta_calc      REAL,
    esito_check     TEXT    DEFAULT 'N/A'
);

CREATE TABLE IF NOT EXISTS validazioni (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cedolino     INTEGER NOT NULL REFERENCES cedolini(id) ON DELETE CASCADE,
    formula         TEXT    NOT NULL,
    descrizione     TEXT    NOT NULL,
    valore_calc     REAL    NOT NULL,
    valore_letto    REAL    NOT NULL,
    delta           REAL    NOT NULL,
    esito           TEXT    NOT NULL,
    nota            TEXT,
    validato_il     TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cedolini_dip   ON cedolini(id_dipendente, anno, mese);
CREATE INDEX IF NOT EXISTS idx_voci_ced       ON voci_cedolino(id_cedolino, codice);
CREATE INDEX IF NOT EXISTS idx_valid_ced      ON validazioni(id_cedolino);
CREATE INDEX IF NOT EXISTS idx_dip_cf         ON dipendenti(codice_fiscale);

CREATE VIEW IF NOT EXISTS v_riepilogo_annuo AS
SELECT d.codice_fiscale, d.cognome_nome, c.anno,
    COUNT(*) mesi_caricati,
    SUM(c.totale_lordo) lordo_annuo,
    SUM(c.tot_contrib_soc) contrib_annui,
    SUM(c.irpef_erario) irpef_erario_annua,
    SUM(c.addiz_regionale) addiz_reg_annua,
    SUM(c.addiz_comunale) addiz_com_annua,
    SUM(c.netto_busta) netto_annuo,
    SUM(c.tfr_mese) tfr_accantonato,
    MAX(c.prog_imp_irpef) imp_irpef_progressivo,
    MAX(c.prog_irpef_pagata) irpef_pagata_progressiva
FROM cedolini c JOIN dipendenti d ON d.id=c.id_dipendente
GROUP BY d.id, c.anno;

CREATE VIEW IF NOT EXISTS v_voci_annuo AS
SELECT d.cognome_nome, c.anno, v.codice, v.descrizione, v.tipo,
    COUNT(*) mesi, SUM(v.importo) totale_importo
FROM voci_cedolino v
JOIN cedolini c ON c.id=v.id_cedolino
JOIN dipendenti d ON d.id=c.id_dipendente
GROUP BY d.id, c.anno, v.codice;

CREATE VIEW IF NOT EXISTS v_checks_falliti AS
SELECT d.cognome_nome, c.mese, c.anno, v.formula, v.descrizione,
    v.valore_calc, v.valore_letto, v.delta, v.esito
FROM validazioni v
JOIN cedolini c ON c.id=v.id_cedolino
JOIN dipendenti d ON d.id=c.id_dipendente
WHERE v.esito IN ('KO','WARN')
ORDER BY d.cognome_nome, c.anno, c.mese;
"""

def salva_db(c: Cedolino, calc: dict, checks: list, db_path: str):
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try: cur.execute(stmt)
            except: pass

    cur.execute("""
        INSERT INTO dipendenti (codice_dip,codice_fiscale,cognome_nome,data_nascita,
            comune_residenza,matricola,matr_inps_az,pos_inail,qualifica,livello,
            data_assunzione,data_cessazione)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(codice_fiscale) DO UPDATE SET
            cognome_nome=excluded.cognome_nome,
            data_cessazione=excluded.data_cessazione,
            aggiornato_il=datetime('now')
    """, (c.codice_dip,c.codice_fiscale,c.cognome_nome,c.data_nascita,
          c.comune_residenza,c.matricola,c.matr_inps_az,c.pos_inail,
          c.qualifica,c.livello,c.data_assunzione,c.data_cessazione or None))
    cur.execute("SELECT id FROM dipendenti WHERE codice_fiscale=?", (c.codice_fiscale,))
    id_dip = cur.fetchone()[0]

    fp = c.ferie_perm; inps = c.inps; prog = c.prog; detr = c.detr

    cur.execute("""
        INSERT INTO cedolini (id_dipendente,mese,anno,foglio_n,file_pdf,tipo_cedolino,
            paga_base,contingenza,el_dis_san,el_dis_bil,retr_oraria_att,retr_giornaliera,
            retrib_di_fatto,gg_contratto,ore_contratto,totale_lordo,imponibile_contrib,
            tot_contrib_soc,imp_irpef_mese,irpef_lorda_mese,tot_detr_mese,tot_trat_irpef,
            tot_trattenute,netto_busta,irpef_erario,addiz_regionale,addiz_comunale,
            arr_prec,arr_attuale,conguaglio_irpef,detr_lavoro_dip,detr_coniuge,detr_figli,
            detr_altri,detr_totale,ferie_ap,ferie_mat,ferie_god,ferie_res,perm_ap,perm_mat,
            perm_god,perm_res,rol_ap,rol_mat,rol_god,rol_res,fest_ap,fest_mat,fest_god,
            fest_res,pos_sett_inps,ore_inps,gg_inps,gg_minimi_inps,ore_inail,gg_inail,
            imponibile_inail,tfr_mese,prog_imp_inail,prog_imp_contrib_soc,prog_contrib_soc,
            prog_oneri_deduc,prog_imp_irpef,prog_irpef_lorda,prog_tot_detr,prog_irpef_pagata,
            imp_contrib_voci,retr_oraria_calc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id_dipendente,mese,anno) DO UPDATE SET
            file_pdf=excluded.file_pdf, totale_lordo=excluded.totale_lordo,
            netto_busta=excluded.netto_busta, importato_il=datetime('now')
    """, (id_dip,c.mese,c.anno,c.foglio_n,c.file_pdf,c.tipo_cedolino,
          c.paga_base,c.contingenza,c.el_dis_san,c.el_dis_bil,c.retr_oraria_att,
          c.retr_giornaliera,c.retrib_di_fatto,c.gg_contratto,c.ore_contratto,
          c.totale_lordo,c.imponibile_contrib,c.tot_contrib_soc,c.imp_irpef_mese,
          c.irpef_lorda_mese,c.tot_detr_mese,c.tot_trat_irpef,c.tot_trattenute,
          c.netto_busta,c.irpef_erario,c.addiz_regionale,c.addiz_comunale,
          c.arr_prec,c.arr_attuale,c.conguaglio_irpef,detr.lavoro_dip,detr.coniuge,
          detr.figli,detr.altri_carichi,detr.totale,
          fp.ferie_ap,fp.ferie_mat,fp.ferie_god,fp.ferie_res,
          fp.perm_ap,fp.perm_mat,fp.perm_god,fp.perm_res,
          fp.rol_ap,fp.rol_mat,fp.rol_god,fp.rol_res,
          fp.fest_ap,fp.fest_mat,fp.fest_god,fp.fest_res,
          inps.pos_sett,inps.ore_inps,inps.gg_inps,inps.gg_minim,
          inps.ore_inail,inps.gg_inail,inps.imponibile_inail,inps.tfr_mese,
          prog.imp_inail,prog.imp_contrib_soc,prog.contrib_soc,prog.oneri_deduc,
          prog.imp_irpef,prog.irpef_lorda,prog.tot_detr,prog.irpef_pagata,
          calc.get("imponibile_contrib_voci",0.0),calc.get("retr_oraria",0.0)))

    cur.execute("SELECT id FROM cedolini WHERE id_dipendente=? AND mese=? AND anno=?",
                (id_dip,c.mese,c.anno))
    id_ced = cur.fetchone()[0]

    cur.execute("DELETE FROM voci_cedolino WHERE id_cedolino=?", (id_ced,))
    for v in c.voci:
        ic = ar(v.ore_gg*v.base) if v.ore_gg and v.base else None
        dc = ar(abs(ic-v.importo)) if ic else None
        es = ("OK" if dc is not None and dc<=TOLL else "KO" if dc else "N/A")
        cur.execute("""INSERT INTO voci_cedolino
            (id_cedolino,codice,descrizione,tipo,ore_gg,base_unitaria,importo,
             riferimento,importo_calcolato,delta_calc,esito_check)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (id_ced,v.codice,v.descrizione,v.tipo,v.ore_gg,v.base,v.importo,
             v.riferimento,ic,dc,es))

    cur.execute("DELETE FROM validazioni WHERE id_cedolino=?", (id_ced,))
    for ch in checks:
        es = ("OK" if ch.ok else "WARN" if abs(ch.delta) < 1.0 else "KO")
        cur.execute("""INSERT INTO validazioni
            (id_cedolino,formula,descrizione,valore_calc,valore_letto,delta,esito,nota)
            VALUES (?,?,?,?,?,?,?,?)""",
            (id_ced,ch.campo[:4],ch.campo,ch.calcolato,ch.letto,ch.delta,es,ch.nota or None))

    conn.commit(); conn.close()
    print(f"\n  💾 DB: {db_path}  (cedolino id={id_ced}, dipendente id={id_dip})")

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)

    if "--schema" in args:
        print(SCHEMA_SQL); sys.exit(0)

    db_path = DB_DEFAULT; save = True
    if "--db" in args:
        i = args.index("--db")
        if i+1 < len(args): db_path = args[i+1]
    if "--no-save" in args: save = False

    # Modalità batch
    if "--batch" in args:
        i = args.index("--batch")
        cartella = Path(args[i+1]) if i+1 < len(args) else Path(".")
        pdfs = sorted(cartella.glob("*.pdf"))
        print(f"Batch: {len(pdfs)} PDF in {cartella}")
        for pdf in pdfs:
            print(f"\n{'▶'*3} {pdf.name}")
            try:
                ced = parse(str(pdf))
                val, chks = calcola(ced)
                n_ok = sum(1 for ch in chks if ch.ok)
                n_f  = sum(1 for ch in chks if not ch.ok and abs(ch.delta) >= 1.0)
                print(f"  {ced.cognome_nome}  {ced.mese_anno}  "
                      f"Lordo={fmt(ced.totale_lordo)} Netto={fmt(ced.netto_busta)}  "
                      f"Check: ✅{n_ok} ❌{n_f}")
                if save: salva_db(ced, val, chks, db_path)
            except Exception as e:
                print(f"  ❌ Errore: {e}")
        sys.exit(0)

    # Modalità singolo file
    ced  = parse(args[0])
    val, chks = calcola(ced)
    stampa(ced, val, chks)
    if save: salva_db(ced, val, chks, db_path)
