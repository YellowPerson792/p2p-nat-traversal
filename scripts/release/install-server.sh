#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root: sudo bash install.sh SERVER_IPV4' >&2; exit 1; }
[[ $# == 1 && $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo 'Expected SERVER_IPV4' >&2; exit 1; }
address=$1
IFS=. read -r -a octets <<< "$address"
for octet in "${octets[@]}"; do (( 10#$octet <= 255 )) || exit 1; done
for command in openssl curl systemctl ss; do command -v "$command" >/dev/null; done
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
version=$(cat "$package/VERSION")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
base=/opt/p2p-overlay-native
release="$base/releases/$version"
[[ ! -e "$release" ]] || { echo "Release $version already installed" >&2; exit 1; }
if ! systemctl is-active --quiet p2p-overlay-native; then
    if ss -H -lntu | awk '{print $5}' | grep -Eq ':(8443|3478)$'; then
        echo 'TCP 8443 or UDP 3478 is already occupied' >&2; exit 1
    fi
fi
id p2p-overlay-native >/dev/null 2>&1 || useradd --system --home-dir "$base" --shell /usr/sbin/nologin p2p-overlay-native
install -d -m 755 "$base/releases" "$release"
cp -a "$package/bin/." "$release/"
chmod 755 "$release/p2p-overlay"
chown -R root:root "$release"
chmod -R go-w "$release"
"$release/p2p-overlay" doctor
config=/etc/p2p-overlay-native
state=/var/lib/p2p-overlay-native
install -d -m 750 -o root -g p2p-overlay-native "$config"
install -d -m 700 -o p2p-overlay-native -g p2p-overlay-native "$state"
if [[ ! -f "$config/server.crt" ]]; then
    [[ ! -e "$config/server.key" ]] || { echo 'Existing private key without certificate' >&2; exit 1; }
    (umask 077; openssl req -x509 -newkey ed25519 -nodes -days 365 \
        -keyout "$config/server.key" -out "$config/server.crt" \
        -subj "/CN=$address" -addext "subjectAltName=IP:$address")
fi
openssl x509 -in "$config/server.crt" -noout -checkip "$address" >/dev/null
chown root:p2p-overlay-native "$config/server.key" "$config/server.crt"
chmod 640 "$config/server.key" "$config/server.crt"
if [[ ! -e "$config/admin.token" ]]; then
    (umask 077; openssl rand -hex 32 > "$config/admin.token")
fi
chown root:p2p-overlay-native "$config/admin.token"
chmod 640 "$config/admin.token"
if [[ ! -e "$state/members.json" ]]; then
    (umask 077; printf '%s\n' '{"version":1,"members":{}}' > "$state/members.json")
    chown p2p-overlay-native:p2p-overlay-native "$state/members.json"
fi
cat > "$release/p2p-overlay-native.service" <<'UNIT'
[Unit]
Description=P2P NAT traversal coordinator and encrypted relay
After=network-online.target
Wants=network-online.target
[Service]
User=p2p-overlay-native
Group=p2p-overlay-native
WorkingDirectory=/opt/p2p-overlay-native/current
ExecStart=/opt/p2p-overlay-native/current/p2p-overlay server --cert /etc/p2p-overlay-native/server.crt --key /etc/p2p-overlay-native/server.key --registry /var/lib/p2p-overlay-native/members.json --admin-token-file /etc/p2p-overlay-native/admin.token
StateDirectory=p2p-overlay-native
StateDirectoryMode=0700
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
old=$(readlink -f "$base/current" || true)
restore() {
    if [[ -n "$old" && -f "$old/p2p-overlay-native.service" ]]; then
        ln -sfn "$old" "$base/current"
        install -m 644 "$old/p2p-overlay-native.service" /etc/systemd/system/p2p-overlay-native.service
        systemctl daemon-reload
        systemctl restart p2p-overlay-native || true
    else
        systemctl stop p2p-overlay-native || true
    fi
    echo 'Startup failed; previous program restored if available. Inspect journalctl.' >&2
}
trap restore ERR
ln -sfn "$release" "$base/current"
install -m 644 "$release/p2p-overlay-native.service" /etc/systemd/system/p2p-overlay-native.service
systemctl daemon-reload
systemctl enable p2p-overlay-native
systemctl restart p2p-overlay-native
healthy=false
for attempt in {1..20}; do
    if curl --silent --fail --noproxy '*' --connect-timeout 2 --max-time 3 \
        --cacert "$config/server.crt" --resolve "$address:8443:127.0.0.1" \
        "https://$address:8443/health" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then healthy=true; break; fi
    sleep 1
done
[[ $healthy == true ]]
if [[ -n "$old" && "$old" != "$release" ]]; then ln -sfn "$old" "$base/previous"; fi
trap - ERR
echo "Healthy coordinator: https://$address:8443/admin"
echo "Administrator credential: $config/admin.token (not printed)"
echo 'Open TCP 8443 and UDP 3478. Trust the server certificate on each client.'
