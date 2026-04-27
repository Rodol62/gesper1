import json
import csv
from functools import lru_cache
from pathlib import Path
from datetime import datetime


DATASET_PATH = Path(__file__).resolve().parent / 'data' / 'comuni_italiani.json'
ISTAT_COMUNI_CSV = Path(__file__).resolve().parent / 'data' / 'istat_comuni_italiani.csv'
ISTAT_STATI_CSV = (
    Path(__file__).resolve().parent
    / 'data'
    / 'istat_stati_esteri'
    / 'Elenco-codici-e-denominazioni-unita-territoriali-estere'
    / 'Elenco-codici-e-denominazioni-al-31_12_2023.csv'
)
URL_ISTAT_COMUNI = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
URL_ISTAT_STATI = "https://www.istat.it/wp-content/uploads/2024/03/Elenco-codici-e-denominazioni-unita-territoriali-estere.zip"


def _norm(value):
    return (value or '').strip().upper()


@lru_cache(maxsize=1)
def _load_dataset():
    with DATASET_PATH.open('r', encoding='utf-8') as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _load_istat_comuni():
    if not ISTAT_COMUNI_CSV.exists():
        return []
    rows = []
    with ISTAT_COMUNI_CSV.open('r', encoding='latin-1', newline='') as handle:
        reader = csv.reader(handle, delimiter=';')
        header = next(reader, [])
        # Indici da intestazioni ufficiali ISTAT
        idx_regione = next((i for i, h in enumerate(header) if 'Denominazione Regione' in h), None)
        idx_provincia = next((i for i, h in enumerate(header) if "Denominazione dell'Unità territoriale sovracomunale" in h), None)
        idx_sigla = next((i for i, h in enumerate(header) if 'Sigla automobilistica' in h), None)
        idx_comune = next((i for i, h in enumerate(header) if h.strip() == 'Denominazione in italiano'), None)
        idx_catasto = next((i for i, h in enumerate(header) if 'Codice Catastale del comune' in h), None)
        if None in (idx_regione, idx_provincia, idx_sigla, idx_comune):
            return []
        for row in reader:
            max_idx = max([x for x in (idx_regione, idx_provincia, idx_sigla, idx_comune, idx_catasto) if x is not None])
            if len(row) <= max_idx:
                continue
            rows.append(
                {
                    'regione': _norm(row[idx_regione]),
                    'provincia': _norm(row[idx_provincia]),
                    'sigla': _norm(row[idx_sigla]),
                    'comune': _norm(row[idx_comune]),
                    'codice_catastale': _norm(row[idx_catasto]) if idx_catasto is not None else '',
                    'cap': '',
                }
            )
    return rows


@lru_cache(maxsize=1)
def regioni():
    istat = _load_istat_comuni()
    if istat:
        regs = {_norm(item.get('regione')) for item in istat}
        return sorted([r for r in regs if r])
    regs = {_norm(item.get('regione', {}).get('nome')) for item in _load_dataset()}
    return sorted([r for r in regs if r])


@lru_cache(maxsize=64)
def province_per_regione(regione):
    reg = _norm(regione)
    if not reg:
        return []
    istat = _load_istat_comuni()
    if istat:
        province = {
            (_norm(item.get('provincia')), _norm(item.get('sigla')))
            for item in istat
            if _norm(item.get('regione')) == reg
        }
    else:
        province = {
            (_norm(item.get('provincia', {}).get('nome')), _norm(item.get('sigla')))
            for item in _load_dataset()
            if _norm(item.get('regione', {}).get('nome')) == reg
        }
    out = []
    for nome, sigla in sorted(province, key=lambda x: (x[0], x[1])):
        if nome:
            out.append({'nome': nome, 'sigla': sigla})
    return out


