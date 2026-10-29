#!/bin/sh
# Build the wheels the installer downloads: web/dist/{*.whl,SHA256SUMS,VERSION}.
set -eu
cd "$(dirname "$0")/.."

rm -rf web/dist
mkdir -p web/dist
uv build --quiet --wheel --package pokbot-core --out-dir web/dist
uv build --quiet --wheel --package pokbot-cli --out-dir web/dist

cd web/dist
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum ./*.whl | sed 's#\./##' > SHA256SUMS
else
  shasum -a 256 ./*.whl | sed 's#\./##' > SHA256SUMS
fi
ls pokbot_cli-*.whl | sed -E 's/pokbot_cli-([^-]+)-.*/\1/' > VERSION
echo "built pokbot $(cat VERSION):"
cat SHA256SUMS
