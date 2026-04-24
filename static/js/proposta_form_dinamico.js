/**
 * Proposta assunzione — cascata CCNL + tipo contratto → retribuzione (API).
 * Livello CCNL filtra le opzioni qualifica; posizione/livello_ccnl sono hidden e compilati al salvataggio.
 */

document.addEventListener('DOMContentLoaded', function () {
    function gesperUrlPath(pathFromRoot) {
        if (typeof window !== 'undefined' && typeof window.gesperUrlPath === 'function') {
            return window.gesperUrlPath(pathFromRoot);
        }
        var sn = (typeof window !== 'undefined' && window.__GESPER_SCRIPT_NAME) ? String(window.__GESPER_SCRIPT_NAME).trim() : '';
        sn = sn.replace(/\/$/, '');
        var p = (pathFromRoot || '').trim();
        if (!p.startsWith('/')) p = '/' + p;
        return sn + p;
    }

    const JS_BUILD_VERSION = '2026-04-21-1';
    const marker = document.getElementById('js-build-marker');
    const isEditMode = marker && marker.getAttribute('data-edit-mode') === '1';
    if (marker) {
        marker.setAttribute('data-loaded-js-version', JS_BUILD_VERSION);
    }
    const livelloGruppo = document.getElementById('id_ccnl_livello_scelta');
    const parametroSelect = document.getElementById('id_parametro_ccnl');
    const tipoContrattoSelect = document.getElementById('id_tipo_contratto');
    const mansioneSelect = document.getElementById('id_mansione');
    const qualificaEl = document.getElementById('id_qualifica');
    const cbTredicesima = document.getElementById('id_tredicesima');
    const cbQuattordicesima = document.getElementById('id_quattordicesima');
    let rateiTouchedByUser = Boolean(isEditMode);

    const campiEconomici = {
        stipendio: 'id_stipendio_lordo_mensile',
        paga_base: 'id_paga_base_mensile',
        contingenza: 'id_contingenza_mensile',
        edr: 'id_edr_mensile',
        indennita: 'id_indennita_mensile',
        giorni_ferie: 'id_giorni_ferie_annuali',
        giorni_permesso: 'id_giorni_permesso_annuali',
        ore_sett: 'id_ore_settimanali',
        ore_mens: 'id_ore_mensili',
        ore_giorn: 'id_ore_giornaliere',
        ferie_annue_giorni: 'id_ferie_annue_giorni',
        permessi_annui_ore: 'id_permessi_annui_ore',
        decorrenza_da: 'id_decorrenza_validita_da',
        decorrenza_a: 'id_decorrenza_validita_a',
        scatto_periodo: 'id_scatto_periodicita_mesi',
        scatto_importo: 'id_scatto_importo',
        scatto_max: 'id_numero_scatti_massimi',
        straord_diurno: 'id_straordinario_diurno_maggiorazione',
        straord_notturno: 'id_straordinario_notturno_maggiorazione',
        straord_festivo: 'id_straordinario_festivo_maggiorazione',
        riposi_regola: 'id_riposi_compensativi_regola',
    };

    function trimStr(s) {
        return String(s == null ? '' : s).trim();
    }

    /** Allinea es. "4" con "4.0" o confronto numerico se entrambi numerici. */
    function livelliCoerenti(livScelto, livOpt) {
        const a = trimStr(livScelto);
        const b = trimStr(livOpt);
        if (!a || !b) {
            return !a;
        }
        if (a === b) {
            return true;
        }
        const na = Number(a.replace(',', '.'));
        const nb = Number(b.replace(',', '.'));
        if (Number.isFinite(na) && Number.isFinite(nb)) {
            return na === nb;
        }
        return a.toLowerCase() === b.toLowerCase();
    }

    function filtraParametriPerLivello() {
        if (!parametroSelect || !livelloGruppo) {
            return;
        }
        const liv = trimStr(livelloGruppo.value);
        let selectedHidden = false;
        let visibiliConValore = 0;
        Array.from(parametroSelect.options).forEach(function (opt) {
            if (!opt.value) {
                opt.hidden = false;
                return;
            }
            const ol = opt.getAttribute('data-livello');
            const hide = Boolean(liv) && !livelliCoerenti(liv, ol);
            opt.hidden = hide;
            if (!hide) {
                visibiliConValore += 1;
            }
            if (hide && opt.selected) {
                selectedHidden = true;
            }
        });
        /* Se il livello è scelto ma nessuna opzione ha data-livello (HTML vecchio / errore), non filtrare. */
        if (liv && visibiliConValore === 0) {
            Array.from(parametroSelect.options).forEach(function (opt) {
                opt.hidden = false;
            });
        }
        if (selectedHidden) {
            parametroSelect.value = '';
        }
    }

    function filtraMansioniPerLivello() {
        if (!mansioneSelect || !livelloGruppo) {
            return;
        }
        const liv = trimStr(livelloGruppo.value);
        let selectedHidden = false;
        let visibili = 0;
        Array.from(mansioneSelect.options).forEach(function (opt) {
            if (!opt.value) {
                opt.hidden = false;
                return;
            }
            const livelli = trimStr(opt.getAttribute('data-livelli'));
            if (!livelli) {
                opt.hidden = false;
                visibili += 1;
                return;
            }
            const allowed = livelli.split('|').map(function (x) { return trimStr(x); }).filter(Boolean);
            const show = !liv || allowed.some(function (x) { return livelliCoerenti(liv, x); });
            opt.hidden = !show;
            if (show) {
                visibili += 1;
            }
            if (!show && opt.selected) {
                selectedHidden = true;
            }
        });
        if (selectedHidden) {
            mansioneSelect.value = '';
        }
        if (liv && visibili === 0) {
            // Nessuna mappa compatibile: lascia visibili tutte per evitare blocco operativo.
            Array.from(mansioneSelect.options).forEach(function (opt) {
                opt.hidden = false;
            });
        }
    }

    function aggiornaMansioniCoerentiUI() {
        if (!livelloGruppo || !parametroSelect) {
            return;
        }
        const box = document.getElementById('msg-mansioni-coerenti');
        if (!box) {
            return;
        }
        const livello = trimStr(livelloGruppo.value);
        if (!livello) {
            box.style.display = 'none';
            return;
        }

        // Prima fonte: mansioni realmente disponibili dopo filtro livello (tabella ponte).
        let qualifiche = mansioneSelect
            ? Array.from(mansioneSelect.options)
                .filter(function (opt) { return Boolean(opt.value) && !opt.hidden; })
                .map(function (opt) { return trimStr(opt.textContent); })
                .filter(function (q) { return Boolean(q); })
            : [];
        // Fallback: qualifiche tabellari opzioni parametro CCNL.
        if (!qualifiche.length) {
            qualifiche = Array.from(parametroSelect.options)
                .filter(function (opt) { return Boolean(opt.value) && !opt.hidden; })
                .map(function (opt) { return trimStr(opt.getAttribute('data-qualifica')); })
                .filter(function (q) { return Boolean(q); });
        }

        const uniche = Array.from(new Set(qualifiche));
        if (!uniche.length) {
            box.className = 'alert alert-warning border py-2 mt-2 mb-0';
            box.textContent = 'Nessuna mansione mappata per il livello selezionato. Verifica le mappature in Admin.';
            box.style.display = 'block';
            return;
        }

        const selectedMansioneTxt = mansioneSelect && mansioneSelect.selectedOptions[0]
            ? trimStr(mansioneSelect.selectedOptions[0].textContent)
            : '';
        const coerente = selectedMansioneTxt
            ? uniche.some(function (q) { return q.toLowerCase() === selectedMansioneTxt.toLowerCase(); })
            : false;

        box.className = 'alert border py-2 mt-2 mb-0 ' + (coerente ? 'alert-success' : 'alert-light');
        box.innerHTML =
            '<strong>Livello ' + livello + ':</strong> mansioni/qualifiche coerenti: ' +
            uniche.map(function (q) { return '<span class="badge bg-secondary me-1">' + q + '</span>'; }).join(' ') +
            (selectedMansioneTxt ? ('<div class="small mt-1 ' + (coerente ? 'text-success' : 'text-warning') + '">' +
                (coerente
                    ? 'La mansione selezionata e coerente con il livello.'
                    : 'La mansione selezionata non e coerente con il livello: scegli una delle qualifiche sopra.')
                + '</div>') : '');
        box.style.display = 'block';
    }

    function numOrEmpty(v, decimals) {
        const n = Number(v);
        if (!Number.isFinite(n)) {
            return '';
        }
        return decimals != null ? n.toFixed(decimals) : String(n);
    }

    function parseNum(v) {
        const s = trimStr(v).replace(',', '.');
        const n = Number(s);
        return Number.isFinite(n) ? n : 0;
    }

    function mostraFonteRetribuzione(text, level) {
        const box = document.getElementById('msg-retribuzione-source');
        if (!box) {
            return;
        }
        box.className = 'alert py-2 mb-3';
        if (level === 'warning') {
            box.classList.add('alert-warning');
        } else {
            box.classList.add('alert-success');
        }
        box.textContent = text;
        box.style.display = 'block';
    }

    function ricalcolaLordoDaComponenti() {
        const pagaBaseEl = document.getElementById(campiEconomici.paga_base);
        const contingenzaEl = document.getElementById(campiEconomici.contingenza);
        const edrEl = document.getElementById(campiEconomici.edr);
        const indennitaEl = document.getElementById(campiEconomici.indennita);
        const lordoEl = document.getElementById(campiEconomici.stipendio);
        if (!pagaBaseEl || !contingenzaEl || !edrEl || !indennitaEl || !lordoEl) {
            return;
        }
        const totale = parseNum(pagaBaseEl.value) + parseNum(contingenzaEl.value) + parseNum(edrEl.value) + parseNum(indennitaEl.value);
        lordoEl.value = numOrEmpty(totale, 2);
    }

    function normalizzaCampiOreDueDecimali() {
        [campiEconomici.ore_sett, campiEconomici.ore_mens, campiEconomici.ore_giorn].forEach(function (id) {
            const el = document.getElementById(id);
            if (!el || trimStr(el.value) === '') {
                return;
            }
            el.value = numOrEmpty(el.value, 2);
        });
    }

    function mostraTooltipTipoContratto(desc, coeff) {
        const msg = document.getElementById('msg-tipo-contratto-info');
        if (!msg) {
            return;
        }
        const c = Number(coeff);
        const pct = Number.isFinite(c) ? (c * 100).toFixed(0) : '?';
        msg.innerHTML =
            'Tipo: ' + (desc || '—') + (Number.isFinite(c) ? ' (ore al ' + pct + '%)' : '');
        msg.style.display = 'block';
    }

    function applicaDatiCcnl(data) {
        const fields = {};
        fields[campiEconomici.stipendio] = numOrEmpty(data.stipendio_lordo_mensile, 2);
        fields[campiEconomici.paga_base] = numOrEmpty(data.paga_base_mensile, 2);
        fields[campiEconomici.contingenza] = numOrEmpty(data.contingenza_mensile, 2);
        fields[campiEconomici.edr] = numOrEmpty(data.edr_mensile, 2);
        fields[campiEconomici.indennita] = numOrEmpty(data.indennita_mensile, 2);
        fields[campiEconomici.giorni_ferie] = data.giorni_ferie_annuali;
        fields[campiEconomici.giorni_permesso] = data.giorni_permesso_annuali;
        fields[campiEconomici.ore_sett] = numOrEmpty(data.ore_settimanali, 2);
        fields[campiEconomici.ore_mens] = numOrEmpty(data.ore_mensili, 2);
        fields[campiEconomici.ore_giorn] = numOrEmpty(data.ore_giornaliere, 2);
        fields[campiEconomici.ferie_annue_giorni] = numOrEmpty(data.ferie_annue_giorni, 2);
        fields[campiEconomici.permessi_annui_ore] = numOrEmpty(data.permessi_annui_ore, 2);
        fields[campiEconomici.decorrenza_da] = data.decorrenza_validita_da || '';
        fields[campiEconomici.decorrenza_a] = data.decorrenza_validita_a || '';
        fields[campiEconomici.scatto_periodo] = data.scatto_periodicita_mesi;
        fields[campiEconomici.scatto_importo] = numOrEmpty(data.scatto_importo, 2);
        fields[campiEconomici.scatto_max] = data.numero_scatti_massimi;
        fields[campiEconomici.straord_diurno] = numOrEmpty(data.straordinario_diurno_maggiorazione, 2);
        fields[campiEconomici.straord_notturno] = numOrEmpty(data.straordinario_notturno_maggiorazione, 2);
        fields[campiEconomici.straord_festivo] = numOrEmpty(data.straordinario_festivo_maggiorazione, 2);
        fields[campiEconomici.riposi_regola] = data.riposi_compensativi_regola || '';

        Object.keys(fields).forEach(function (fieldId) {
            const elem = document.getElementById(fieldId);
            if (elem) {
                const v = fields[fieldId];
                elem.value = v === null || v === undefined ? '' : v;
            }
        });

        if (qualificaEl && data.qualifica) {
            qualificaEl.value = String(data.qualifica);
        }

        if (cbTredicesima && data.tredicesima !== undefined && !rateiTouchedByUser) {
            cbTredicesima.checked = !!data.tredicesima;
        }
        if (cbQuattordicesima && data.quattordicesima !== undefined && !rateiTouchedByUser) {
            cbQuattordicesima.checked = !!data.quattordicesima;
        }

        const titoloField = document.getElementById('id_titolo');
        if (titoloField && data.qualifica && data.livello) {
            const titoloAuto = 'Proposta assunzione — ' + data.qualifica + ' Livello ' + data.livello;
            const valoreAttuale = titoloField.value.trim();
            const isProposto =
                valoreAttuale === '' || /^Proposta assunzione[— -]/i.test(valoreAttuale);
            if (isProposto) {
                titoloField.value = titoloAuto;
            }
        }

        mostraTooltipTipoContratto(data.tipo_contratto_desc, data.coefficiente_ore);
        mostraFonteRetribuzione('Dati retributivi aggiornati automaticamente da CCNL (' + (data.fonte_dati || 'sistema') + ').', 'success');
        ricalcolaLordoDaComponenti();
    }

    function aggiornaQualificaDaOpzione() {
        const mansioneOpt = mansioneSelect && mansioneSelect.selectedOptions[0];
        const mansioneTxt = mansioneOpt ? trimStr(mansioneOpt.textContent) : '';
        if (qualificaEl && mansioneTxt) {
            qualificaEl.value = mansioneTxt;
            return;
        }
        const opt = parametroSelect && parametroSelect.selectedOptions[0];
        const qu = opt ? trimStr(opt.getAttribute('data-qualifica')) : '';
        if (qualificaEl && qu) {
            qualificaEl.value = qu;
        }
    }

    function autoSelezionaParametroVisibile() {
        if (!parametroSelect) {
            return;
        }
        const opzioni = Array.from(parametroSelect.options);
        const mansioneOpt = mansioneSelect && mansioneSelect.selectedOptions[0];
        const mansioneTxt = mansioneOpt ? trimStr(mansioneOpt.textContent).toLowerCase() : '';
        const selezionata = parametroSelect.selectedOptions[0];
        if (selezionata && selezionata.value && !selezionata.hidden) {
            return;
        }
        if (mansioneTxt) {
            const coerenteMansione = opzioni.find(function (opt) {
                if (!opt.value || opt.hidden) {
                    return false;
                }
                const q = trimStr(opt.getAttribute('data-qualifica')).toLowerCase();
                return q && q === mansioneTxt;
            });
            if (coerenteMansione) {
                parametroSelect.value = coerenteMansione.value;
                return;
            }
        }
        const primaVisibile = opzioni.find(function (opt) {
            return Boolean(opt.value) && !opt.hidden;
        });
        parametroSelect.value = primaVisibile ? primaVisibile.value : '';
    }

    function verificaEcompila() {
        aggiornaQualificaDaOpzione();
        let parametroId = parametroSelect && trimStr(parametroSelect.value);
        const tipoContrattoId = tipoContrattoSelect && trimStr(tipoContrattoSelect.value);

        if (!parametroId) {
            filtraParametriPerLivello();
            autoSelezionaParametroVisibile();
            parametroId = parametroSelect && trimStr(parametroSelect.value);
            aggiornaQualificaDaOpzione();
        }

        if (!parametroId) {
            return;
        }

        const url = new URL(gesperUrlPath('/rapporti/api/ccnl-parametri/'), window.location.origin);
        url.searchParams.append('parametro_id', parametroId);
        if (tipoContrattoId) {
            url.searchParams.append('tipo_contratto_id', tipoContrattoId);
        }

        fetch(url, { credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('Errore API ccnl-parametri');
                }
                return response.json();
            })
            .then(applicaDatiCcnl)
            .catch(function (error) {
                console.error('Errore nel caricamento parametri CCNL:', error);
            });
        aggiornaMansioniCoerentiUI();
    }

    if (livelloGruppo) {
        livelloGruppo.addEventListener('change', function () {
            filtraParametriPerLivello();
            filtraMansioniPerLivello();
            autoSelezionaParametroVisibile();
            aggiornaQualificaDaOpzione();
            aggiornaMansioniCoerentiUI();
            verificaEcompila();
        });
    }
    if (parametroSelect) {
        parametroSelect.addEventListener('change', function () {
            aggiornaQualificaDaOpzione();
            aggiornaMansioniCoerentiUI();
            verificaEcompila();
        });
    }
    if (tipoContrattoSelect) {
        tipoContrattoSelect.addEventListener('change', function () {
            // Punto 5 -> Punto 6: riallinea sempre parametro/qualifica prima del ricalcolo.
            filtraParametriPerLivello();
            filtraMansioniPerLivello();
            autoSelezionaParametroVisibile();
            aggiornaQualificaDaOpzione();
            aggiornaMansioniCoerentiUI();
            verificaEcompila();
        });
    }
    if (mansioneSelect) {
        mansioneSelect.addEventListener('change', function () {
            autoSelezionaParametroVisibile();
            aggiornaQualificaDaOpzione();
            aggiornaMansioniCoerentiUI();
            verificaEcompila();
        });
    }
    if (cbTredicesima) {
        cbTredicesima.addEventListener('change', function () {
            rateiTouchedByUser = true;
        });
    }
    if (cbQuattordicesima) {
        cbQuattordicesima.addEventListener('change', function () {
            rateiTouchedByUser = true;
        });
    }
    const indennitaInput = document.getElementById(campiEconomici.indennita);
    if (indennitaInput) {
        indennitaInput.addEventListener('input', function () {
            ricalcolaLordoDaComponenti();
            mostraFonteRetribuzione('Lordo mensile ricalcolato includendo l\'indennita inserita manualmente.', 'warning');
        });
    }

    const formEl = document.querySelector('form');
    if (formEl) {
        formEl.addEventListener('submit', function () {
            normalizzaCampiOreDueDecimali();
        });
    }

    filtraParametriPerLivello();
    filtraMansioniPerLivello();
    autoSelezionaParametroVisibile();
    aggiornaQualificaDaOpzione();
    aggiornaMansioniCoerentiUI();
    ricalcolaLordoDaComponenti();
    normalizzaCampiOreDueDecimali();
    if (!isEditMode) {
        setTimeout(verificaEcompila, 250);
    }
});
