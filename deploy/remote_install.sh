#!/bin/sh
# Runs on the instance (as root, via scripts/aws/deploy.sh):
#   remote_install.sh <release-id> <tarball> <public-host> [github-client-id]
# Unpacks a release, installs deps, switches the `current` symlink, restarts, health-checks,
# and rolls back on failure. Refuses to restart while a tournament is running.
set -eu
REL="$1"; TARBALL="$2"; HOST="$3"; GITHUB_CLIENT_ID="${4:-}"
ROOT=/opt/pokbot
DIR="$ROOT/releases/$REL"

i=0
until [ -f /var/lib/pokbot/.bootstrapped ]; do
  i=$((i + 1)); [ "$i" -gt 90 ] && { echo "bootstrap did not finish; see /var/log/cloud-init-output.log" >&2; exit 1; }
  echo "waiting for first-boot setup…"; sleep 10
done

if curl -fsS --max-time 3 http://127.0.0.1:8000/api/schedule 2>/dev/null | grep -q '"running":"t_'; then
  echo "a tournament is running; deploy again after it ends" >&2
  exit 75
fi

rm -rf "$DIR"; mkdir -p "$DIR"
tar -xzf "$TARBALL" -C "$DIR"
chown -R pokbot:pokbot "$DIR"
chmod -R a+rX "$DIR"   # bot containers (uid 65534) read core/src from here
sudo -u pokbot -H env UV_CACHE_DIR=/var/lib/pokbot/.cache/uv \
  /usr/local/bin/uv sync --frozen --no-dev --python 3.12 --directory "$DIR"

ENVF=/etc/pokbot/env
touch "$ENVF"
setenv() {  # setenv KEY VALUE [force]: add if missing, replace when forced
  if grep -q "^$1=" "$ENVF"; then
    [ "${3:-}" = force ] || return 0
    sed -i "/^$1=/d" "$ENVF"
  fi
  echo "$1=$2" >> "$ENVF"
}
setenv POKBOT_PUBLIC_URL "https://$HOST" force
setenv POKBOT_DATA /var/lib/pokbot/data
setenv POKBOT_SANDBOX docker
setenv POKBOT_DOCKER_RUNTIME runsc
setenv POKBOT_MAX_PARALLEL_TABLES 1
if [ -n "$GITHUB_CLIENT_ID" ]; then setenv POKBOT_GITHUB_CLIENT_ID "$GITHUB_CLIENT_ID" force; fi
chown root:pokbot /etc/pokbot/env; chmod 640 /etc/pokbot/env

install -m 644 "$DIR/deploy/pokbot.service" /etc/systemd/system/pokbot.service
install -m 644 "$DIR/deploy/pokbot-reaper.service" /etc/systemd/system/pokbot-reaper.service
install -m 644 "$DIR/deploy/pokbot-reaper.timer" /etc/systemd/system/pokbot-reaper.timer
install -m 644 "$DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
echo "POKBOT_HOST=$HOST" > /etc/default/caddy
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/default/caddy\n' > /etc/systemd/system/caddy.service.d/pokbot.conf

PREV="$(readlink -f "$ROOT/current" 2>/dev/null || true)"
ln -sfn "$DIR" "$ROOT/current"
systemctl daemon-reload
systemctl enable --now pokbot-reaper.timer
systemctl enable pokbot caddy
systemctl restart pokbot
systemctl restart caddy

i=0
until curl -fsS --max-time 2 http://127.0.0.1:8000/api/schedule >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 30 ]; then
    echo "health check failed" >&2
    journalctl -u pokbot -n 30 --no-pager >&2 || true
    if [ -n "$PREV" ] && [ "$PREV" != "$DIR" ]; then
      echo "rolling back to $PREV" >&2
      ln -sfn "$PREV" "$ROOT/current"; systemctl restart pokbot
    fi
    exit 1
  fi
  sleep 1
done

# Keep the five newest releases.
ls -1dt "$ROOT"/releases/* | tail -n +6 | xargs -r rm -rf
rm -f "$TARBALL"
echo "deployed $REL at https://$HOST"
