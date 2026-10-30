#!/usr/bin/env bash
set -euo pipefail
base=/opt/p2p-overlay
health() {
    python3 - <<'PY'
import ssl, urllib.request, json, sys
from pathlib import Path
try:
    config = json.loads(Path('/etc/p2p-overlay/server.json').read_text())
    ctx = ssl.create_default_context(cafile='/etc/p2p-overlay/server.crt')
    url = 'https://' + config['server'] + ':8443/health'
    assert json.load(urllib.request.urlopen(url, context=ctx, timeout=2))['status'] == 'ok'
except Exception:
    sys.exit(1)
PY
}
wait_health() {
    for attempt in {1..20}; do
        if health; then return 0; fi
        sleep 1
    done
    return 1
}
if [[ ${1:-} == rollback ]]; then
    previous=$(readlink -f "$base/previous")
    current=$(readlink -f "$base/current")
    [[ -d "$previous/.venv" && "$previous" == "$base/releases/"* ]]
    ln -sfn "$previous" "$base/current"
    install -m 644 "$previous/scripts/p2p-overlay.service" /etc/systemd/system/p2p-overlay.service
    systemctl daemon-reload
    if ! systemctl restart p2p-overlay || ! wait_health; then
        ln -sfn "$current" "$base/current"
        install -m 644 "$current/scripts/p2p-overlay.service" /etc/systemd/system/p2p-overlay.service
        systemctl daemon-reload
        systemctl restart p2p-overlay
        exit 1
    fi
    ln -sfn "$current" "$base/previous"
    echo "Restored $previous"
    exit 0
fi
version=$2
address=$3
[[ $version =~ ^[0-9]{8}-[0-9]{6}$ ]]
release="$base/releases/$version"
[[ ! -e "$release" ]]
if ! systemctl is-active --quiet p2p-overlay; then
    if ss -H -lntu | awk '{print $5}' | grep -Eq ':(8443|3478)$'; then
        echo 'Required port 8443 or 3478 is occupied' >&2
        exit 1
    fi
fi
if ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -q 'install ok installed'; then
    apt-get update -qq
    apt-get install -y -qq python3-venv
fi
id p2p-overlay >/dev/null 2>&1 || useradd --system --home-dir "$base" --shell /usr/sbin/nologin p2p-overlay
install -d -m 755 "$base/releases" "$release"
tar -xzf "$base/incoming/$version.tgz" -C "$release"
chown -R root:root "$release"
chmod -R go-w "$release"
python3 -m venv "$release/.venv"
if [[ -f "$release/requirements.lock" ]]; then
    "$release/.venv/bin/pip" install -q -r "$release/requirements.lock"
fi
"$release/.venv/bin/pip" install -q "$release"
"$release/.venv/bin/python" -m compileall -q "$release/overlay"
"$release/.venv/bin/python" -c 'from overlay.server import create_app; from overlay.client import Client'
install -d -m 750 -o root -g p2p-overlay /etc/p2p-overlay
if [[ ! -f /etc/p2p-overlay/members.json ]]; then
    printf '%s\n' '{"version":1,"members":{}}' > /etc/p2p-overlay/members.json
fi
if [[ ! -f /etc/p2p-overlay/server.crt ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 365 -sha256 \
        -keyout /etc/p2p-overlay/server.key -out /etc/p2p-overlay/server.crt \
        -subj /CN=p2p-overlay -addext "subjectAltName=IP:$address" 2>/dev/null
fi
chown root:p2p-overlay /etc/p2p-overlay/server.{key,crt} /etc/p2p-overlay/members.json
chmod 640 /etc/p2p-overlay/server.{key,crt} /etc/p2p-overlay/members.json
# GUI approvals need an atomically replaceable, service-owned state directory.
# Preserve the previous registry as the migration/rollback snapshot in /etc.
install -d -m 700 -o p2p-overlay -g p2p-overlay /var/lib/p2p-overlay
if [[ ! -f /var/lib/p2p-overlay/members.json ]]; then
    install -m 600 -o p2p-overlay -g p2p-overlay /etc/p2p-overlay/members.json /var/lib/p2p-overlay/members.json
fi
python3 - <<'PY'
import os, secrets
path = '/etc/p2p-overlay/admin.token'
if not os.path.exists(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(secrets.token_hex(32))
PY
chown root:p2p-overlay /etc/p2p-overlay/admin.token
chmod 640 /etc/p2p-overlay/admin.token
export OVERLAY_ADDRESS="$address"
python3 - <<'PY'
import json, os
from pathlib import Path
path = Path('/etc/p2p-overlay/server.json')
path.write_text(json.dumps({'server': os.environ['OVERLAY_ADDRESS'],
                           'ca': 'server.crt', 'control_port': 8443, 'udp_port': 3478}))
path.chmod(0o600)
PY
install -m 644 "$release/scripts/p2p-overlay.service" /etc/systemd/system/p2p-overlay.service
old=$(readlink -f "$base/current" || true)
restore() {
    if [[ -n "$old" && -d "$old/.venv" ]]; then
        ln -sfn "$old" "$base/current"
        install -m 644 "$old/scripts/p2p-overlay.service" /etc/systemd/system/p2p-overlay.service
        systemctl daemon-reload
        systemctl restart p2p-overlay || true
    else
        systemctl stop p2p-overlay || true
        rm -f "$base/current"
    fi
    echo 'Deployment failed; previous service state restored' >&2
}
trap restore ERR
ln -sfn "$release" "$base/current"
systemctl daemon-reload
systemctl enable p2p-overlay >/dev/null
systemctl restart p2p-overlay
wait_health
if [[ -n "$old" && "$old" != "$release" ]]; then ln -sfn "$old" "$base/previous"; fi
trap - ERR
echo "Healthy release: $version"
echo "Administrator page: https://$address:8443/admin"
echo "Administrator credential: /etc/p2p-overlay/admin.token (not printed)"
echo "Live registry: /var/lib/p2p-overlay/members.json; clients also require peer consent"
