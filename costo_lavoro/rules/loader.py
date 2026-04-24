"""
Compat layer: usa l'implementazione canonica in costo_lavoro.engine.loader.
Manteniamo il modulo per non rompere import legacy.
"""

from costo_lavoro.engine.loader import RuleLoader

__all__ = ["RuleLoader"]
