from costo_lavoro.logger import logger

class ConditionEvaluator:

    @staticmethod
    def _compare(op, left, right):
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == "in":
            return left in right
        if op == "contains":
            return right in left
        return False

    @staticmethod
    def match(rule, conditions):
        """
        conditions: dict semplice, es:
        {
            "ccnl": "turismo",
            "dimensione": 12,
            "regione": "sicilia"
        }

        la regola può contenere:
        {
            "field": "dimensione",
            "op": "<=",
            "value": 15
        }
        oppure chiavi dirette: "ccnl": "turismo"
        """
        # 1. condizioni semplici (chiave diretta)
        for key, value in conditions.items():
            if key in rule and not isinstance(rule[key], dict):
                if rule[key] != value:
                    return False

        # 2. condizioni avanzate (lista di condizioni)
        advanced = rule.get("conditions", [])
        for cond in advanced:
            field = cond.get("field")
            op = cond.get("op", "==")
            expected = cond.get("value")

            if field not in conditions:
                logger.debug(f"Campo {field} non presente nelle condizioni")
                return False

            actual = conditions[field]

            if not ConditionEvaluator._compare(op, actual, expected):
                return False

        return True
