from github_copilot_sdk import Copilot
import os

agent = Copilot()

task = """
Sei un agente di refactoring. Analizza l'intero repository e applica queste regole:

1. Identifica funzioni troppo lunghe e suddividile.
2. Rimuovi codice duplicato.
3. Applica PEP8.
4. Aggiungi type hints a tutte le funzioni.
5. Migliora i nomi delle variabili.
6. Se trovi classi disorganizzate, riorganizzale.
7. Aggiorna gli import in modo coerente.
8. Genera test unitari per i moduli modificati.

Applica le modifiche direttamente ai file del progetto.
Restituisci un riepilogo finale delle modifiche effettuate.
"""

result = agent.run(task)

print("\n=== RISULTATO AGENTE ===\n")
print(result.output)
