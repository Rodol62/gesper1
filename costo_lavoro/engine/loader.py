import json
import os
from costo_lavoro.logger import logger

class RuleLoader:

    def __init__(self, base_path=None):
        if base_path is None:
            base_path = os.path.join(os.path.dirname(__file__), "..", "rules")
        self.base_path = os.path.abspath(base_path)

    def load(self, filename):
        path = os.path.join(self.base_path, filename)
        logger.info(f"Carico regole da: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
