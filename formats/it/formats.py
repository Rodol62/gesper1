# Formati visualizzazione per lingua italiana (GESPER).
# Le date in database restano campi Date/DateTime (valore Python ISO); qui si definisce solo la resa a schermo.
# Riferimento: https://docs.djangoproject.com/en/stable/ref/templates/builtins/#date

DATE_FORMAT = 'd/m/Y'  # gg/mm/aaaa (anche dove il default it sarebbe lungo)
SHORT_DATE_FORMAT = 'd/m/Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'd/m/Y H:i'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
# Anno sempre a 4 cifre nelle etichette che usano questi formati:
YEAR_MONTH_FORMAT = 'm/Y'  # es. 04/2026 — mese/anno numerico
MONTH_DAY_FORMAT = 'd/m'
FIRST_DAY_OF_WEEK = 1  # lunedì

DECIMAL_SEPARATOR = ','
THOUSAND_SEPARATOR = '.'
NUMBER_GROUPING = 3

DATE_INPUT_FORMATS = [
    '%d/%m/%Y',
    '%d/%m/%y',
    '%d-%m-%Y',
    '%d-%m-%y',
    '%Y-%m-%d',
]
DATETIME_INPUT_FORMATS = [
    '%d/%m/%Y %H:%M:%S',
    '%d/%m/%Y %H:%M',
    '%d/%m/%y %H:%M:%S',
    '%d/%m/%y %H:%M',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
]
