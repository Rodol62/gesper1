"""
Riferimento unico ai motori retributivi Gesper.

Qualsiasi modifica a logica busta/simulazione/conciliazione «lato calcolo nostro»
deve passare dal **motore busta paga**. Qualsiasi ingestione da PDF cedolino deve
passare dal **motore cedolino** (pipeline di acquisizione).

Motore busta paga (simulazione, test admin, confronto con cedolino lato calcolo)
---------------------------------------------------------------------------
- Implementazione: :func:`rapporto_di_lavoro.utils_motore_paga.calcola_busta_paga_mese`
- Chiamata consigliata (log strutturato):
  :func:`rapporto_di_lavoro.services_simulazione.invoca_calcola_busta_paga_mese`

Motore cedolino (lettura analitica PDF → DB / conciliazione lettura)
--------------------------------------------------------------------
- Pipeline unica PDF:
  :func:`documenti.busta_acquisizione.acquisisci_busta_pdf_bytes`
  (prima ``posizionale_v4``, poi fallback testo marcato ``legacy_testo``).
- Persistenza righe v4: modelli ``CedolinoMotoreV4`` / ``VoceCedolinoMotoreV4``
  (vedi ``documenti.cedolino_estrazione_v4_store``).
- Stesso mese, più PDF (es. ordinaria + 13ª a dicembre, ordinaria + 14ª a luglio): distinzione
  con ``natura_busta`` in DB — vedi ``documenti.natura_busta_utils``.
- Conciliazione cedolino vs motore busta (``documenti.cedolino_conciliazione_motore_paga``): per
  competenze **prima** del ``2026-03-01``: con **ROF** su ``CedolinoMotoreV4`` si omette la griglia mensile
  ruolo (presenze non ancora allineate al consulente); senza ROF si usa **fallback** alla stessa griglia
  storica per non azzerare le ore del motore. **Dal 01/03/2026 inclusivo** la griglia ruolo è la fonte
  operativa quando presente (``usa_calendario_ruolo_organico_in_conciliazione`` +
  ``cedolino_motore_v4_ha_rof_per_conciliazione``).

Premessa: stessi «dati di regola» della procedura e del consulente
-------------------------------------------------------------------
La procedura assume che **i parametri di input** (tabelle CCNL, aliquote, coefficienti,
calendari, presenze) siano **gli stessi** che il consulente applica nel proprio software
per predisporre le buste paga. Non si tratta di imitare il programma del consulente,
ma di **condividere il medesimo insieme di regole oggettive** (contratti collettivi,
leggi nazionali, decreti) così che simulazione e cedolino erogato siano confrontabili.

**Dominio dati in comune** (da tenere allineati tra Gesper e consulente):

- **Parametri contrattuali CCNL**: voci e livelli retributivi, maggiorazioni, ratei,
  casistiche previste dal testo contrattuale (oggettive, non discrezionali).
- **Presenze / tempo**: giorni e ore lavorate, ordinarie, domenicali, festive,
  straordinari e assimilati, secondo le stesse regole di conteggio concordate.
- **Ratei e accantonamenti**: 13ª, 14ª, TFR, ferie/ROL o equivalenti, coerenti con CCNL e contratto.
- **Imponibili e contributi**: basi e aliquote INPS, INAIL, IRPEF, addizionali regionali
  e comunali, bonus normativi (es. agevolazioni fiscali previste da legge), decontribuzioni
  ove applicabili — sempre riferiti alle **stesse fonti normative** e alle stesse date
  di decorrenza/validità dei parametri in anagrafica.

Scostamenti residui dopo l’allineamento dei dati indicano divergenze di **implementazione**
o di **interpretazione** rispetto al software del consulente, da analizzare voce per voce,
non differenze arbitrarie di «due mondi» di parametri.

Prestazioni (loop simulazioni / proposte)
-----------------------------------------
Evitare query ridondanti su ``ParametroRatei`` / ``CCNL`` nei loop esterni attorno al
motore. Dentro ``utils_motore_paga`` le letture ratei più frequenti sono state
compatte (es. ``anno_efficace_parametro_ratei``, flag 13ª/14ª in
``ricava_parametri_proposta_contrattuale``). La simulazione annua 2026 precarica
dipendenti/contratti per mese e compatta i ``ParametroRatei`` di coefficiente.

Percorsi da non usare come sostituti del motore busta (stime semplificate / legacy)
----------------------------------------------------------------------------------
- :func:`rapporto_di_lavoro.utils_calcoli.calcola_completo` e simili: mattoni o
  stime rapide, non rappresentano la busta mensile completa del motore canonico.
- Proprietà convenienza su modelli (es. netto da solo lordo tabellare) non
  sostituiscono ``calcola_busta_paga_mese`` per simulazioni o conciliazione.

Parità simulazione locale / produzione
--------------------------------------
Il motore **non** applica formule diverse in base a ``DEBUG`` o al tipo di deploy:
gli importi dipendono quasi sempre da **contesto e database**, non dal branch «prod vs dev».

Per ottenere gli stessi numeri in locale che in produzione servono, al minimo:

- **Stesso file SQLite** (o stesso dump dei parametri): in produzione ``settings_production``
  usa ``GESPER_DATA_ROOT/db.sqlite3``. In sviluppo, ``settings.py`` sceglie lo stesso file
  se esiste sotto ``GESPER_DATA_ROOT`` (default ``documento/``), altrimenti il legacy
  ``gesper/db.sqlite3``. Se in locale avevi *due* copie del DB, verificare con
  ``diagnostica_ambiente_simulazione`` quale path è **effettivo**. Due DB diversi ⇒ parametri
  e anagrafiche diverse.
- **Stessa azienda operativa in sessione** e stesso dipendente/contratto/mese simulato.
- **Stessi dati di presenza / ROF** se la simulazione li legge dal ruolo organico o dalla griglia mensile.

Per un confronto rapido tra ambienti: ``python manage.py diagnostica_ambiente_simulazione``
(su ciascuna macchina o dopo aver copiato il DB) stampa path effettivo del DB e conteggi
indicativi sui parametri motore.
"""

from __future__ import annotations

# Identificativi stringa coerenti con output ``ricava_parametri_proposta_contrattuale`` / report
MOTORE_PAGA_CANONICO_ORIGINE = "motore_paga_canonico_default"

# Allineati a ``documenti.busta_acquisizione`` / store v4
MOTORE_CEDOLINO_POSIZIONALE_V4 = "posizionale_v4"
MOTORE_CEDOLINO_LEGACY_TESTO = "legacy_testo"

__all__ = [
    "MOTORE_CEDOLINO_LEGACY_TESTO",
    "MOTORE_CEDOLINO_POSIZIONALE_V4",
    "MOTORE_PAGA_CANONICO_ORIGINE",
]
