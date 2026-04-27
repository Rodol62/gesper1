"""
Codice fiscale italiano: validazione di controllo (DM 23/12/1976, circolari Agenzia Entrate)
e decodifica dei campi anagrafici (data di nascita, sesso, luogo di nascita da codice Belfiore).

Gestisce anche omocodia sulle posizioni numeriche (L->0, M->1, N->2, P->3, Q->4, R->5,
S->6, T->7, U->8, V->9) per validazione e decodifica.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

# Tabella posizioni dispari (0-indexed) per checksum Codice Fiscale
_CF_ODD = {
    '0': 1, '1': 0, '2': 5, '3': 7, '4': 9, '5': 13, '6': 15,
    '7': 17, '8': 19, '9': 21,
    'A': 1, 'B': 0, 'C': 5, 'D': 7, 'E': 9, 'F': 13, 'G': 15,
    'H': 17, 'I': 19, 'J': 21, 'K': 2, 'L': 4, 'M': 18, 'N': 20,
    'O': 11, 'P': 3, 'Q': 6, 'R': 8, 'S': 12, 'T': 14, 'U': 16,
    'V': 10, 'W': 22, 'X': 25, 'Y': 24, 'Z': 23,
}

# Mese di nascita: lettera ufficiale (pos. 9 del CF, 1-based = indice 8)
_CF_MESE = {
    'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'H': 6,
    'L': 7, 'M': 8, 'P': 9, 'R': 10, 'S': 11, 'T': 12,
}

# Omocodia cifre <-> lettere
_OMOCODIA_TO_DIGIT = {
    'L': '0',
    'M': '1',
    'N': '2',
    'P': '3',
    'Q': '4',
    'R': '5',
    'S': '6',
    'T': '7',
    'U': '8',
    'V': '9',
}

_CF_RE = re.compile(
    r'^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-EHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$',
)


def valida_cf(cf: str) -> bool:
    """True se il CF rispetta formato e carattere di controllo (algoritmo MEF)."""
    cf = (cf or '').strip().upper()
    if not _CF_RE.fullmatch(cf):
        return False
    totale = 0
    for i, c in enumerate(cf[:15]):
        if i % 2 == 0:
            totale += _CF_ODD[c]
        else:
            totale += ord(c) - ord('A') if c.isalpha() else int(c)
    atteso = chr(ord('A') + totale % 26)
    return cf[15] == atteso


def _digit_char(ch: str) -> str:
    c = (ch or '').upper()
    if c.isdigit():
        return c
    return _OMOCODIA_TO_DIGIT.get(c, '')


def _parse_num_pair(cf: str, idx_start: int) -> int | None:
    a = _digit_char(cf[idx_start:idx_start + 1])
    b = _digit_char(cf[idx_start + 1:idx_start + 2])
    if not a or not b:
        return None
    return int(a + b)


def _normalizza_belfiore(cf: str) -> str | None:
    if len(cf) < 15:
        return None
    c0 = cf[11].upper()
    if not ('A' <= c0 <= 'Z'):
        return None
    d1 = _digit_char(cf[12])
    d2 = _digit_char(cf[13])
    d3 = _digit_char(cf[14])
    if not (d1 and d2 and d3):
        return None
    return f"{c0}{d1}{d2}{d3}"


def _anno_da_yy(yy: int, oggi: date | None = None) -> int:
    """Due cifre anno → secolo plausibile (età 0–120 anni, non futuro)."""
    oggi = oggi or date.today()
    cand = [2000 + yy, 1900 + yy]
    ok = [y for y in cand if y <= oggi.year and (oggi.year - y) <= 120]
    if not ok:
        return 1900 + yy if 1900 + yy <= oggi.year else 2000 + yy
    return max(ok)


@dataclass(frozen=True)
class DecodificaCodiceFiscale:
    data_nascita: date
    sesso: str  # M o F
    codice_belfiore: str
    nascita_italiana: bool
    regione_nascita: str | None
    provincia_sigla: str | None
    comune_nome: str | None
    stato_estero_nome: str | None


def decodifica_codice_fiscale(cf: str, oggi: date | None = None) -> DecodificaCodiceFiscale | None:
    """
    Estrae data, sesso e luogo di nascita da un CF valido.
    Ritorna None se il CF non è valido o la data non è ammissibile.
    """
    from anagrafiche.territorio_it import comune_da_codice_catastale, denominazione_stato_da_codice_at

    cf = (cf or '').strip().upper()
    if not valida_cf(cf):
        return None
    oggi = oggi or date.today()
    try:
        yy = _parse_num_pair(cf, 6)
        if yy is None:
            return None
        mese = _CF_MESE.get(cf[8])
        if not mese:
            return None
        giorno_raw = _parse_num_pair(cf, 9)
        if giorno_raw is None:
            return None
        if giorno_raw > 40:
            sesso = 'F'
            giorno = giorno_raw - 40
        else:
            sesso = 'M'
            giorno = giorno_raw
        if giorno < 1 or giorno > 31:
            return None
        anno = _anno_da_yy(yy, oggi)
        data_nascita = date(anno, mese, giorno)
    except (ValueError, TypeError):
        return None
    if data_nascita > oggi:
        return None

    cod = _normalizza_belfiore(cf)
    if not cod:
        return None
    if cod.startswith('Z'):
        stato = denominazione_stato_da_codice_at(cod)
        return DecodificaCodiceFiscale(
            data_nascita=data_nascita,
            sesso=sesso,
            codice_belfiore=cod,
            nascita_italiana=False,
            regione_nascita=None,
            provincia_sigla=None,
            comune_nome=None,
            stato_estero_nome=stato,
        )

    info = comune_da_codice_catastale(cod)
    if not info:
        return DecodificaCodiceFiscale(
            data_nascita=data_nascita,
            sesso=sesso,
            codice_belfiore=cod,
            nascita_italiana=True,
            regione_nascita=None,
            provincia_sigla=None,
            comune_nome=None,
            stato_estero_nome=None,
        )
    return DecodificaCodiceFiscale(
        data_nascita=data_nascita,
        sesso=sesso,
        codice_belfiore=cod,
        nascita_italiana=True,
        regione_nascita=info.get('regione'),
        provincia_sigla=info.get('sigla'),
        comune_nome=info.get('comune'),
        stato_estero_nome=None,
    )


def serializza_decodifica(d: DecodificaCodiceFiscale) -> dict[str, Any]:
    out: dict[str, Any] = {
        'data_nascita': d.data_nascita.isoformat(),
        'sesso': d.sesso,
        'codice_belfiore': d.codice_belfiore,
        'nascita_italiana': d.nascita_italiana,
    }
    if d.nascita_italiana:
        out['nascita'] = {
            'italia': True,
            'regione': d.regione_nascita,
            'provincia': d.provincia_sigla,
            'comune': d.comune_nome,
            'codice_catastale': d.codice_belfiore,
        }
        out['nazionalita_suggerita'] = 'ITALIA'
    else:
        out['nascita'] = {
            'italia': False,
            'codice_at': d.codice_belfiore,
            'stato_estero': d.stato_estero_nome,
        }
        out['nazionalita_suggerita'] = (d.stato_estero_nome or '').upper() if d.stato_estero_nome else None
    return out


def _vuoto(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def merge_profilo_candidato_da_codice_fiscale(cleaned: dict[str, Any]) -> None:
    """
    Integra i dati anagrafici dal CF: data e sesso solo se ancora vuoti;
    luogo di nascita (Italia o estero) sempre allineato al CF se decodificabile.
    """
    cf = (cleaned.get('codice_fiscale') or '').strip().upper()
    if len(cf) != 16 or not valida_cf(cf):
        return
    dec = decodifica_codice_fiscale(cf)
    if not dec:
        return
    if _vuoto(cleaned.get('data_nascita')):
        cleaned['data_nascita'] = dec.data_nascita
    if _vuoto(cleaned.get('sesso')):
        cleaned['sesso'] = dec.sesso
    if dec.nascita_italiana and dec.regione_nascita and dec.provincia_sigla and dec.comune_nome:
        cleaned['regione_nascita'] = dec.regione_nascita
        cleaned['provincia_nascita'] = dec.provincia_sigla
        cleaned['comune_nascita'] = dec.comune_nome.upper()
        cleaned['luogo_nascita'] = dec.comune_nome.upper()
        if _vuoto(cleaned.get('nazionalita')):
            cleaned['nazionalita'] = 'ITALIA'
    elif not dec.nascita_italiana:
        cleaned['regione_nascita'] = 'ESTERO'
        if dec.stato_estero_nome:
            cleaned['comune_nascita_estero'] = dec.stato_estero_nome.upper()


def merge_dipendente_da_codice_fiscale(cleaned: dict[str, Any]) -> None:
    """Come merge_profilo: data/sesso se vuoti; luogo nascita da CF con priorità."""
    cf = (cleaned.get('codice_fiscale') or '').strip().upper()
    if len(cf) != 16 or not valida_cf(cf):
        return
    dec = decodifica_codice_fiscale(cf)
    if not dec:
        return
    if _vuoto(cleaned.get('data_nascita')):
        cleaned['data_nascita'] = dec.data_nascita
    if _vuoto(cleaned.get('sesso')):
        cleaned['sesso'] = dec.sesso
    if dec.nascita_italiana and dec.regione_nascita and dec.provincia_sigla and dec.comune_nome:
        cleaned['regione_nascita'] = dec.regione_nascita
        cleaned['provincia_nascita'] = dec.provincia_sigla
        cleaned['comune_nascita'] = dec.comune_nome.upper()
        if _vuoto(cleaned.get('luogo_nascita')):
            cleaned['luogo_nascita'] = dec.comune_nome.upper()
        if _vuoto(cleaned.get('paese_nascita')):
            cleaned['paese_nascita'] = 'ITALIA'
        if _vuoto(cleaned.get('cittadinanza')):
            cleaned['cittadinanza'] = 'ITALIANA'
    elif not dec.nascita_italiana and dec.stato_estero_nome:
        cleaned['regione_nascita'] = 'ESTERO'
        cleaned['paese_nascita'] = dec.stato_estero_nome.upper()
        cleaned['comune_nascita_estero'] = dec.stato_estero_nome.upper()
