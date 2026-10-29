#!/bin/sh
# Ship the committed HEAD (plus freshly built installer wheels) to the Lightsail instance.
#
#   POKBOT_HOST=1-2-3-4.sslip.io [POKBOT_GITHUB_CLIENT_ID=Iv1...] scripts/aws/deploy.sh
set -eu
cd "$(dirname "$0")/../.."

HOST="${POKBOT_HOST:?set POKBOT_HOST (printed by provision.sh)}"
NAME="${NAME:-pokbot}"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/${NAME}-lightsail.pem}"
IP="${POKBOT_IP:-$(echo "$HOST" | sed -E 's/^([0-9]+)-([0-9]+)-([0-9]+)-([0-9]+)\..*/\1.\2.\3.\4/')}"
SSH="ssh -i $KEY_FILE -o StrictHostKeyChecking=accept-new ubuntu@$IP"

[ -z "$(git status --porcelain)" ] || { echo "commit your changes first; deploys ship HEAD" >&2; exit 1; }
REL="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)"

./scripts/build_dist.sh >/dev/null
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git archive --format=tar HEAD > "$tmp/release.tar"
COPYFILE_DISABLE=1 tar -rf "$tmp/release.tar" web/dist
gzip -9 "$tmp/release.tar"
echo "✓ built release $REL ($(du -h "$tmp/release.tar.gz" | cut -f1))"

scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "$tmp/release.tar.gz" "ubuntu@$IP:/tmp/pokbot-$REL.tar.gz"
# The GitHub OAuth client id is public (device flow has no secret) but kept out of the repo.
$SSH "sudo sh -s -- $REL /tmp/pokbot-$REL.tar.gz $HOST ${POKBOT_GITHUB_CLIENT_ID:-}" < deploy/remote_install.sh

echo "✓ live: https://$HOST"
echo "  install: curl -fsSL https://$HOST/install.sh | sh"
