class DatiContributivi:
    """
    Contiene tutte le aliquote e contributi a carico azienda
    derivati dal motore di regole (INPS, INAIL, FIS, NASpI, ecc.).
    """

    def __init__(self, **kwargs):
        # es: aliquota_inps_azienda, aliquota_inail, contributo_fis, contributo_naspi, ecc.
        self.__dict__.update(kwargs)

    def get(self, name, default=0):
        return getattr(self, name, default)
