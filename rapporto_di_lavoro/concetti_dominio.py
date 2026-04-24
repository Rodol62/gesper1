# -*- coding: utf-8 -*-
"""
Concetti di dominio — proposta, contratto, candidato, dipendente (riferimento normativo-prodotto).

Questo modulo non contiene logica eseguibile obbligatoria: documenta il modello concettuale
concordato con il committente, così viste, form e migrazioni future restano allineate.

-------------------------------------------------------------------------------
1) CANDIDATO
-------------------------------------------------------------------------------
- Soggetto che si registra sul portale e completa il profilo secondo le regole di convalida HR/admin.
- Riceve una *proposta di assunzione* creata dall'amministrazione (stato bozza → inviata → …).
- Deve *accettare* la proposta (firma digitale / accettazione esplicita prevista dal flusso).
- L'amministrazione, dopo l'accettazione del candidato, *firma in via definitiva* e *trasforma*
  la pratica nel *contratto definitivo* (Rapporto di lavoro in essere nel gestionale).
- Con il contratto definitivo il candidato *esce dallo stato di candidato* e *diventa dipendente*
  (anagrafica `Dipendente.stato = attivo`, utente con ruolo dipendente dove applicabile).

Nota implementativa (codice attuale):
- La promozione a dipendente e l'attivazione anagrafica avvengono in `PropostaAssunzione.firma_definitiva_admin`
  (firma datore su proposta già firmata dal candidato) e, in un percorso alternativo di firma
  contratto in bozza, anche in `accetta_contratto_dipendente` quando il rapporto passa da
  `proposta` a `sottoscritto`. Il flusso canonico descritto dal committente resta:
  accettazione candidato sulla proposta → firma definitiva admin → contratto definitivo.

-------------------------------------------------------------------------------
2) DIPENDENTE
-------------------------------------------------------------------------------
- Soggetto in anagrafica (`Dipendente`) che ha un *contratto definitivo* (rapporto sottoscritto /
  gestito come attivo nel sistema) oppure, per *candidature storiche* (pre-digitale), è stato
  inquadrato senza la procedura attuale ma non risulta *cessato*.
- Non è dipendente "operativo" chi è solo candidato senza rapporto definitivo, né chi è cessato
  salvo reinserimenti espliciti gestiti da HR.
- In termini di dati: la *posizione* e i *parametri retributivi* devono riflettere l'ultimo
  atto applicabile (contratto + eventuali addendum/variazioni registrate).

-------------------------------------------------------------------------------
3) LOGICA CONTRATTUALE
-------------------------------------------------------------------------------
*Tempo indeterminato (TI)*
- Ha data di inizio; non ha scadenza naturale: resta valido fino a dimissioni volontarie del
  dipendente o fino a recesso/licenziamento da parte dell'azienda (giusta causa, esigenze
  organizzative / contrazione forza lavoro, ecc., secondo legge e CCNL).

*Tempo determinato (TD)*
- Ha data di inizio e data di fine.
- Il sistema deve fornire *evidenza delle scadenze* per consentire, *almeno entro 30 giorni*
  dalla scadenza, la preparazione di: *proroga* o *rinnovo* (mantenendo o variando i parametri
  contrattuali precedenti), oppure l'assenza di rinnovo con *cessazione automatica* del
  dipendente alla scadenza (da modellare nei flussi e nelle notifiche).

*Variazioni in corso di rapporto*
- Ogni modifica sostanziale (livello retributivo, passaggio full-time ↔ part-time o tra
  diverse percentuali part-time, tipo contratto, ecc.) va documentata come *integrazione /
  variazione* rispetto ai contratti precedenti, *conservando la storia* dei parametri.
- I parametri retributivi effettivi e la posizione devono essere *visibili nel profilo del
  dipendente* e nella *posizione / scheda dipendente* lato admin e consulente del lavoro.

Implementazione attuale (prodotto):
- `AddendumContrattuale` su `RapportoDiLavoro` (storico; opzionale sincronizzazione campi sul rapporto).
- Elenco scadenze TD (≤30 gg) e TD scaduti ancora aperti: `lista_contratti_scadenza` + servizi
  `contratti_td_in_scadenza` / `contratti_td_scaduti_non_chiusi`; banner in `lista_proposte` (admin/HR).
- Cessazione batch TD scaduti: `applica_cessazioni_td_scadute` + comando `applica_scadenze_contratti_td`.
- Posizione e addendum su scheda dipendente, modifica dipendente (admin/HR/consulente) e documenti
  consulente: `posizione_contrattuale_per_dipendente` + partial `_profilo_posizione_contrattuale.html`.
- Consulente: lettura scheda contratto se stessa azienda (`_get_contratto_con_permesso`); addendum solo admin/HR.

-------------------------------------------------------------------------------
Riferimenti codice (punti di ingresso)
-------------------------------------------------------------------------------
- Proposta / firma definitiva: `rapporto_di_lavoro.models.PropostaAssunzione.firma_definitiva_admin`
- Accettazione contratto bozza (percorso alternativo): `accounts.views_candidato.accetta_contratto_dipendente`
- Storico variazioni contratto: `rapporto_di_lavoro.models.AddendumContrattuale`
"""

# Elenco evolutivo esplicito per grep / pianificazione (non usato a runtime obbligatoriamente).
EVOLUZIONI_DOMINIO_CONTRATTO = (
	'notifiche_email_scadenza_td',
	'wizard_rinnovo_td_da_scadenza',
	'export_storico_retributivo',
)
