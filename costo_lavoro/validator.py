class Validator:
    @staticmethod
    def require(field, value):
        if value is None:
            raise ValueError(f"Campo obbligatorio mancante: {field}")
