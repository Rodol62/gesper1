import re
from django.core.exceptions import ValidationError


class CodiceFiscalePasswordValidator:
    """
    Password iniziale = codice fiscale (16 caratteri alfanumerici, formato CF italiano).
    Usato solo nella registrazione candidato (non nei validatori globali AUTH_PASSWORD_VALIDATORS).
    """

    _CF = re.compile(r'^[A-Z0-9]{16}$')

    def validate(self, password, user=None):
        p = (password or '').strip().upper()
        if len(p) != 16 or not self._CF.match(p):
            raise ValidationError(
                'Il codice fiscale deve essere di 16 caratteri (lettere e numeri, es. RSSMRA85M01H501Z).',
                code='codice_fiscale_invalido',
            )

    def get_help_text(self):
        return 'Inserisci il codice fiscale su 16 caratteri: sarà la password iniziale del tuo account.'


class PasswordForteValidator:
    """
    Valida che la password soddisfi i requisiti di sicurezza elevata:
      - almeno 12 caratteri
      - almeno 1 lettera MAIUSCOLA
      - almeno 1 lettera minuscola
      - almeno 1 cifra
      - almeno 1 carattere speciale (!@#$%^&*...)
    """

    SPECIAL = r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>?/\\|`~]'

    def validate(self, password, user=None):
        errors = []
        if len(password) < 12:
            errors.append(ValidationError(
                "La password deve contenere almeno 12 caratteri.",
                code='password_too_short',
            ))
        if not re.search(r'[A-Z]', password):
            errors.append(ValidationError(
                "La password deve contenere almeno una lettera maiuscola (A-Z).",
                code='password_no_upper',
            ))
        if not re.search(r'[a-z]', password):
            errors.append(ValidationError(
                "La password deve contenere almeno una lettera minuscola (a-z).",
                code='password_no_lower',
            ))
        if not re.search(r'\d', password):
            errors.append(ValidationError(
                "La password deve contenere almeno una cifra (0-9).",
                code='password_no_digit',
            ))
        if not re.search(self.SPECIAL, password):
            errors.append(ValidationError(
                "La password deve contenere almeno un carattere speciale "
                "(!  @  #  $  %  ^  &  *  -  _  =  +  ecc.).",
                code='password_no_special',
            ))
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return (
            "La password deve avere almeno 12 caratteri e includere: "
            "lettere maiuscole e minuscole, cifre e caratteri speciali "
            "(!  @  #  $  %  &  *  ...)."
        )
