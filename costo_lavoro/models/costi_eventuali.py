class CostiEventuali:
    """
    Costi extra non obbligatori ma ricorrenti:
    fringe benefit, buoni pasto, welfare, ecc.
    """

    def __init__(self, fringe_benefit=0.0, buoni_pasto=0.0, welfare=0.0):
        self.fringe_benefit = fringe_benefit
        self.buoni_pasto = buoni_pasto
        self.welfare = welfare

    @property
    def totale(self):
        return self.fringe_benefit + self.buoni_pasto + self.welfare
