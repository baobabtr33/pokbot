#!/bin/sh
# End-to-end test that does what a new user does, against a throwaway local server:
# install with curl | sh, sign in, scaffold a bot, validate it, submit it, challenge another
# player's bot, then wait for a real tournament and check the leaderboard.
#
#   scripts/test.sh            # ~1 minute; leaves nothing behind
#   KEEP=1 scripts/test.sh     # keep the temp dir (server log, fake home) for debugging
#
# Uses dev settings (POKBOT_DEV_LOGIN=1 name-only login, POKBOT_SANDBOX=unsafe-local, short
# tournaments every 20 s); nothing touches the real server or your ~/.config.
set -eu
cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"
BASE="http://127.0.0.1:$PORT"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pokbot-e2e.XXXXXX")"
G="$(printf '\033[32m')"; R="$(printf '\033[31m')"; D="$(printf '\033[2m')"; N="$(printf '\033[0m')"
SERVER_PID=""

cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  if [ "${KEEP:-0}" = 1 ]; then echo "${D}kept $TMP${N}"; else rm -rf "$TMP"; fi
}
trap cleanup EXIT INT TERM

step() { printf '%s▸ %s%s\n' "$D" "$1" "$N"; }
ok() { printf '%s✓%s %s\n' "$G" "$N" "$1"; }
fail() { printf '%s✗ %s%s\n' "$R" "$1" "$N" >&2; [ -f "$TMP/server.log" ] && tail -20 "$TMP/server.log" >&2; exit 1; }
expect() { # expect "<what>" <needle> <haystack>
  case "$3" in *"$2"*) ok "$1" ;; *) printf '%s\n' "$3" >&2; fail "$1: expected '$2'" ;; esac
}

# -- server ----------------------------------------------------------------------------------
step "building installer wheels"
./scripts/build_dist.sh >/dev/null
step "starting a local server on :$PORT"
POKBOT_DEV_LOGIN=1 POKBOT_SANDBOX=unsafe-local POKBOT_INTERVAL=20 POKBOT_BOT_HANDS=500 \
POKBOT_TOURNAMENT_BUDGET_S=15 POKBOT_DATA="$TMP/data" POKBOT_PUBLIC_URL="$BASE" \
  uv run pokbot-server --port "$PORT" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do curl -fs "$BASE/api/schedule" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fs "$BASE/api/schedule" >/dev/null || fail "server did not start"
ok "server up"

# -- a brand-new user ------------------------------------------------------------------------
export HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/home/.config"
mkdir -p "$HOME"
PATH="/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$(command -v uv)")"
export PATH

step "curl -fsSL $BASE/install.sh | sh"
out="$(curl -fsSL "$BASE/install.sh" | POKBOT_NO_MODIFY_PATH=1 sh 2>&1)" || { echo "$out"; fail "install"; }
expect "installer finished" "installed to" "$out"
POKBOT="$HOME/.pokbot/bin/pokbot"
expect "pokbot --version" "pokbot " "$("$POKBOT" --version)"

cd "$TMP"
step "pokbot login"
expect "signed in" "tester" "$("$POKBOT" login --name tester 2>&1)"
expect "pokbot whoami" "tester" "$("$POKBOT" whoami 2>&1)"

step "pokbot init mybot"
expect "scaffolded" "created" "$("$POKBOT" init mybot 2>&1)"
step "pokbot validate mybot"
expect "bot is valid" "valid" "$("$POKBOT" validate mybot 2>&1)"
step "pokbot submit mybot"
expect "bot submitted" "uploaded" "$("$POKBOT" submit mybot 2>&1)"
expect "pokbot schedule shows it" "1" "$(curl -fs "$BASE/api/schedule" | sed -E 's/.*"entrants":([0-9]+).*/\1/')"

step "invalid bot is refused"
mkdir -p broken && cp "$OLDPWD/bots/examples/broken.py" broken/bot.py
set +e; "$POKBOT" submit broken/bot.py >/dev/null 2>&1; code=$?; set -e
[ "$code" = 1 ] && ok "pokbot submit exits 1 for a bad bot" || fail "bad bot exit code $code (want 1)"

# -- a second player, then a challenge -------------------------------------------------------
step "a rival submits the level-4 ladder bot"
XDG_CONFIG_HOME="$TMP/rival" "$POKBOT" login --name rival --server "$BASE" >/dev/null 2>&1 || fail "rival login"
expect "rival submitted" "uploaded" "$(XDG_CONFIG_HOME="$TMP/rival" "$POKBOT" submit "$OLDPWD/bots/ladder/l4_tag.py" --name tag 2>&1)"

step "pokbot play mybot --vs rival (heads-up on the server)"
out="$("$POKBOT" play mybot --vs rival --plain 2>&1)" || { echo "$out"; fail "versus game"; }
expect "heads-up game finished" "Heads-up" "$out"
expect "no leaderboard points for games" "no leaderboard points" "$out"

# -- a real tournament -----------------------------------------------------------------------
step "waiting for the next tournament (every 20 s here)"
for _ in $(seq 1 90); do
  board="$(curl -fs "$BASE/api/leaderboard")"
  case "$board" in *tester-mybot*) break ;; esac
  sleep 1
done
expect "tournament ran: tester-mybot on the leaderboard" "tester-mybot" "$board"
expect "points awarded" '"points"' "$board"
expect "pokbot leaderboard" "tester-mybot" "$("$POKBOT" leaderboard 2>&1)"
grep -q "Traceback" "$TMP/server.log" && fail "server logged a traceback" || ok "no server errors"

printf '\n%sall user flows passed%s\n' "$G" "$N"
