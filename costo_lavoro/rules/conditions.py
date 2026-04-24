"""
Compat layer: usa l'implementazione canonica in costo_lavoro.engine.conditions.
Manteniamo il modulo per non rompere import legacy.
"""

from costo_lavoro.engine.conditions import ConditionEvaluator

__all__ = ["ConditionEvaluator"]
