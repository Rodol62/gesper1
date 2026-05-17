"""
Compat layer: usa l'implementazione canonica in costo_lavoro.engine.rule_engine.
Manteniamo il modulo per non rompere import legacy.
"""

from costo_lavoro.engine.rule_engine import RuleEngine

__all__ = ["RuleEngine"]
