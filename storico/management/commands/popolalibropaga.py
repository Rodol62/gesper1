import re
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand
from django.db import transaction
from storico.models import LibroPagaStorico
from anagrafiche.models import Dipendente
from rapporto_di_lavoro.models import SimulazionePagaSalvata
from documenti.models import Documento
from accounts.models import MovimentoImportPaghe


# ── Nomi mesi italiani (come in documenti/views.py) ──────────────────────────
MESI_ITA = {
    'GENNAIO': 1, 'FEBBRAIO': 2, 'MARZO': 3, 'APRILE': 4,
    'MAGGIO': 5, 'GIUGNO': 6, 'LUGLIO': 7, 'AGOSTO': 8,
    'SETTEMBRE': 9, 'OTTOBRE': 10, 'NOVEMBRE': 11, 'DICEMBRE': 12,
}


def _parse_periodo_busta(doc):
    """
    Estrae (mese, anno) da descrizione busta; fallback a data_caricamento.
    Replica la logica di documenti.views._parse_periodo_busta.
    """
    desc = (getattr(doc, 'descrizione', '') or '').upper()

    # Formato MM/YYYY o MM/YYYY
    m = re.search(r'\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b', desc)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Anno + nome mese italiano in descrizione
    year_m = re.search(r'\b(20\d{2})\b', desc)
    year = int(year_m.group(1)) if year_m else None
    month = None
    for nome, num in MESI_ITA.items():
        if nome in desc:
            month = num
            break

    if month and year:
        return month, year

    # Fallback: data di caricamento
    if doc.data_caricamento:
        return doc.data_caricamento.month, doc.data_caricamento.year
    return None, None


def _estrai_importi_pdf(doc):
    """
    Estrae (netto, lordo) da PDF busta paga via documenti.views.
    Restituisce (None, None) se il PDF non è disponibile o l'estrazione fallisce.
    """
    try:
        from documenti.views import _extract_busta_importi_da_pdf
        return _extract_busta_importi_da_pdf(doc)
    except Exception:
        return None, None


def _estrai_dettaglio_busta_pdf(doc):
    """
    Dict campi libro da busta: merge tra euristica legacy PDF e pipeline lettura cedolino
    (:mod:`documenti.libro_paga_da_busta`), così colonne e importi coincidono con la lettura canonica.
    """
    try:
        from documenti.libro_paga_da_busta import merge_dettaglio_libro_paga_per_documento

        return merge_dettaglio_libro_paga_per_documento(doc)
    except Exception:
        try:
            from documenti.views import estrai_busta_dettaglio_libro_paga_da_pdf

            return estrai_busta_dettaglio_libro_paga_da_pdf(doc)
        except Exception:
            return {}


def _movimento_busta_per_documento(doc, dip, mese, anno):
    """MovimentoImportPaghe collegato al documento o stesso dipendente/periodo."""
    q = MovimentoImportPaghe.objects.filter(tipo='BUSTA', dipendente=dip, mese=mese, anno=anno)
    mov = q.filter(documento_id=doc.pk).first()
    if mov:
        return mov
    return q.first()


def _is_blank_importo(val):
    if val is None:
        return True
    try:
        return Decimal(str(val)).is_zero()
    except Exception:
        return True


def _merge_movimento_in_dettaglio(det, mov):
    """Integra importi da import massivo PDF se mancano o sono zero nel dict estratto."""
    if not mov:
        return det
    out = dict(det) if det else {}
    if mov.importo_netto is not None and _is_blank_importo(out.get('importo')):
        out['importo'] = mov.importo_netto
    if mov.importo_lordo is not None and _is_blank_importo(out.get('lordo_mensile')):
        out['lordo_mensile'] = mov.importo_lordo
    return out


_LIBRO_CAMPI_DA_ESTRAZIONE = (
    'importo', 'lordo_mensile', 'inps_dipendente', 'irpef', 'addizionali', 'altre_trattenute',
    'trattamento_integrativo', 'retribuzione_base', 'indennita_accessorie',
    'inps_azienda', 'inail_azienda', 'costo_azienda', 'tfr_mensile', 'rateo_13', 'rateo_14',
    'ore_ordinarie', 'ore_straordinario', 'ore_assenza',
)


