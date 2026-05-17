class DatiContrattuali:
    """
    Dati contrattuali di base del lavoratore.
    """

    def __init__(
        self,
        retribuzione_lorda_mensile: float,
        giorni_lavorativi_mese: int,
        giorni_lavorati: int,
        mensilita: int,
        ore_settimanali: int,
        livello: str,
        ccnl: str,
    ):
        self.retribuzione_lorda_mensile = retribuzione_lorda_mensile
        self.giorni_lavorativi_mese = giorni_lavorativi_mese
        self.giorni_lavorati = giorni_lavorati
        self.mensilita = mensilita
        self.ore_settimanali = ore_settimanali
        self.livello = livello
        self.ccnl = ccnl
