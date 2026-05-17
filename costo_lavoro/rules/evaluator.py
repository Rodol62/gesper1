"""
Compat layer: usa l'implementazione canonica in costo_lavoro.engine.evaluator.
Manteniamo il modulo per non rompere import legacy.
"""

from costo_lavoro.engine.evaluator import RuleEvaluator

__all__ = ["RuleEvaluator"]