def _snapshot_rapporto_per_libro(dip):
    """Livello/qualifica/contratto da rapporto sottoscritto o anagrafica dipendente."""
    from rapporto_di_lavoro.models import RapportoDiLavoro

    r = (
        RapportoDiLavoro.objects.filter(dipendente=dip, stato='sottoscritto')
        .select_related('tipo_contratto')
        .order_by('-data_inizio_rapporto')
        .first()
    )
    livello = ''
    qualifica = ''
    tipo_ctr = ''
    if r:
        livello = (r.livello_ccnl or '')[:50]
        qualifica = (r.qualifica or '')[:200]
        if r.tipo_contratto_id:
            tipo_ctr = (r.tipo_contratto.nome or '')[:100]
    if not livello and getattr(dip, 'livello', None):
        livello = str(dip.livello)[:50]
    if not qualifica and getattr(dip, 'ruolo', None):
        qualifica = str(dip.ruolo)[:200]
    if not any((livello, qualifica, tipo_ctr)):
        return {}
    return {
        'livello_ccnl': livello,
        'qualifica': qualifica,
        'tipo_contratto': tipo_ctr,
    }


def _merge_defaults_rapporto(defaults, dip):
    snap = _snapshot_rapporto_per_libro(dip)
    for k, v in snap.items():
        if v and not (defaults.get(k) or '').strip():
            defaults[k] = v
    return defaults


def _applica_snapshot_testuali_su_voce(voce, dip):
    """Compila livello/qualifica/tipo contratto se ancora vuoti."""
    snap = _snapshot_rapporto_per_libro(dip)
    changed = False
    for attr, val in snap.items():
        if not val:
            continue
        cur = (getattr(voce, attr, None) or '').strip()
        if not cur:
            setattr(voce, attr, val)
            changed = True
    return changed


def _applica_dettaglio_su_voce(voce, det, *, force=False):
    """
    Aggiorna campi numerici da ``det``.
    Con ``force=True`` (es. «Ricarica da buste» solo documenti) sovrascrive sempre i campi
    presenti in ``det``, così si correggono importi errati da vecchie estrazioni.
    """
    changed = False
    for attr in _LIBRO_CAMPI_DA_ESTRAZIONE:
        val = det.get(attr)
        if val is None:
            continue
        try:
            val_dec = val if isinstance(val, Decimal) else Decimal(str(val))
        except Exception:
            continue
        if force:
            cur = getattr(voce, attr, None)
            if cur != val_dec:
                setattr(voce, attr, val_dec)
                changed = True
            continue
        if val_dec.is_zero():
            continue
        cur = getattr(voce, attr, None)
        if cur is None or (isinstance(cur, Decimal) and cur.is_zero()):
            setattr(voce, attr, val_dec)
            changed = True
    return changed


