#!/usr/bin/env bash
# Riassegna i file sotto MEDIA_ROOT: da media/documenti/<sottocartelle> a media/<sottocartelle>,
# elimina F24 vuota a livello media, rimuove la cartella documenti se vuota.
# Uso: dalla radice del progetto, indicare MEDIA_ROOT (es. ./media) come primo argomento.
set -euo pipefail
MEDIA_ROOT="${1:-${MEDIA_ROOT:-./media}}"
MEDIA_ROOT="$(cd "$(dirname "$MEDIA_ROOT")" && pwd)/$(basename "$MEDIA_ROOT")"
DOC_DIR="$MEDIA_ROOT/documenti"
F24_DIR="$MEDIA_ROOT/F24"

if [[ ! -d "$MEDIA_ROOT" ]]; then
  echo "MEDIA_ROOT non esiste: $MEDIA_ROOT" >&2
  exit 1
fi

if [[ -d "$F24_DIR" ]]; then
  if find "$F24_DIR" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    echo "F24 contiene file: non elimino. Controlla: $F24_DIR" >&2
  else
    echo "Rimuovo cartella vuota: $F24_DIR"
    rmdir "$F24_DIR" || true
  fi
fi

if [[ ! -d "$DOC_DIR" ]]; then
  echo "Nessuna cartella documenti/ sotto $MEDIA_ROOT; nulla da spostare."
  echo "Allinea il DB: python3 manage.py allinea_path_flat_media --applica"
  exit 0
fi

shopt -s nullglob
for p in "$DOC_DIR"/*; do
  base="$(basename "$p")"
  dest="$MEDIA_ROOT/$base"
  if [[ -e "$dest" ]]; then
    if [[ ! -d "$dest" ]]; then
      echo "Destinazione esiste (non directory), salto: $dest" >&2
      continue
    fi
    if find "$dest" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
      echo "Destinazione già esistente (non vuota), salto: $dest" >&2
      continue
    fi
    echo "Rimuovo destinazione vuota: $dest"
    rmdir "$dest" 2>/dev/null || true
  fi
  echo "Sposto: $p -> $dest"
  mv "$p" "$dest"
done
shopt -u nullglob

# macOS: .DS_Store lascia documenti/ non vuota per rmdir
find "$DOC_DIR" -name .DS_Store -delete 2>/dev/null || true

if rmdir "$DOC_DIR" 2>/dev/null; then
  echo "Rimossa cartella vuota: $DOC_DIR"
else
  echo "Nota: $DOC_DIR non è vuota o non è stata eliminata; controlla manualmente." >&2
fi

echo
echo "Prossimo passo: allinea i path nel database:"
echo "  python3 manage.py allinea_path_flat_media"
echo "  python3 manage.py allinea_path_flat_media --applica"
echo
echo "In produzione (gesper1), stesso ordine: sposta file, poi comandi Django in venv con settings_production."
