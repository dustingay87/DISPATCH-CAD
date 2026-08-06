#!/bin/bash
# One-command setup for the VolCAD TAIP UDP/TCP -> HTTP relay on Ubuntu/Debian.

set -e

RELAY_USER="taip"
RELAY_DIR="/opt/taip_relay"
TARGET_URL="${TAIP_RELAY_TARGET:-https://cad.dispatchtodiscipleship.net/taip/ingest}"
UDP_PORT="${TAIP_RELAY_UDP_PORT:-5005}"
TCP_PORT="${TAIP_RELAY_TCP_PORT:-5005}"
RELAY_URL="https://raw.githubusercontent.com/dustingay87/DISPATCH-CAD/main/taip_relay.py"

echo "== Installing dependencies =="
apt-get update
apt-get install -y python3 curl ufw

echo "== Creating relay user and directory =="
mkdir -p "$RELAY_DIR"
if ! id -u "$RELAY_USER" >/dev/null 2>&1; then
    useradd -r -s /bin/false -d "$RELAY_DIR" "$RELAY_USER"
fi
chown -R "$RELAY_USER:$RELAY_USER" "$RELAY_DIR"

echo "== Downloading taip_relay.py =="
curl -L -o "$RELAY_DIR/taip_relay.py" "$RELAY_URL"
chown "$RELAY_USER:$RELAY_USER" "$RELAY_DIR/taip_relay.py"
chmod 644 "$RELAY_DIR/taip_relay.py"

echo "== Creating systemd service =="
cat > /etc/systemd/system/taip_relay.service <<EOF
[Unit]
Description=TAIP UDP/TCP to HTTP relay
After=network.target

[Service]
Type=simple
User=$RELAY_USER
Group=$RELAY_USER
WorkingDirectory=$RELAY_DIR
ExecStart=/usr/bin/python3 $RELAY_DIR/taip_relay.py --target $TARGET_URL --udp-port $UDP_PORT --tcp-port $TCP_PORT
Restart=always
RestartSec=5
Environment=TAIP_RELAY_TARGET=$TARGET_URL
Environment=TAIP_RELAY_UDP_PORT=$UDP_PORT
Environment=TAIP_RELAY_TCP_PORT=$TCP_PORT

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now taip_relay

echo "== Configuring firewall =="
ufw allow 22/tcp >/dev/null
ufw allow "$UDP_PORT/udp" >/dev/null
if [ "$TCP_PORT" -gt 0 ]; then
    ufw allow "$TCP_PORT/tcp" >/dev/null
fi
ufw --force enable

echo "== Done =="
echo "Relay is running on UDP port $UDP_PORT and POSTing to $TARGET_URL"
echo "Check status with: sudo systemctl status taip_relay"
echo "Check logs with:   sudo journalctl -u taip_relay -f"
