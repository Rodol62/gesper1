#!/usr/bin/env bash
# Dipendenze di sistema per import PDF unico buste/F24 (poppler + OCR).
# Eseguire sulla VPS come root: sudo bash deploy/install-pdf-import-deps.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-ita
command -v pdftotext
command -v tesseract
echo "OK: pdftotext e tesseract installati."
