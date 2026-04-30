/**
 * Invio multipart via XHR con barra di avanzamento (% upload verso il server).
 * Agganciare la classe `gesper-multipart-upload` ai form con file.
 * Escludere: `data-no-upload-progress="1"` oppure form senza file con dimensione > 0.
 */
(function () {
  function csrfFromForm(form) {
    var inp = form.querySelector('[name=csrfmiddlewaretoken]');
    if (inp && inp.value) return inp.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1].trim()) : '';
  }

  function formHasRealFile(form) {
    var inputs = form.querySelectorAll('input[type="file"]');
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      if (el.files && el.files.length) {
        for (var j = 0; j < el.files.length; j++) {
          if (el.files[j].size > 0) return true;
        }
      }
    }
    return false;
  }

  function ensureBar(form) {
    var w = form.querySelector('.gesper-upload-progress-wrap');
    if (w) return w;
    w = document.createElement('div');
    w.className = 'gesper-upload-progress-wrap d-none mb-2 w-100';
    w.innerHTML =
      '<div class="progress" style="height:1.15rem" role="progressbar" aria-valuemin="0" aria-valuemax="100">' +
      '<div class="progress-bar progress-bar-striped progress-bar-animated gesper-upload-progress-bar" ' +
      'style="width:0%">0%</div></div>' +
      '<div class="small text-muted text-center mt-1 gesper-upload-progress-label"></div>';
    var first = form.firstElementChild;
    if (first) form.insertBefore(w, first);
    else form.appendChild(w);
    return w;
  }

  function bindForm(form) {
    if (form.dataset.gesperUploadBound === '1') return;
    form.dataset.gesperUploadBound = '1';
    form.addEventListener('submit', function (e) {
      if (form.getAttribute('data-no-upload-progress') === '1') return;
      if (!formHasRealFile(form)) return;
      e.preventDefault();
      var wrap = ensureBar(form);
      var bar = wrap.querySelector('.gesper-upload-progress-bar');
      var label = wrap.querySelector('.gesper-upload-progress-label');
      var btn = form.querySelector('button[type="submit"]');
      wrap.classList.remove('d-none');
      if (bar) {
        bar.style.width = '0%';
        bar.textContent = '0%';
      }
      if (label) label.textContent = 'Invio in corso…';
      if (btn) {
        btn.disabled = true;
        btn.dataset.prevHtml = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Invio…';
      }

      var fd = new FormData(form);
      var xhr = new XMLHttpRequest();
      var action = form.getAttribute('action');
      var url = action && action.length ? action : window.location.href.split('#')[0];

      xhr.open('POST', url, true);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      var tok = csrfFromForm(form);
      if (tok) xhr.setRequestHeader('X-CSRFToken', tok);

      xhr.upload.addEventListener('progress', function (ev) {
        if (!ev.lengthComputable || !bar) return;
        var pct = Math.max(0, Math.min(100, Math.round((100 * ev.loaded) / ev.total)));
        bar.style.width = pct + '%';
        bar.textContent = pct + '%';
        if (label) {
          if (pct >= 100) {
            label.textContent = 'Upload completato. Elaborazione sul server in corso…';
          } else {
            label.textContent =
              'Caricamento: ' + pct + '% (' + Math.round(ev.loaded / 1024) + ' / ' + Math.round(ev.total / 1024) + ' KB)';
          }
        }
      });

      xhr.onload = function () {
        if (btn) {
          btn.disabled = false;
          if (btn.dataset.prevHtml) btn.innerHTML = btn.dataset.prevHtml;
        }
        if (xhr.status >= 200 && xhr.status < 400) {
          var dest = xhr.responseURL || url;
          window.location.assign(dest);
          return;
        }
        wrap.classList.add('d-none');
        if (label) label.textContent = '';
        var msg =
          xhr.status === 403
            ? 'Sessione scaduta o permesso negato (403). Ricarica la pagina e riprova.'
            : 'Errore durante l\'invio (HTTP ' + xhr.status + ').';
        window.alert(msg);
      };

      xhr.onerror = function () {
        if (btn) {
          btn.disabled = false;
          if (btn.dataset.prevHtml) btn.innerHTML = btn.dataset.prevHtml;
        }
        wrap.classList.add('d-none');
        window.alert('Errore di rete durante l\'invio. Verifica la connessione e riprova.');
      };

      xhr.send(fd);
    });
  }

  function scan() {
    document.querySelectorAll('form.gesper-multipart-upload').forEach(bindForm);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scan);
  } else {
    scan();
  }
})();
