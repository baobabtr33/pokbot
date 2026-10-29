#!/bin/sh
# First-boot setup for the pokbot Lightsail instance (passed as user-data; runs as root).
# Installs Docker + gVisor (runsc), Caddy and uv, and creates the pokbot user and directories.
# Safe to re-run.
set -eux
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https docker.io

# gVisor: the sandbox runtime for uploaded bots.
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list

# Caddy: TLS + reverse proxy.
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list

apt-get update
apt-get install -y runsc caddy

runsc install
cat > /etc/docker/daemon.json <<'JSON'
{
  "runtimes": { "runsc": { "path": "/usr/bin/runsc" } },
  "icc": false,
  "live-restore": true,
  "log-driver": "local",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl restart docker

# uv (Python toolchain) for the server.
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

id pokbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/pokbot --shell /usr/sbin/nologin pokbot
usermod -aG docker pokbot
install -d -o pokbot -g pokbot -m 755 /opt/pokbot /opt/pokbot/releases /var/lib/pokbot
install -d -o root -g pokbot -m 750 /etc/pokbot

# Bot base images, pulled ahead of the first tournament.
docker pull python:3.12-slim
docker pull node:20-slim

# 2 GB swap: small instances need headroom when several bot containers start at once.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

touch /var/lib/pokbot/.bootstrapped
