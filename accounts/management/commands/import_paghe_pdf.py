from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile
from django.db import transaction
from pypdf import PdfReader, PdfWriter

from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento
from accounts.models import MovimentoImportPaghe, MovimentoImportPagheF24Dettaglio
from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read


def _split_name(full_name: str) -> tuple[str, str]:
    raw = (full_name or "").strip()
    if not raw:
        return "Sconosciuto", "Sconosciuto"
    parts = raw.split()
    if len(parts) == 1:
        return parts[0].title(), "Sconosciuto"
    # Nei cedolini il formato è tipicamente COGNOME NOME
    cognome = parts[0].title()
    nome = " ".join(parts[1:]).title()
    return cognome, nome


def _parse_birth(value: str | None):
    if not value:
        return None
    txt = value.strip().replace(" ", "")
    try:
        d_s, m_s, y_s = txt.split("/")
        d = int(d_s)
        m = int(m_s)
        y = int(y_s)
        if y < 100:
            # pivot semplice per dati paghe storici
            y = 2000 + y if y <= 30 else 1900 + y
        return date(y, m, d)
    except Exception:
        return None


def _parse_date_conv(value: str | None) -> date | None:
    """Parsa una data DD/MM/YYYY estratta da cedolino (assunzione / cessazione)."""
    if not value:
        return None
    txt = (value or "").strip().replace(" ", "")
    try:
        parts = txt.split("/")
        if len(parts) != 3:
            return None
        d_v, m_v, y_v = int(parts[0]), int(parts[1]), int(parts[2])
        if y_v < 100:
            y_v = 2000 + y_v if y_v <= 30 else 1900 + y_v
        if 1 <= d_v <= 31 and 1 <= m_v <= 12 and 1940 <= y_v <= 2100:
            return date(y_v, m_v, d_v)
    except Exception:
        pass
    return None


def _aggiorna_date_dipendente(dip: "Dipendente", row: dict) -> list[str]:
    """
    Aggiorna data_assunzione e data_cessazione sul Dipendente a partire
    dai dati estratti dalla singola pagina della busta paga.
    Restituisce la lista dei campi modificati (per update_fields).
    """
    changed: list[str] = []

    data_ass = _parse_date_conv(row.get("data_assunzione_conv"))
    if data_ass is not None and dip.data_assunzione != data_ass:
        dip.data_assunzione = data_ass
        changed.append("data_assunzione")

    data_cess = _parse_date_conv(row.get("data_cessazione"))
    if data_cess is not None and dip.data_cessazione != data_cess:
        dip.data_cessazione = data_cess
        changed.append("data_cessazione")
        # Se la cessazione è valorizzata → aggiorna stato a 'cessato'
        if dip.stato != "cessato":
            dip.stato = "cessato"
            changed.append("stato")

    return changed


def _parse_periodo(periodo: str | None) -> tuple[int | None, int | None]:
    if not periodo:
        return None, None
    try:
        mese_s, anno_s = (periodo or '').split('/')
        mese = int(mese_s)
        anno = int(anno_s)
        if 1 <= mese <= 12 and anno > 1900:
            return mese, anno
    except Exception:
        return None, None
    return None, None


