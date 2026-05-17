from costo_lavoro.engine.conditions import ConditionEvaluator
from costo_lavoro.logger import logger

class RuleEvaluator:

    def find(self, rules, **conditions):
        logger.debug(f"Ricerca regole con condizioni: {conditions}")

        # Compatibilità: alcuni file possono avere top-level dict invece di lista
        if isinstance(rules, dict):
            rules = [rules]
        elif not isinstance(rules, list):
            logger.warning("Formato regole non supportato, restituisco dizionario vuoto")
            return {}

        for rule in rules:
            if not isinstance(rule, dict):
                continue

            if ConditionEvaluator.match(rule, conditions):
                logger.info(f"Regola trovata: {rule.get('name', 'senza nome')}")

                # Se manca 'values', restituisci l'intera regola (utile per tabelle classificazione)
                if "values" in rule:
                    return rule.get("values", {})
                return rule

        logger.warning("Nessuna regola trovata, restituisco dizionario vuoto")
        return {}
