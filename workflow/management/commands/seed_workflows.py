from django.core.management.base import BaseCommand
from workflow.models import RichiestaWorkflow, StepApprovazione


class Command(BaseCommand):
    help = 'Crea i workflow standard richieste'

    def handle(self, *args, **options):
        # FERIE: Manager -> HR
        wf_ferie, _ = RichiestaWorkflow.objects.get_or_create(
            nome='Ferie Standard',
            tipo_richiesta='ferie',
            defaults={'numero_step': 2}
        )
        StepApprovazione.objects.get_or_create(
            workflow=wf_ferie,
            numero_step=1,
            defaults={
                'titolo': 'Approvazione Manager',
                'ruolo_approvatore': 'manager',
                'order': 1,
            }
        )
        StepApprovazione.objects.get_or_create(
            workflow=wf_ferie,
            numero_step=2,
            defaults={
                'titolo': 'Controfirma HR',
                'ruolo_approvatore': 'hr',
                'order': 2,
            }
        )

        # PERMESSO: HR
        wf_perm, _ = RichiestaWorkflow.objects.get_or_create(
            nome='Permesso Standard',
            tipo_richiesta='permesso',
            defaults={'numero_step': 1}
        )
        StepApprovazione.objects.get_or_create(
            workflow=wf_perm,
            numero_step=1,
            defaults={
                'titolo': 'Approvazione HR',
                'ruolo_approvatore': 'hr',
                'order': 1,
            }
        )

        # MALATTIA: HR
        wf_mal, _ = RichiestaWorkflow.objects.get_or_create(
            nome='Malattia Standard',
            tipo_richiesta='malattia',
            defaults={'numero_step': 1}
        )
        StepApprovazione.objects.get_or_create(
            workflow=wf_mal,
            numero_step=1,
            defaults={
                'titolo': 'Verifica HR',
                'ruolo_approvatore': 'hr',
                'order': 1,
            }
        )

        self.stdout.write(self.style.SUCCESS('✓ Workflow standard creati'))
