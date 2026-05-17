#!/bin/bash
# ============================================
# Script di setup GESPER su VPS Linux (es. Aruba Cloud / Oracle)
# Eseguire come utente con sudo. Procedura aggiornata: deploy/PROCEDURA_DEPLOY.md
# ============================================

echo "=== SETUP GESPER SU VPS LINUX ==="

# 1. Aggiornamento sistema
echo ">>> Aggiornamento sistema..."
sudo apt update && sudo apt upgrade -y

# 2. Installazione dipendenze
echo ">>> Installazione Python, Nginx..."
sudo apt install -y python3 python3-pip python3-venv nginx

# 3. Creazione directory
echo ">>> Creazione directory progetto..."
mkdir -p /home/ubuntu/gesper/logs

# 4. Copia progetto (da fare manualmente con scp prima)
# scp -r /percorso/locale/gesper/* ubuntu@IP_SERVER:/home/ubuntu/gesper/

# 5. Virtual environment
echo ">>> Creazione virtual environment..."
cd /home/ubuntu/gesper
python3 -m venv venv
source venv/bin/activate

# 6. Installazione librerie
echo ">>> Installazione requirements..."
pip install -r requirements.txt

# 7. Migrazione database
echo ">>> Migrazione database..."
export DJANGO_SETTINGS_MODULE=settings_production
python3 manage.py migrate

# 8. Creazione superuser
echo ">>> Creazione superuser..."
python3 manage.py createsuperuser

# 9. Collectstatic
echo ">>> Raccolta file statici..."
python3 manage.py collectstatic --noinput

# 10. Configurazione Nginx (split: gesper.* vs www+apex con proxy Aruba + /gesper/)
echo ">>> Configurazione Nginx..."
sudo cp deploy/nginx-gesper-production-split.conf /etc/nginx/sites-available/gesper
sudo ln -sf /etc/nginx/sites-available/gesper /etc/nginx/sites-enabled/gesper
sudo nginx -t && sudo systemctl reload nginx

# 11. Configurazione servizi Gunicorn (gesper + gesper-www per prefisso /gesper/ su www)
echo ">>> Configurazione servizi GESPER..."
sudo cp deploy/gunicorn.service /etc/systemd/system/gesper.service
sudo cp deploy/gesper-www.service.production /etc/systemd/system/gesper-www.service
sudo systemctl daemon-reload
sudo systemctl enable gesper gesper-www
sudo systemctl start gesper gesper-www

# 12. Firewall (preferire ufw su Ubuntu: ufw allow OpenSSH; ufw allow 80,443/tcp; ufw enable)
echo ">>> Configurazione firewall (esempio iptables — adattare o usare ufw)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 7 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo ""
echo "=== SETUP COMPLETATO ==="
echo "GESPER: https://gesper.DOMINIO/ (root) e https://www.DOMINIO/gesper/ (vedi nginx split + gesper-www)"
echo ""
echo "PROSSIMI PASSI:"
echo "1. DNS (Hosting/domini): A www → IP pubblico QUESTA VPS (vedi deploy/PROCEDURA_DEPLOY.md)"
echo "2. TLS: sudo certbot --nginx -d gesper1.plazapretoria.it (e altri -d se necessario); non duplicare SSL a mano"
echo "3. DJANGO_SECRET_KEY e percorsi in systemd (gesper / gesper-www)"
echo ""
