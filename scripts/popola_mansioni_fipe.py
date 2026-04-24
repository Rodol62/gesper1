from rapporto_di_lavoro.models import ParametroCCNLTurismo
from django.utils import timezone

def elimina_generici_e_inserisci_mansioni():
    # Elimina record generici per livelli 5, 6, 7 nella sezione ristoranti_pizzerie
    ParametroCCNLTurismo.objects.filter(
        livello__in=['5', '6', '7'],
        sezione='ristoranti_pizzerie',
        qualifica__startswith='Livello'
    ).delete()
    oggi = timezone.now().date()
    dati = [
        dict(livello='5', qualifica='Cuoco', sezione='ristoranti_pizzerie'),
        dict(livello='5', qualifica='Pizzaiolo', sezione='ristoranti_pizzerie'),
        dict(livello='6', qualifica='Cameriere/a', sezione='ristoranti_pizzerie'),
        dict(livello='7', qualifica='Fattorino', sezione='ristoranti_pizzerie'),
    ]
    for d in dati:
        ParametroCCNLTurismo.objects.update_or_create(
            livello=d['livello'], qualifica=d['qualifica'], sezione=d['sezione'],
            defaults={
                'ccnl': 'FIPE Ristorazione',
                'versione': '2024-2026',
                'tipo_contratto_nazionale': 'Tempo indeterminato',
                'decorrenza_validita_da': oggi,
                'decorrenza_validita_a': None,
                'minimo_tabellare': 1000,
                'totale_tabellare': 1200,
                'fonte_tabella': 'FIPE Ristorazione esercizi minori',
                'importo_lordo_mensile': 1300,
                'paga_base_mensile': 1000,
                'contingenza_mensile': 100,
                'edr_mensile': 50,
                'elemento_distinto_sanita': 0,
                'elemento_distinto_bilateralita': 0,
                'indennita_mensile': 50,
                'ore_settimanali': 40,
                'ore_mensili': 173.33,
                'ore_giornaliere': 8,
                'scatto_periodicita_mesi': 24,
                'scatto_importo': 20,
                'numero_scatti_massimi': 10,
                'straordinario_diurno_maggiorazione': 15,
                'straordinario_notturno_maggiorazione': 30,
                'straordinario_festivo_maggiorazione': 30,
                'riposi_compensativi_regola': '',
                'note': '',
                'attivo': True,
            }
        )
    print('Mansioni FIPE inserite/aggiornate')

if __name__ == '__main__':
    elimina_generici_e_inserisci_mansioni()
