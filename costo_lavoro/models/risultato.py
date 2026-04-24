class RisultatoCostoLavoro:
    """
    Contiene il dettaglio del costo del lavoro.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_dict(self):
        return self.__dict__
