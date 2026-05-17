class Decontribuzioni:
    """
    Rappresenta una decontribuzione applicabile:
    tipo: 'percentuale', 'massimale', 'esonero_totale', 'esonero_parziale'
    """

    def __init__(self, tipo: str, valore: float = 0.0, massimale_mensile: float = 0.0):
        self.tipo = tipo
        self.valore = valore
        self.massimale_mensile = massimale_mensile