@lru_cache(maxsize=512)
def comuni_per_regione_provincia(regione, provincia):
    reg = _norm(regione)
    prov = _norm(provincia)
    if not reg or not prov:
        return []
    comuni = []
    seen = set()
    istat = _load_istat_comuni()
    if istat:
        source = istat
        for item in source:
            if _norm(item.get('regione')) != reg:
                continue
            prov_nome = _norm(item.get('provincia'))
            prov_sigla = _norm(item.get('sigla'))
            if prov not in (prov_nome, prov_sigla):
                continue
            nome = _norm(item.get('comune'))
            if not nome or nome in seen:
                continue
            seen.add(nome)
            comuni.append({
                'nome': nome,
                'cap': '',
                'codice_catastale': _norm(item.get('codice_catastale')),
            })
    else:
        for item in _load_dataset():
            if _norm(item.get('regione', {}).get('nome')) != reg:
                continue
            prov_nome = _norm(item.get('provincia', {}).get('nome'))
            prov_sigla = _norm(item.get('sigla'))
            if prov not in (prov_nome, prov_sigla):
                continue
            nome = _norm(item.get('nome'))
            if not nome or nome in seen:
                continue
            seen.add(nome)
            cap = ''
            caps = item.get('cap') or []
            if isinstance(caps, list) and caps:
                cap = str(caps[0]).strip()
            comuni.append({
                'nome': nome,
                'cap': cap,
                'codice_catastale': _norm(item.get('codice_catastale')),
            })
    comuni.sort(key=lambda x: x['nome'])
    return comuni


@lru_cache(maxsize=1)
def paesi_istat():
    if not ISTAT_STATI_CSV.exists():
        return []
    items = []
    with ISTAT_STATI_CSV.open('r', encoding='latin-1', newline='') as handle:
        reader = csv.DictReader(handle, delimiter=';')
        for row in reader:
            if _norm(row.get('Stato(S)/Territorio(T)')) != 'S':
                continue
            nome = _norm(row.get('Denominazione IT'))
            codice_at = _norm(row.get('Codice AT'))
            if not nome:
                continue
            items.append({'nome': nome, 'codice_at': codice_at})
    # deduplica mantenendo ordinamento alfabetico
    uniq = {}
    for it in items:
        uniq[it['nome']] = it
    return [uniq[k] for k in sorted(uniq.keys())]


@lru_cache(maxsize=512)
def comune_da_codice_catastale(codice: str) -> dict | None:
    """
    Risolve regione, sigla provincia e nome comune dal codice catastale/Belfiore (4 caratteri, es. H501).
    In caso di più comuni storici con stesso codice si usa il primo match nel dataset ISTAT.
    """
    c = _norm(codice)
    if len(c) != 4:
        return None
    for item in _load_istat_comuni():
        cc = _norm(item.get('codice_catastale'))
        if cc == c:
            return {
                'regione': item.get('regione'),
                'provincia': item.get('provincia'),
                'sigla': item.get('sigla'),
                'comune': item.get('comune'),
                'codice_catastale': cc,
            }
    return None


@lru_cache(maxsize=1)
def _mappa_codice_at_stato_estero():
    """Codice AT (es. Z100) → denominazione italiana ISTAT dello stato estero."""
    if not ISTAT_STATI_CSV.exists():
        return {}
    out: dict[str, str] = {}
    with ISTAT_STATI_CSV.open('r', encoding='latin-1', newline='') as handle:
        reader = csv.DictReader(handle, delimiter=';')
        for row in reader:
            if _norm(row.get('Stato(S)/Territorio(T)')) != 'S':
                continue
            at = _norm(row.get('Codice AT'))
            nome = _norm(row.get('Denominazione IT'))
            if at and nome and len(at) >= 3 and at not in out:
                out[at] = nome
    return out


def denominazione_stato_da_codice_at(codice_at: str) -> str | None:
    """Denominazione IT dello stato da codice AT a 4 caratteri (es. Z100 → Albania)."""
    return _mappa_codice_at_stato_estero().get(_norm(codice_at))


def _fmt_mtime(path: Path):
    if not path.exists():
        return ''
    return datetime.fromtimestamp(path.stat().st_mtime).strftime('%d/%m/%Y %H:%M')


def dataset_sources_info():
    return {
        'comuni_file': str(ISTAT_COMUNI_CSV),
        'comuni_url': URL_ISTAT_COMUNI,
        'comuni_updated_at': _fmt_mtime(ISTAT_COMUNI_CSV),
        'stati_file': str(ISTAT_STATI_CSV),
        'stati_url': URL_ISTAT_STATI,
        'stati_updated_at': _fmt_mtime(ISTAT_STATI_CSV),
    }
