#!/bin/bash
# Script di deploy sicuro per GESPER
set -e

LOCAL_DIR="/Applications/XAMPP/xamppfiles/htdocs/gesper"
REMOTE="root@94.177.201.223"
REMOTE_DIR="/var/www/gesper"

# 1. Check sintassi locale
cd "$LOCAL_DIR"
. .venv/bin/activate
python3 manage.py check

# 2. Copia TUTTO il modulo modificato e templates
scp -r accounts anagrafiche costo_lavoro documenti log_attivita notifiche notifiche_email presenze rapporto_di_lavoro report richieste ruoli static storico templates workflow manage.py settings.py urls.py wsgi.py asgi.py "$REMOTE:$REMOTE_DIR/"

# 3. Riavvia Gunicorn
ssh $REMOTE 'pkill -f gunicorn && cd /var/www/gesper && source .venv/bin/activate && nohup gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:application &'

# 4. Controlla errori subito dopo il deploy
ssh $REMOTE 'tail -n 40 /var/www/gesper/gesper.log || tail -n 40 /var/log/syslog'

echo "Deploy completato. Controlla eventuali errori sopra."
