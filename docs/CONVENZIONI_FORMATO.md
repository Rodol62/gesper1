# GESPER — Convenzioni formato (Italia)

Documento di riferimento per **tutta la procedura**: cosa memorizzare nel database e cosa mostrare in interfaccia.

## Date

| Aspetto | Regola |
| --- | --- |
| **Database** | Solo `DateField` / `DateTimeField` (valore Python `date` / `datetime`). Nessun cambio: il DB salva in forma canonica (es. SQLite/Postgres in formato data ISO). |
| **Schermate** | Visualizzazione **gg/mm/aaaa** (`d/m/Y`). Orari: **HH:mm** (24h). |
| **Django Admin** | Usa `FORMAT_MODULE_PATH` + `formats/it/formats.py` (`DATE_FORMAT`, `SHORT_DATE_FORMAT`, …). |
| **Template** | Preferire `date` con formato `d/m/Y` sui campi data (`{% load format_it_tags %}` e filtro `it_date` dove utile). Evitare il carattere pipe letterale nelle celle di tabella Markdown. |

**Input form:** `DATE_INPUT_FORMATS` in `settings.py` accetta **gg/mm/aaaa** e varianti, più **aaaa-mm-gg** per `type="date"` e integrazioni.

## Anno (filtri ed etichette)

- Nei **filtri** e nelle **etichette** l’anno è sempre **quattro cifre (aaaa)**, **senza** separatore delle migliaia (`2026`, mai `2.026`).
- Con `USE_THOUSAND_SEPARATOR = True` e `THOUSAND_SEPARATOR = '.'`, il rendering grezzo di un intero anno in template può diventare `2.026`: **non** usare `{{ anno }}` da solo in URL, hidden input o titoli.
- Nei template: preferire `{% load format_it_tags %}` e **`{{ anno|anno_it }}`** (anche per `anno_prev`, `anno_next`, `anno_corrente`), oppure `{{ anno|stringformat:"d" }}` o `{{ anno|unlocalize }}`.
- Per l’anno da una data: `{{ data|date:"Y" }}` o `{{ data|anno_it }}`.
- Evitare abbreviazioni a 2 cifre per l’anno in UI (salvo casi tecnici interni).

## Importi in euro

| Aspetto | Regola |
| --- | --- |
| **Database** | `DecimalField` / valori numerici senza formattazione (punto decimale interno Python). |
| **Schermata** | Separatore **migliaia `.`**, decimali **`,`**, **due cifre** dopo la virgola. |
| **Allineamento** | **A destra**; in tabella usare classi **`euro`** o **`text-euro`** (definite in `base.html`, con cifre tabulari). |
| **Template** | Caricare `format_it_tags`; simbolo € con `euro_it` o `num_it:2` senza simbolo (evitare pipe nelle celle tabella MD). |
| **Python (CSV, Admin, job)** | `from accounts.formatting import euro_it_str, num_it_str` — stessa logica di `number_format` con `use_l10n=True`. |

Impostazioni globali: `USE_THOUSAND_SEPARATOR`, `DECIMAL_SEPARATOR`, `THOUSAND_SEPARATOR`, `NUMBER_GROUPING` in `settings.py` (allineati all’italiano).

## Estensione progressiva

Le pagine esistenti possono essere aggiornate **una alla volta**: sostituire `floatformat:2` sugli importi con `euro_it` / `num_it` e aggiungere le classi `euro` sulle celle. **Già allineati:** Libro paga, dashboard portale, profilo, completa profilo; **candidato**; **accounts** (`assegna_proposta_candidato`, `candidato_admin_dettaglio`); **documenti** (`lista`, `upload_buste_massivo`); **consulente** (F24, partitario, candidati, proposta, approva); **presenze** `anteprima_cedolino_riepilogo`; **rapporto di lavoro** (simulazioni, modifica contratto CCNL tabellare, simulatori UI); **Python**: `ScaglioneIRPEF` / `DetrazioneLavoroDipendente` `__str__`, `ParametroCCNLChoiceField`, `_fmt` simulazione proposta, PDF proposta (`euro()`), nota Excel simulazione 2026, export CSV organico, Admin IRPEF/list display, script `_estrai_buste.py`. **Django Admin** libro paga storico e test motore (`change_form` riepilogo). **Anagrafiche** lista dipendenti: nessun importo in elenco. Altri moduli: applicare lo stesso schema dove compaiono importi.

## Middleware e contesto

- `LocaleMiddleware` attivo (dopo `SessionMiddleware`).
- `django.template.context_processors.i18n` nei `TEMPLATES` per coerenza con la lingua attiva.
