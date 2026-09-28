#!/usr/bin/env bash
# Installe qBittorrent (interface web) sur un Raspberry Pi / Debian.
# Usage : sudo bash install_qbittorrent.sh [PORT] [DOSSIER_TELECHARGEMENT]
#   PORT                    port de la WebUI (défaut 8080)
#   DOSSIER_TELECHARGEMENT  où stocker les fichiers (défaut /srv/downloads)
set -euo pipefail

PORT="${1:-8080}"
DL_DIR="${2:-/srv/downloads}"
USER_NAME="qbt"
SERVICE="/etc/systemd/system/qbittorrent-nox.service"

if [[ $EUID -ne 0 ]]; then
  echo "Lance ce script avec sudo." >&2
  exit 1
fi

echo "==> Installation du paquet"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y qbittorrent-nox python3

echo "==> Utilisateur système '$USER_NAME'"
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/qbittorrent \
          --shell /usr/sbin/nologin "$USER_NAME"
fi
mkdir -p "$DL_DIR"
chown "$USER_NAME:$USER_NAME" "$DL_DIR"

echo "==> Service systemd"
cat > "$SERVICE" <<EOF
[Unit]
Description=qBittorrent (nox)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
Environment=HOME=/var/lib/qbittorrent
ExecStart=/usr/bin/qbittorrent-nox --webui-port=$PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Première acceptation de l'avertissement légal (sinon nox attend une saisie)
mkdir -p /var/lib/qbittorrent/.config/qBittorrent
chown -R "$USER_NAME:$USER_NAME" /var/lib/qbittorrent
CONF=/var/lib/qbittorrent/.config/qBittorrent/qBittorrent.conf
if [[ ! -f "$CONF" ]]; then
  cat > "$CONF" <<EOF
[LegalNotice]
Accepted=true

[BitTorrent]
Session\DefaultSavePath=$DL_DIR
EOF
  chown "$USER_NAME:$USER_NAME" "$CONF"
fi

systemctl daemon-reload
systemctl enable --now qbittorrent-nox
sleep 5

echo "==> Vérification"
if curl -fsS -o /dev/null "http://localhost:$PORT"; then
  echo "WebUI OK sur le port $PORT"
else
  echo "La WebUI ne répond pas : journalctl -u qbittorrent-nox -n 50" >&2
  exit 1
fi

IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

Terminé.
  Interface : http://$IP:$PORT
  Identifiant : admin
  Mot de passe temporaire (à changer tout de suite dans Options > Interface Web) :
EOF
journalctl -u qbittorrent-nox --no-pager | grep -i "temporary password" | tail -1 \
  || echo "  (introuvable : journalctl -u qbittorrent-nox | grep -i password)"

cat <<EOF

Rappels :
  - N'ouvre pas le port $PORT sur ta box. Pour l'accès distant : Tailscale ou WireGuard.
  - Relis le code de chaque plugin de recherche avant de l'installer.
EOF
