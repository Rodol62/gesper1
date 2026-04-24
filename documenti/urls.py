from django.urls import path
from . import views

urlpatterns = [
    path(
        'admin/archivio-documenti-storage/pdf/',
        views.admin_archivio_documenti_pdf,
        name='admin_archivio_documenti_pdf',
    ),
    path(
        'admin/archivio-documenti-storage/elimina-file/',
        views.admin_archivio_documenti_elimina_file,
        name='admin_archivio_documenti_elimina_file',
    ),
    path(
        'admin/archivio-documenti-storage/',
        views.admin_archivio_documenti_storage,
        name='admin_archivio_documenti_storage',
    ),
    path(
        'admin/archivio-documenti-storage/log/',
        views.admin_archivio_documenti_log,
        name='admin_archivio_documenti_log',
    ),
    path('', views.lista_documenti, name='index_documenti'),
    path('', views.lista_documenti, name='lista_documenti'),
    path('buste-paga/', views.lista_buste_paga, name='lista_buste_paga_documenti'),
    path('f24/', views.lista_f24, name='lista_f24_documenti'),
    path('cud/', views.lista_cud, name='lista_cud_documenti'),
    path('upload/', views.upload_documento, name='upload_documento'),
    path('upload-buste-massivo/', views.upload_buste_paga_massivo, name='upload_buste_paga_massivo'),
    path('upload-cud-massivo/', views.upload_cud_massivo, name='upload_cud_massivo'),
    path('upload-personale/', views.upload_documento_personale, name='upload_documento_personale'),
    path('dipendente/<int:dipendente_id>/', views.documenti_dipendente_admin, name='documenti_dipendente_admin'),
    path('visualizza/<int:documento_id>/', views.visualizza_documento, name='visualizza_documento'),
    path(
        'cedolino/<int:documento_id>/',
        views.visualizza_cedolino_busta,
        name='visualizza_cedolino_busta',
    ),
    path(
        'prova-lettura-busta/',
        views.prova_lettura_busta_paga,
        name='prova_lettura_busta_paga',
    ),
    path(
        'buste-paga/lettura-cedolino/',
        views.buste_paga_lettura_cedolino,
        {'buste_scheda': 'completo'},
        name='buste_paga_lettura_cedolino',
    ),
    path(
        'buste-paga/estrazione-motore-v4/',
        views.buste_paga_lettura_cedolino,
        {'buste_scheda': 'estrazione_v4'},
        name='buste_paga_estrazione_motore_v4',
    ),
    path(
        'buste-paga/conciliazione-cedolino/',
        views.buste_paga_lettura_cedolino,
        {'buste_scheda': 'conciliazione'},
        name='buste_paga_conciliazione_cedolino',
    ),
    path(
        'prova-lettura-buste-anno-zip/',
        views.prova_scarica_buste_anno_cedolino_zip,
        name='prova_scarica_buste_anno_cedolino_zip',
    ),
    path('download/<int:documento_id>/', views.download_documento, name='download_documento'),
    path('elimina/<int:documento_id>/', views.elimina_documento, name='elimina_documento'),
    path('<str:legacy_filename>', views.legacy_documento_redirect, name='legacy_documento_redirect'),
]
