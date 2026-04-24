from costo_lavoro.engine.loader import RuleLoader
from costo_lavoro.engine.evaluator import RuleEvaluator
from costo_lavoro.logger import logger

class RuleEngine:

    def __init__(self, rules_path=None):
        self.loader = RuleLoader(rules_path)
        self.evaluator = RuleEvaluator()

    def get(self, category, **conditions):
        """
        category: nome file JSON senza estensione (es. 'inps', 'inail')
        conditions: es. ccnl="turismo", dimensione=12, regione="sicilia"
        """
        logger.info(f"Richiesta regole per categoria: {category}, condizioni: {conditions}")
        rules = self.loader.load(f"{category}.json")
        values = self.evaluator.find(rules, **conditions)
        logger.debug(f"Valori restituiti: {values}")
        return values
