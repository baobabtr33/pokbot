#!/bin/sh
# pokbot installer:  curl -fsSL __POKBOT_BASE__/install.sh | sh
#
# Downloads the pokbot wheels from this server, verifies their sha256, and installs the
# `pokbot` command into ~/.pokbot/bin using uv (fetched into ~/.pokbot/uv if missing).
#
# Env overrides: POKBOT_BASE, POKBOT_HOME, POKBOT_NO_MODIFY_PATH=1
set -eu

BASE="${POKBOT_BASE:-__POKBOT_BASE__}"
POKBOT_HOME="${POKBOT_HOME:-$HOME/.pokbot}"
BIN_DIR="$POKBOT_HOME/bin"

if [ -t 1 ]; then
  B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; G="$(printf '\033[32m')"
  R="$(printf '\033[31m')"; Y="$(printf '\033[33m')"; N="$(printf '\033[0m')"
else
  B=""; D=""; G=""; R=""; Y=""; N=""
fi

ok()   { printf '%s✓%s %s\n' "$G" "$N" "$1"; }
fail() { printf '%s✗%s %s\n' "$R" "$N" "$1" >&2; exit 1; }
tilde() { printf '%s' "$1" | sed "s#^$HOME#~#"; }

printf '%s◆ pokbot%s · poker bot tournaments\n' "$Y$B" "$N"
printf '  %stexas hold'"'"'em · knockout tables · every 30 minutes%s\n \n' "$D" "$N"

# -- platform --------------------------------------------------------------------------------
os="$(uname -s)"; arch="$(uname -m)"
case "$os" in
  Darwin) os_name="macOS" ;;
  Linux)  os_name="Linux" ;;
  *) fail "unsupported OS: $os (macOS and Linux only; on Windows use WSL)" ;;
esac
case "$arch" in
  arm64|aarch64) arch_name="arm64" ;;
  x86_64|amd64)  arch_name="x86_64" ;;
  *) fail "unsupported CPU: $arch" ;;
esac
ok "$os_name $arch_name"

command -v curl >/dev/null 2>&1 || fail "curl is required"
if command -v sha256sum >/dev/null 2>&1; then
  sha() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  fail "need sha256sum or shasum to verify the download"
fi

# -- download + verify -----------------------------------------------------------------------
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

version="$(curl -fsSL "$BASE/dist/VERSION")" || fail "cannot reach $BASE"
curl -fsSL "$BASE/dist/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "cannot download checksums"

bytes=0
wheels=""
while read -r want file; do
  [ -n "$file" ] || continue
  curl -fsSL "$BASE/dist/$file" -o "$tmp/$file" || fail "download failed: $file"
  got="$(sha "$tmp/$file")"
  [ "$got" = "$want" ] || fail "checksum mismatch for $file (expected $want, got $got)"
  bytes=$((bytes + $(wc -c < "$tmp/$file")))
  wheels="$wheels $file"
  case "$file" in pokbot_cli-*) cli_sum="$want" ;; esac
done < "$tmp/SHA256SUMS"

cli_whl="$(ls "$tmp"/pokbot_cli-*.whl 2>/dev/null | head -1)"
core_whl="$(ls "$tmp"/pokbot_core-*.whl 2>/dev/null | head -1)"
[ -n "$cli_whl" ] && [ -n "$core_whl" ] || fail "server is missing pokbot wheels"

size="$(awk "BEGIN { printf \"%.1f\", $bytes / 1048576 }")"
[ "$size" = "0.0" ] && size="$(awk "BEGIN { printf \"%.0f KB\", $bytes / 1024 }")" || size="$size MB"
ok "downloaded pokbot $version ($size)"
ok "checksum verified (sha256 $(printf '%s' "${cli_sum:-}" | cut -c1-12))"

# -- runtime ---------------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [ -x "$POKBOT_HOME/uv/uv" ]; then
  UV="$POKBOT_HOME/uv/uv"
else
  curl -fsSL https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR="$POKBOT_HOME/uv" INSTALLER_NO_MODIFY_PATH=1 sh -s -- --quiet >/dev/null 2>&1 \
    || fail "could not install the uv runtime"
  UV="$POKBOT_HOME/uv/uv"
fi
ok "runtime ready ($(tilde "$UV"))"

# -- install ---------------------------------------------------------------------------------
UV_TOOL_DIR="$POKBOT_HOME/tools" UV_TOOL_BIN_DIR="$BIN_DIR" \
  "$UV" tool install --force --quiet --python 3.12 "$cli_whl" --with "$core_whl" >"$tmp/uv.log" 2>&1 \
  || { cat "$tmp/uv.log" >&2; fail "install failed"; }

# Point the CLI at this server unless the user already logged in somewhere.
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pokbot"
if [ ! -f "$config_dir/credentials" ]; then
  mkdir -p "$config_dir"
  printf '{"server": "%s"}\n' "$BASE" > "$config_dir/credentials"
  chmod 600 "$config_dir/credentials"
fi
ok "installed to $(tilde "$BIN_DIR")/pokbot"

# `curl | sh` runs in a child shell and cannot change the caller's PATH, so prefer linking into a
# user bin dir that is already on PATH: then `pokbot` works in this terminal right away.
linked=""
for dir in "$HOME/.local/bin" "$HOME/bin"; do
  case ":$PATH:" in *":$dir:"*) ;; *) continue ;; esac
  [ -d "$dir" ] && [ -w "$dir" ] || continue
  target="$dir/pokbot"
  if [ -e "$target" ] && [ "$(readlink "$target" 2>/dev/null)" != "$BIN_DIR/pokbot" ]; then continue; fi
  ln -sf "$BIN_DIR/pokbot" "$target" && linked="$dir" && break
done

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    if [ -n "$linked" ]; then
      ok "linked $(tilde "$linked")/pokbot ${D}(already on your PATH)${N}"
    elif [ "${POKBOT_NO_MODIFY_PATH:-0}" != "1" ]; then
      case "${SHELL:-}" in
        */zsh)  rc="$HOME/.zshrc" ;;
        */bash) rc="$HOME/.bashrc" ;;
        *)      rc="$HOME/.profile" ;;
      esac
      line="export PATH=\"$BIN_DIR:\$PATH\""
      grep -qsF "$line" "$rc" || printf '\n# pokbot\n%s\n' "$line" >> "$rc"
      ok "added $(tilde "$BIN_DIR") to PATH in $(tilde "$rc")"
      need_path=1
    else
      need_path=2
    fi
    ;;
esac

case "${need_path:-0}" in
  1) printf ' \n%sthis terminal%s %s(new terminals already have it)%s\n' "$B" "$N" "$D" "$N" ;;
  2) printf ' \n%sadd to your PATH%s\n' "$B" "$N" ;;
esac
if [ "${need_path:-0}" != "0" ]; then printf '  export PATH="%s:$PATH"\n' "$BIN_DIR"; fi

printf ' \n%snext%s\n' "$B" "$N"
printf '  %-22s %s\n' "pokbot login" "sign in with GitHub"
printf '  %-22s %s\n' "pokbot init mybot" "scaffold a bot"
printf '  %-22s %s\n' "pokbot validate mybot" "check it offline"
printf '  %-22s %s\n' "pokbot play mybot" "enter the next tournament"