def _parse_decimal(value) -> Decimal | None:
    if value in (None, ''):
        return None
    txt = str(value).strip().replace(' ', '').replace('.', '').replace(',', '.')
    try:
        return Decimal(txt).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = (
        "Import da report preview paghe PDF: crea dipendenti mancanti (no documenti per ora). "
        "Default dry-run; usa --apply per scrivere su DB."
    )

    def add_arguments(self, parser):
        parser.add_argument("preview_json", type=str, help="Path JSON generato da preview_import_paghe_pdf")
        parser.add_argument("--azienda-id", type=int, required=True, help="ID azienda target")
        parser.add_argument("--apply", action="store_true", help="Applica le modifiche su DB")
        parser.add_argument(
            "--attach-docs",
            action="store_true",
            help="Allega anche i documenti (split pagina busta + PDF F24 aziendale) durante --apply",
        )
        parser.add_argument(
            "--allow-overwrite",
            action="store_true",
            help="Consente sovrascrittura completa dei movimenti BUSTA esistenti (default: disabilitato)",
        )

    def handle(self, *args, **options):
        preview_path = Path(options["preview_json"]).expanduser().resolve()
        if not preview_path.exists():
            raise CommandError(f"File preview non trovato: {preview_path}")

        try:
            azienda = Azienda.objects.get(id=options["azienda_id"])
        except Azienda.DoesNotExist as exc:
            raise CommandError(f"Azienda non trovata con id={options['azienda_id']}") from exc

        data = json.loads(preview_path.read_text(encoding="utf-8"))
        rows = data.get("rows", [])
        f24_pages = data.get("f24_pages", [])
        natura_busta_file = (data.get("natura_busta_file") or "ORDINARIA").upper()
        source_pdf = Path(data.get("pdf", "")).expanduser()
        if not rows:
            raise CommandError("Nessuna riga nel file preview.")

        attach_docs = bool(options.get("attach_docs"))
        allow_overwrite = bool(options.get("allow_overwrite"))
        reader = None
        if attach_docs:
            if not source_pdf.exists():
                raise CommandError(f"PDF sorgente non trovato nel preview: {source_pdf}")
            reader = PdfReader(str(source_pdf))
            if getattr(reader, 'is_encrypted', False):
                unlocked = False
                for pwd in passwords_for_busta_pdf_read():
                    try:
                        res = reader.decrypt(pwd)
                        if res:
                            unlocked = True
                            break
                    except Exception:
                        continue
                if not unlocked:
                    raise CommandError(
                        f"Impossibile decriptare PDF sorgente: {source_pdf.name}. "
                        "Verificare password del file."
                    )

        to_create = [r for r in rows if r.get("action") == "create"]
        self.stdout.write(f"Righe totali: {len(rows)} | da creare: {len(to_create)}")

        created = 0
        skipped = 0
        errors = 0
        docs_created = 0
        movimenti_upsert = 0

        def _make_single_page_pdf_bytes(page_num_1_based: int) -> bytes:
            if reader is None:
                raise RuntimeError("PdfReader non disponibile")
            w = PdfWriter()
            w.add_page(reader.pages[page_num_1_based - 1])
            import io
            bio = io.BytesIO()
            w.write(bio)
            return bio.getvalue()

        def _make_multi_page_pdf_bytes(page_numbers_1_based: list[int]) -> bytes:
            if reader is None:
                raise RuntimeError("PdfReader non disponibile")
            w = PdfWriter()
            for p in page_numbers_1_based:
                w.add_page(reader.pages[p - 1])
            import io
            bio = io.BytesIO()
            w.write(bio)
            return bio.getvalue()

        def _save_busta_doc(dipendente: Dipendente, row: dict, natura_busta: str = 'ORDINARIA'):
            nonlocal docs_created
            mese_anno = row.get("periodo", "00/0000")
            natura_label = {
                'TREDICESIMA': ' - Tredicesima',
                'QUATTORDICESIMA': ' - Quattordicesima',
            }.get((natura_busta or 'ORDINARIA').upper(), '')
            descr = f"Busta paga {mese_anno}{natura_label} (import PDF)"

            # Evita duplicati per dipendente + descrizione (con --allow-overwrite sostituisce il PDF)
            existing = Documento.objects.filter(
                azienda=azienda,
                dipendente=dipendente,
                tipo="busta_paga",
                descrizione=descr,
            ).first()
            if existing:
                has_existing_file = bool(
                    existing.file
                    and getattr(existing.file, 'name', None)
                    and existing.file.storage.exists(existing.file.name)
                )
                if has_existing_file and not allow_overwrite:
                    return existing

                page_num = int(row.get("page"))
                file_bytes = _make_single_page_pdf_bytes(page_num)
                filename = f"busta_{mese_anno.replace('/', '_')}_dip_{dipendente.id}_p{page_num}.pdf"
                existing.file.save(filename, ContentFile(file_bytes), save=False)
                existing.save(update_fields=['file'])
                docs_created += 1
                return existing

            page_num = int(row.get("page"))
            file_bytes = _make_single_page_pdf_bytes(page_num)
            filename = f"busta_{mese_anno.replace('/', '_')}_dip_{dipendente.id}_p{page_num}.pdf"

            doc = Documento(
                azienda=azienda,
                dipendente=dipendente,
                tipo="busta_paga",
                descrizione=descr,
                caricato_da=None,
                caricato_dal_dipendente=False,
                visibile_al_dipendente=True,
            )
            doc.file.save(filename, ContentFile(file_bytes), save=False)
            doc.save()
            docs_created += 1
            return doc

        @transaction.atomic
        def _apply():
            nonlocal created, skipped, errors, docs_created, movimenti_upsert
            from documenti.views import _extract_busta_importi_da_pdf, _extract_f24_dettagli_da_pdf
            matched_map: dict[int, Dipendente] = {}
            dip_qs = Dipendente.objects.filter(azienda=azienda)

            def _resolve_dipendente_for_row(row: dict):
                dip_id = row.get("dipendente_id")
                if dip_id:
                    d = dip_qs.filter(id=dip_id).first()
                    if d:
                        return d

                cf = (row.get("cf") or "").strip().upper()
                if cf:
                    d = dip_qs.filter(codice_fiscale=cf).first()
                    if d:
                        return d

                full_name = (row.get("full_name") or "").strip()
                if full_name:
                    cognome, nome = _split_name(full_name)
                    d = dip_qs.filter(cognome__iexact=cognome, nome__iexact=nome).first()
                    if d:
                        return d

                return None

            # 1) crea i mancanti
            for r in to_create:
                cf = (r.get("cf") or "").strip().upper() or None
                full_name = (r.get("full_name") or "").strip()
                cognome, nome = _split_name(full_name)
                data_nascita = _parse_birth(r.get("birth_date"))

                # Ri-controllo anti duplicati al momento dell'apply
                if cf:
                    dup_cf = dip_qs.filter(codice_fiscale=cf).first()
                    if dup_cf:
                        skipped += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"SKIP page={r.get('page')} già esistente CF={cf} id={dup_cf.id}"
                            )
                        )
                        continue

                dup_name = dip_qs.filter(cognome__iexact=cognome, nome__iexact=nome).first()
                if dup_name:
                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"SKIP page={r.get('page')} nome già esistente: {cognome} {nome} (id={dup_name.id})"
                        )
                    )
                    continue

                try:
                    Dipendente.objects.create(
                        azienda=azienda,
                        nome=nome,
                        cognome=cognome,
                        codice_fiscale=cf,
                        data_nascita=data_nascita,
                        ruolo="Da completare",
                        stato="attivo",
                    )
                    dip = Dipendente.objects.get(azienda=azienda, codice_fiscale=cf) if cf else Dipendente.objects.filter(azienda=azienda, cognome__iexact=cognome, nome__iexact=nome).first()
                    if dip:
                        matched_map[int(r.get("page"))] = dip
                    created += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"CREATE page={r.get('page')} {cognome} {nome} | CF={cf or '-'} | nasc={data_nascita or '-'}"
                        )
                    )
                except Exception as exc:
                    errors += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"ERROR page={r.get('page')} {cognome} {nome}: {exc}"
                        )
                    )

            # 2) aggiunge i match già presenti nel report preview
            for r in rows:
                action = r.get("action")
                if action not in {"match_cf", "match_name", "already_present"}:
                    continue
                d = _resolve_dipendente_for_row(r)
                if d:
                    matched_map[int(r.get("page"))] = d

            # 3) registra movimenti buste (e allega documenti se richiesto)
            for r in rows:
                if r.get("action") == "ambiguous":
                    continue
                if r.get("action") == "already_present" and not allow_overwrite:
                    # Busta già presente: con --attach-docs ripristina file se manca (senza sovrascrittura).
                    if attach_docs:
                        dip = _resolve_dipendente_for_row(r)
                        mese, anno = _parse_periodo(r.get("periodo"))
                        natura_busta = (r.get('natura_busta') or natura_busta_file or 'ORDINARIA').upper()
                        mov = None
                        if mese and anno:
                            mov_qs = MovimentoImportPaghe.objects.filter(
                                azienda=azienda,
                                tipo='BUSTA',
                                anno=anno,
                                mese=mese,
                                natura_busta=natura_busta,
                            )
                            if dip:
                                mov = mov_qs.filter(dipendente=dip).first()
                            if mov is None and (r.get("cf") or "").strip():
                                mov = mov_qs.filter(cf_estratto=(r.get("cf") or "").strip().upper()).first()
                            if mov is None:
                                mov = mov_qs.first()
                            if mov and not dip and mov.dipendente_id:
                                dip = mov.dipendente
                            if mov:
                                try:
                                    doc_existing = _save_busta_doc(dip, r, natura_busta) if dip else None
                                    changed_fields = []
                                    if doc_existing and mov.documento_id != doc_existing.id:
                                        mov.documento = doc_existing
                                        changed_fields.append('documento')

                                    netto_row = _parse_decimal(r.get('netto_busta'))
                                    lordo_row = _parse_decimal(r.get('lordo_busta'))
                                    if netto_row is not None and mov.importo_netto is None:
                                        mov.importo_netto = netto_row
                                        if mov.importo is None:
                                            mov.importo = netto_row
                                            changed_fields.append('importo')
                                        changed_fields.append('importo_netto')
                                    if lordo_row is not None and mov.importo_lordo is None:
                                        mov.importo_lordo = lordo_row
                                        changed_fields.append('importo_lordo')

                                    if changed_fields:
                                        mov.save(update_fields=changed_fields)
                                except Exception as exc:
                                    errors += 1
                                    self.stdout.write(self.style.ERROR(f"ERROR ripristino already_present page={r.get('page')}: {exc}"))

                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"SKIP page={r.get('page')} busta già presente per periodo {r.get('periodo')}"
                        )
                    )
                    continue
                page_num = int(r.get("page"))
                dip = matched_map.get(page_num)
                if not dip:
                    dip = _resolve_dipendente_for_row(r)
                if not dip:
                    continue

                documento_busta = None
                natura_busta = (r.get('natura_busta') or natura_busta_file or 'ORDINARIA').upper()
                if attach_docs:
                    try:
                        documento_busta = _save_busta_doc(dip, r, natura_busta)
                    except Exception as exc:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f"ERROR doc busta page={page_num}: {exc}"))

                mese, anno = _parse_periodo(r.get("periodo"))
                if mese and anno:
                    netto = _parse_decimal(r.get('netto_busta'))
                    lordo = _parse_decimal(r.get('lordo_busta'))

                    # Priorità al dato estratto dalla singola pagina PDF allegata
                    # (evita lordi/netti semplificati/errati provenienti dal preview).
                    if attach_docs and documento_busta is not None:
                        try:
                            netto_pdf, lordo_pdf = _extract_busta_importi_da_pdf(documento_busta)
                            if netto_pdf is not None:
                                netto = netto_pdf
                            if lordo_pdf is not None:
                                lordo = lordo_pdf
                        except Exception:
                            pass

                    existing_mov = MovimentoImportPaghe.objects.filter(
                        azienda=azienda,
                        dipendente=dip,
                        tipo='BUSTA',
                        anno=anno,
                        mese=mese,
                        natura_busta=natura_busta,
                    ).first()

                    if existing_mov and not allow_overwrite:
                        changed_fields = []
                        doc_senza_file = False
                        if existing_mov.documento_id:
                            ed = existing_mov.documento
                            if (
                                not ed
                                or not ed.file
                                or not getattr(ed.file, "name", None)
                            ):
                                doc_senza_file = True
                            else:
                                try:
                                    doc_senza_file = not ed.file.storage.exists(
                                        ed.file.name
                                    )
                                except Exception:
                                    doc_senza_file = True
                        if documento_busta is not None and (
                            existing_mov.documento_id is None or doc_senza_file
                        ):
                            existing_mov.documento = documento_busta
                            changed_fields.append('documento')
                        if netto is not None and existing_mov.importo_netto is None:
                            existing_mov.importo_netto = netto
                            changed_fields.append('importo_netto')
                            if existing_mov.importo is None:
                                existing_mov.importo = netto
                                changed_fields.append('importo')
                        if lordo is not None and existing_mov.importo_lordo is None:
                            existing_mov.importo_lordo = lordo
                            changed_fields.append('importo_lordo')
                        if not existing_mov.cf_estratto and r.get('cf'):
                            existing_mov.cf_estratto = (r.get('cf') or '')[:16]
                            changed_fields.append('cf_estratto')
                        if not existing_mov.nominativo_estratto and r.get('full_name'):
                            existing_mov.nominativo_estratto = (r.get('full_name') or '')[:160]
                            changed_fields.append('nominativo_estratto')
                        if not existing_mov.periodo_label:
                            existing_mov.periodo_label = r.get('periodo') or f'{mese:02d}/{anno}'
                            changed_fields.append('periodo_label')
                        if not existing_mov.source_pdf:
                            existing_mov.source_pdf = str(source_pdf)
                            changed_fields.append('source_pdf')
                        if existing_mov.page_number is None:
                            existing_mov.page_number = page_num
                            changed_fields.append('page_number')

                        if changed_fields:
                            existing_mov.save(update_fields=changed_fields)
                            movimenti_upsert += 1
                            self.stdout.write(
                                self.style.WARNING(
                                    f"PATCH page={page_num} movimento esistente {mese:02d}/{anno} ({natura_busta}) aggiornato solo campi mancanti"
                                )
                            )
                        else:
                            skipped += 1
                            self.stdout.write(
                                self.style.WARNING(
                                    f"SKIP page={page_num} movimento {mese:02d}/{anno} ({natura_busta}) già completo (nessuna sovrascrittura)"
                                )
                            )
                        continue

                    MovimentoImportPaghe.objects.update_or_create(
                        azienda=azienda,
                        dipendente=dip,
                        tipo='BUSTA',
                        anno=anno,
                        mese=mese,
                        natura_busta=natura_busta,
                        defaults={
                            'documento': documento_busta,
                            'importo': netto,
                            'importo_netto': netto,
                            'importo_lordo': lordo,
                            'cf_estratto': (r.get('cf') or '')[:16],
                            'nominativo_estratto': (r.get('full_name') or '')[:160],
                            'periodo_label': r.get('periodo') or f'{mese:02d}/{anno}',
                            'source_pdf': str(source_pdf),
                            'page_number': page_num,
                        },
                    )
                    movimenti_upsert += 1

            # 3.5) Aggiorna date anagrafiche (data assunzione / cessazione) estratte dai PDF
            for r in rows:
                page_num_r = int(r.get("page") or 0)
                if not (r.get("data_assunzione_conv") or r.get("data_cessazione")):
                    continue
                dip_r = matched_map.get(page_num_r)
                if not dip_r:
                    dip_id_r = r.get("dipendente_id")
                    if dip_id_r:
                        dip_r = Dipendente.objects.filter(id=dip_id_r, azienda=azienda).first()
                if not dip_r:
                    continue
                try:
                    chg = _aggiorna_date_dipendente(dip_r, r)
                    if chg:
                        dip_r.save(update_fields=chg)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"DATE page={page_num_r} [{dip_r}] aggiornati {chg}"
                            )
                        )
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"ERROR date page={page_num_r}: {exc}"))

            # 4) registra movimento F24 mensile (e allega documento se richiesto)
            # F24 in triplice copia: deduplica per periodo/importo e conserva una sola pagina per gruppo.
            f24_unique = {}
            for rec in f24_pages:
                p = rec.get("page")
                if not p:
                    continue
                key = (
                    rec.get("period_month"),
                    rec.get("period_year"),
                    str(rec.get("f24_importo") or ""),
                )
                f24_unique.setdefault(key, int(p))
            f24_page_nums = sorted(set(f24_unique.values()))
            if f24_page_nums:
                periodo = next((x.get("periodo") for x in rows if x.get("periodo")), "00/0000")
                doc_f24 = None

                if attach_docs:
                    descr_f24 = f"Modello F24 {periodo} (import PDF)"
                    doc_f24 = Documento.objects.filter(
                        azienda=azienda,
                        dipendente__isnull=True,
                        tipo="altro",
                        descrizione=descr_f24,
                    ).first()
                    try:
                        if doc_f24:
                            has_existing_file = False
                            try:
                                has_existing_file = bool(
                                    doc_f24.file
                                    and getattr(doc_f24.file, 'name', None)
                                    and doc_f24.file.storage.exists(doc_f24.file.name)
                                )
                            except Exception:
                                has_existing_file = False
                            if not has_existing_file:
                                f24_bytes = _make_multi_page_pdf_bytes(f24_page_nums)
                                fname = f"f24_{periodo.replace('/', '_')}_azienda_{azienda.id}.pdf"
                                doc_f24.file.save(fname, ContentFile(f24_bytes), save=False)
                                doc_f24.save(update_fields=['file'])
                                docs_created += 1
                        else:
                            f24_bytes = _make_multi_page_pdf_bytes(f24_page_nums)
                            fname = f"f24_{periodo.replace('/', '_')}_azienda_{azienda.id}.pdf"
                            doc_f24 = Documento(
                                azienda=azienda,
                                dipendente=None,
                                tipo="altro",
                                descrizione=descr_f24,
                                caricato_da=None,
                                caricato_dal_dipendente=False,
                                visibile_al_dipendente=False,
                            )
                            doc_f24.file.save(fname, ContentFile(f24_bytes), save=False)
                            doc_f24.save()
                            docs_created += 1
                    except Exception as exc:
                        # Non bloccare l'import buste se il filesystem F24 non e scrivibile.
                        doc_f24 = None
                        errors += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"WARN F24 periodo={periodo}: allegato PDF non salvato ({exc}). "
                                "Continuo con movimenti/importi."
                            )
                        )

                mese, anno = _parse_periodo(periodo)
                if mese and anno:
                    importi_f24 = [_parse_decimal(x.get('f24_importo')) for x in f24_pages]
                    importi_f24 = [x for x in importi_f24 if x is not None]
                    importo_f24 = max(importi_f24) if importi_f24 else None

                    mov_f24, _ = MovimentoImportPaghe.objects.update_or_create(
                        azienda=azienda,
                        dipendente=None,
                        tipo='F24',
                        anno=anno,
                        mese=mese,
                        defaults={
                            'documento': doc_f24,
                            'importo': importo_f24,
                            'importo_netto': importo_f24,
                            'importo_lordo': None,
                            'f24_saldo_finale': importo_f24,
                            'cf_estratto': '',
                            'nominativo_estratto': 'Azienda',
                            'periodo_label': periodo,
                            'source_pdf': str(source_pdf),
                            'page_number': f24_page_nums[0],
                        },
                    )

                    if doc_f24 is not None:
                        try:
                            f24_data = _extract_f24_dettagli_da_pdf(doc_f24)
                            MovimentoImportPagheF24Dettaglio.objects.filter(movimento=mov_f24).delete()

                            detail_rows = []
                            for r in (f24_data.get('rows') or []):
                                detail_rows.append(MovimentoImportPagheF24Dettaglio(
                                    movimento=mov_f24,
                                    documento=doc_f24,
                                    sezione=r.get('sezione') or 'ALTRO',
                                    codice_tributo=(r.get('codice_tributo') or '')[:12],
                                    anno_riferimento=r.get('anno_riferimento'),
                                    periodo_riferimento=(r.get('periodo_riferimento') or '')[:16],
                                    importo_debito=r.get('importo_debito'),
                                    importo_credito=r.get('importo_credito'),
                                    ordine=r.get('ordine') or 0,
                                ))
                            if detail_rows:
                                MovimentoImportPagheF24Dettaglio.objects.bulk_create(detail_rows)

                            changed = []
                            tot_deb = f24_data.get('tot_debito')
                            tot_cred = f24_data.get('tot_credito')
                            saldo_calc = f24_data.get('saldo_finale')
                            if tot_deb is not None and mov_f24.f24_tot_debito != tot_deb:
                                mov_f24.f24_tot_debito = tot_deb
                                changed.append('f24_tot_debito')
                            if tot_cred is not None and mov_f24.f24_tot_credito != tot_cred:
                                mov_f24.f24_tot_credito = tot_cred
                                changed.append('f24_tot_credito')
                            if saldo_calc is not None and mov_f24.f24_saldo_finale != saldo_calc:
                                mov_f24.f24_saldo_finale = saldo_calc
                                changed.append('f24_saldo_finale')
                                if mov_f24.importo is None:
                                    mov_f24.importo = saldo_calc
                                    changed.append('importo')
                                if mov_f24.importo_netto is None:
                                    mov_f24.importo_netto = saldo_calc
                                    changed.append('importo_netto')
                            if changed:
                                mov_f24.save(update_fields=changed)
                        except Exception as exc:
                            errors += 1
                            self.stdout.write(self.style.ERROR(f"ERROR dettaglio F24 periodo={periodo}: {exc}"))

                    movimenti_upsert += 1

        if options["apply"]:
            _apply()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Completato: created={created}, skipped={skipped}, errors={errors}, docs_created={docs_created}, movimenti_upsert={movimenti_upsert}"
                )
            )
        else:
            for r in to_create:
                cf = (r.get("cf") or "").strip().upper() or "-"
                cognome, nome = _split_name((r.get("full_name") or "").strip())
                data_nascita = _parse_birth(r.get("birth_date"))
                self.stdout.write(
                    f"DRY-RUN page={r.get('page')} -> CREATE {cognome} {nome} | CF={cf} | nasc={data_nascita or '-'}"
                )
            if attach_docs:
                self.stdout.write("DRY-RUN docs: verrebbero allegati split PDF buste + F24 aziendale.")
            self.stdout.write(self.style.WARNING("Modalità dry-run: nessuna modifica salvata. Usa --apply per importare."))