def _d(val):
    """Converte un valore in Decimal sicuro o restituisce None."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return None


def _ordina_dipendente(dip_id):
    """Riassegna ordinamento cronologico alle voci del dipendente."""
    voci = LibroPagaStorico.objects.filter(dipendente_id=dip_id).order_by(
        'data_pagamento', 'periodo_riferimento'
    )
    for i, voce in enumerate(voci, start=1):
        if voce.ordinamento != i:
            voce.ordinamento = i
            voce.save(update_fields=['ordinamento'])


class Command(BaseCommand):
    help = 'Popola LibroPagaStorico da SimulazionePagaSalvata e Documenti (busta_paga)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Cancella tutte le voci esistenti prima di importare (solo per test)',
        )
        parser.add_argument(
            '--solo-simulazioni',
            action='store_true',
            help='Importa solo da SimulazionePagaSalvata',
        )
        parser.add_argument(
            '--solo-documenti',
            action='store_true',
            help='Importa solo da Documenti (busta_paga); niente simulazioni né voci solo da MovimentoImportPaghe.',
        )
        parser.add_argument(
            '--includi-movimenti-orfani',
            action='store_true',
            help='In modalità completa include anche MovimentoImportPaghe tipo BUSTA non riconciliati su Documenti.',
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            if options['reset']:
                deleted, _ = LibroPagaStorico.objects.all().delete()
                self.stdout.write(self.style.WARNING(f'Reset: {deleted} voci cancellate.'))

            dip_ids_aggiornati = set()

            # ── 1. Da SimulazionePagaSalvata ──────────────────────────────────
            if not options['solo_documenti']:
                count_sim = 0
                skip_sim = 0
                for sim in SimulazionePagaSalvata.objects.select_related('utente').all():
                    if not sim.utente or not sim.anno or not sim.mese:
                        skip_sim += 1
                        continue
                    dip = Dipendente.objects.filter(utente=sim.utente).first()
                    if not dip:
                        skip_sim += 1
                        continue

                    res = sim.form_data if isinstance(sim.form_data, dict) else {}
                    netto = _d(sim.netto_totale) or _d(res.get('netto_totale')) or Decimal('0')
                    periodo = f"{sim.mese:02d}/{sim.anno}"
                    data_pag = sim.data_modifica.date() if sim.data_modifica else sim.data_creazione.date()

                    _, created = LibroPagaStorico.objects.update_or_create(
                        dipendente=dip,
                        periodo_riferimento=periodo,
                        defaults=dict(
                            azienda=dip.azienda,
                            data_inizio_rapporto=dip.data_assunzione or sim.data_creazione.date(),
                            data_fine_rapporto=dip.data_cessazione,
                            livello_ccnl=sim.ccnl_livello or '',
                            qualifica=sim.ccnl_qualifica or '',
                            tipo_contratto=sim.tipo_contratto_nome or '',
                            data_pagamento=data_pag,
                            lordo_mensile=_d(sim.lordo_mensile),
                            importo=netto,
                            costo_azienda=_d(sim.costo_mensile),
                            fonte_dati='simulazione',
                            note=f"Simulazione: {sim.nome}",
                        ),
                    )
                    dip_ids_aggiornati.add(dip.id)
                    if created:
                        count_sim += 1

                self.stdout.write(self.style.SUCCESS(
                    f'SimulazionePagaSalvata: {count_sim} voci create/aggiornate, {skip_sim} ignorate.'
                ))

            # ── 2. Da Documenti (busta_paga) ──────────────────────────────────
            if not options['solo_simulazioni']:
                count_doc = 0
                skip_doc = 0

                qs_doc = Documento.objects.filter(
                    tipo='busta_paga'
                ).select_related('dipendente', 'azienda').order_by(
                    'dipendente', 'data_caricamento'
                )

                for doc in qs_doc:
                    if not doc.dipendente:
                        skip_doc += 1
                        continue

                    dip = doc.dipendente

                    # Periodo reale dalla descrizione (non dalla data upload)
                    mese, anno = _parse_periodo_busta(doc)
                    if not mese or not anno:
                        skip_doc += 1
                        continue

                    periodo = f"{mese:02d}/{anno}"

                    data_pag = doc.data_caricamento.date()

                    det = _estrai_dettaglio_busta_pdf(doc)
                    mov = _movimento_busta_per_documento(doc, dip, mese, anno)
                    det = _merge_movimento_in_dettaglio(det, mov)

                    defaults = dict(
                        azienda=dip.azienda,
                        data_inizio_rapporto=dip.data_assunzione or data_pag,
                        data_fine_rapporto=dip.data_cessazione,
                        data_pagamento=data_pag,
                        fonte_dati='documento',
                        note=f"Busta paga: {doc.descrizione or ''}".strip(),
                    )
                    _merge_defaults_rapporto(defaults, dip)
                    for attr in _LIBRO_CAMPI_DA_ESTRAZIONE:
                        v = det.get(attr)
                        if v is not None and not (isinstance(v, Decimal) and v.is_zero()):
                            defaults[attr] = v

                    if 'importo' not in defaults:
                        netto_fb, lordo_fb = _estrai_importi_pdf(doc)
                        if netto_fb is not None and not (
                            isinstance(netto_fb, Decimal) and netto_fb.is_zero()
                        ):
                            defaults['importo'] = netto_fb
                        if lordo_fb is not None and not (
                            isinstance(lordo_fb, Decimal) and lordo_fb.is_zero()
                        ):
                            defaults['lordo_mensile'] = lordo_fb

                    if 'importo' not in defaults:
                        defaults['importo'] = Decimal('0')

                    esistente = LibroPagaStorico.objects.filter(
                        dipendente=dip, periodo_riferimento=periodo
                    ).first()

                    if esistente:
                        forza_doc = options["solo_documenti"] and (esistente.fonte_dati or "") == "documento"
                        changed = _applica_dettaglio_su_voce(esistente, det, force=forza_doc)
                        if _applica_snapshot_testuali_su_voce(esistente, dip):
                            changed = True
                        if not esistente.data_inizio_rapporto and dip.data_assunzione:
                            esistente.data_inizio_rapporto = dip.data_assunzione
                            changed = True
                        if changed:
                            esistente.save()
                        skip_doc += 1
                    else:
                        LibroPagaStorico.objects.create(
                            dipendente=dip,
                            periodo_riferimento=periodo,
                            **defaults,
                        )
                        dip_ids_aggiornati.add(dip.id)
                        count_doc += 1

                self.stdout.write(self.style.SUCCESS(
                    f'Documenti (busta_paga): {count_doc} nuove voci, {skip_doc} già presenti/aggiornate.'
                ))

                # ── 2b. Movimenti da import PDF unico (MovimentoImportPaghe) senza Documento ──
                # Solo in modalità "completa" (non --solo-documenti): altrimenti «Ricarica da buste»
                # nel portale deve riflettere unicamente la tabella Documenti (busta_paga).
                if not options['solo_documenti'] and options['includi_movimenti_orfani']:
                    mov_fill = 0
                    for mov in MovimentoImportPaghe.objects.filter(tipo='BUSTA').exclude(
                        dipendente__isnull=True
                    ).select_related('dipendente', 'azienda'):
                        dip = mov.dipendente
                        periodo = f"{mov.mese:02d}/{mov.anno}"
                        es = LibroPagaStorico.objects.filter(
                            dipendente=dip, periodo_riferimento=periodo
                        ).first()
                        det_m = {}
                        if mov.importo_netto is not None:
                            det_m['importo'] = mov.importo_netto
                        if mov.importo_lordo is not None:
                            det_m['lordo_mensile'] = mov.importo_lordo
                        if es:
                            if _applica_dettaglio_su_voce(es, det_m):
                                es.save()
                                mov_fill += 1
                            continue
                        if mov.importo_netto is None and mov.importo_lordo is None:
                            continue
                        data_pag = mov.created_at.date() if getattr(mov, 'created_at', None) else (
                            dip.data_assunzione or date(mov.anno, mov.mese, 1)
                        )
                        LibroPagaStorico.objects.create(
                            dipendente=dip,
                            azienda=dip.azienda,
                            periodo_riferimento=periodo,
                            data_inizio_rapporto=dip.data_assunzione or data_pag,
                            data_fine_rapporto=dip.data_cessazione,
                            data_pagamento=data_pag,
                            importo=mov.importo_netto if mov.importo_netto is not None else Decimal('0'),
                            lordo_mensile=mov.importo_lordo,
                            fonte_dati='importazione',
                            note=(
                                f"Import paghe PDF: {mov.source_pdf or mov.periodo_label or ''}"
                            )[:500],
                        )
                        dip_ids_aggiornati.add(dip.id)
                        mov_fill += 1
                    if mov_fill:
                        self.stdout.write(self.style.SUCCESS(
                            f'Movimenti import paghe (BUSTA): {mov_fill} voci create/aggiornate da cedolini decomposti.'
                        ))

            # ── 3. Riordino cronologico per ogni dipendente toccato ───────────
            for dip_id in dip_ids_aggiornati:
                _ordina_dipendente(dip_id)

            totale = LibroPagaStorico.objects.count()
            self.stdout.write(self.style.SUCCESS(
                f'Libro paga: {totale} voci totali per {len(dip_ids_aggiornati)} dipendenti aggiornati.'
            ))
