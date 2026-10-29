# CLAUDE.md — pokbot (Poker Bot Arena)

Guidance for Claude Code when working in this repository.

## What this project is

`pokbot` is a terminal-first poker tournament platform. Users write poker bots (or sit down
themselves) and enter **knockout tournaments that start every 30 minutes on the clock**
(:00 and :30). A bot enters either by **keeping a live websocket open** (`pokbot play mybot`)
or by **uploading its code** so the server plays every tournament for it
(`pokbot play mybot --upload`). The CLI shows the action live with dynamic terminal graphics
and a fireworks "WINNER" screen for the champion.

Games: the four classic variants are registered — Texas Hold'em, Omaha, Seven-Card Stud,
Five-Card Draw. **Only No-Limit Texas Hold'em is playable today**; the others show as
"coming soon" (`core/.../engine/variants.py`).

## Repository layout (uv workspace)

```
poker-bot-arena/
  pyproject.toml          # workspace root + dev deps (pytest, hypothesis, ruff, httpx)
  core/                   # pokbot-core: pure engine, protocol, players, match, tournament, SDK
    src/pokbot_core/
      engine/             # cards.py, evaluator.py, holdem.py (state machine), variants.py
      protocol.py         # message builders, parse_reply (letters or JSON)
      display.py          # A♥ / 10♦ pretty cards, action words
      players.py          # Player ABC, HouseBot ([BOT]), SubprocessBot, bot_command()
      match.py            # Match: N hands, timeouts, illegal-action handling, stats, events
      tournament.py       # Tournament: tables, top-2 advance, final table, next_start()
      bundle.py           # pokbot.toml, zip pack/unpack (size cap, no path traversal)
      sdk/                # `from pokbot_core.sdk import Bot`; runner.py loads user files
  cli/                    # pokbot-cli: the `pokbot` command
    src/pokbot_cli/
      main.py             # typer app: play, validate, init, login, logout, whoami, games, schedule, leaderboard
      tui.py              # textual live view, HumanPlayer, WinnerScreen (fireworks)
      plain.py            # line output for --plain / --json / non-TTY
      validate.py         # checks shared by `validate` and `play`
      online.py           # HTTP + websocket client
      config.py           # ~/.config/pokbot/credentials (XDG aware)
  server/                 # pokbot-server: FastAPI app
    src/pokbot_server/
      app.py              # REST, /ws/play, /ws/watch, /install.sh, static web/
      hub.py              # lobby, 30-minute scheduler, RemotePlayer, event fan-out, presence + chat
      chat.py             # ChatRoom history, clean(), per-connection RateLimiter
      github.py           # GitHub OAuth device flow (proxied)
      sandbox.py          # docker argv for uploaded bots
      store.py            # JSON-file store: users, bots, leaderboard, tournaments
      config.py           # POKBOT_* settings
  web/                    # intro site (index.html, style.css) + install.sh template
  scripts/build_dist.sh   # builds wheels + SHA256SUMS + VERSION into web/dist for the installer
  bots/examples/          # caller.py, pairs.py, raw_bot.py (no SDK), broken.py (fails validation)
  tests/
```

`core` must stay free of I/O frameworks (no FastAPI, textual, websockets). The engine is a
deterministic function of (seed, actions). `cli` and `server` both depend on `core`, never on
each other.

## Commands

```
uv sync                                         # install everything (editable)
uv run pytest -q                                # all tests (~4 s)
uv run ruff check . && uv run ruff format --check .
uv run pokbot-server --port 8000                # server; tournaments at :00/:30
POKBOT_INTERVAL=20 POKBOT_BOT_HANDS=1000 POKBOT_SANDBOX=unsafe-local uv run pokbot-server   # fast dev loop
uv run pokbot validate bots/examples/pairs.py
uv run pokbot play bots/examples/pairs.py --local            # live TUI vs [BOT]s
uv run pokbot play bots/examples/*.py --local --plain --hands 2000
uv run pokbot play --local                                   # play yourself
./scripts/build_dist.sh                         # rebuild installer wheels after changing core/cli
```

If `uv run pokbot` says `No module named pokbot_cli`, an editable install was created before
the package had files: `uv sync --reinstall-package pokbot-cli` (same for core/server).

## Install and login (user-facing)

```
curl -fsSL https://<server>/install.sh | sh
pokbot login
```

- The server renders `web/install.sh` at `/install.sh`, replacing `__POKBOT_BASE__` with
  `POKBOT_PUBLIC_URL` (or the request URL). The script checks OS/arch, downloads wheels from
  `/dist/`, verifies sha256 against `SHA256SUMS`, gets uv (into `~/.pokbot/uv` if missing),
  `uv tool install`s into `~/.pokbot/bin`, writes the server URL into the CLI config, and adds
  the bin dir to the shell rc (opt out: `POKBOT_NO_MODIFY_PATH=1`). Keep its output style:
  `◆ pokbot` header, one `✓` line per step, a `next` block.
- `pokbot login` uses the GitHub device flow, proxied by the server
  (`/api/auth/device`, `/api/auth/poll`): the CLI shows the code in a box, copies it to the
  clipboard, prints `› paste it at https://github.com/login/device`, polls, then stores a
  pokbot API key (`pk_...`). The CLI never sees a GitHub token; the server discards it after
  reading `/user`. Needs `POKBOT_GITHUB_CLIENT_ID` (OAuth app with device flow enabled).
- **GitHub is the only sign-in.** No OAuth scopes are requested; the server reads only `id`
  and `login` from GitHub's `/user`, discards the GitHub token, and stores
  `{name: login, github_id}` per API key. Never add email/avatar/profile fields.
- Signing in is required to play live (`/ws/play`), upload, and chat. The live seat name is
  always the account login; uploaded bot names must be `<login>` or `<login>-<suffix>`.
- Dev only: `POKBOT_DEV_LOGIN=1` enables name-only `/api/register` (`pokbot login --name`,
  hidden flag). Tests use it. It returns 403 otherwise.

## The `pokbot` CLI

```
pokbot play [FILE ...] [--local] [--upload] [--name] [--hands N] [--table-size N] [--seed N] [--plain] [--json]
pokbot validate FILE [--hands N] [--time-ms N] [--strict] [--json]
pokbot init NAME [--lang py|js]
pokbot chat | watch                      # lobby chat + who's online + spectate
pokbot login | logout | whoami | games | schedule | leaderboard
```

- `FILE` = `mybot.py`, `mybot.py:ClassName`, `./executable`, `bot.js`, or a folder with
  `pokbot.toml` (`name`, `entrypoint`, `runtime` = python | node).
- No FILE = you play as a human (TUI action bar).
- `play` validates every FILE first (200 hands) unless `--skip-validate`; it never queues a
  bot that `validate` rejects. Online play takes one FILE; `--local` takes many.
- Exit codes are a contract: `0` ok, `1` validation failed, `2` usage, `3` server/network.

## Tournament rules

- Starts every `POKBOT_INTERVAL` seconds aligned to the epoch (default 1800 = :00 and :30).
  Entrants = live websocket connections in the lobby + all uploaded bots. No entrants = skip.
- Entrants are spread evenly over the fewest tables of `POKBOT_TABLE_SIZE` (default 6, must
  be > 2). **Top 2 entrants per table advance**; repeat until the field fits one table; the
  final table's top entrant is the **single champion**.
- Empty seats are filled with **house bots named `[BOT] ...`** unless every seated player is
  human. House bots play every hand but **never advance** (so the field always shrinks and the
  winner is always a real entrant). Names starting with `[BOT]` are reserved.
- Ranking at a table: chips won, then hands won, then seat order.
- **Points** (`core/.../points.py`, real entrants only): +1 entry, +10 per table advanced
  from, final-table place 100/60/40/30/20/10, other finalists 5. Sent as `points` in
  `tournament_end`; the leaderboard sorts by points, then titles, then chips.
- Hands per table: 10,000 when only bots are seated (`POKBOT_BOT_HANDS`), 30 when a human is
  seated (`POKBOT_HUMAN_HANDS`). Stacks reset to 200 (100 bb) every hand; blinds 1/2.
- Heads-up matches use duplicate seating (second half replays the same decks with seats
  swapped). Every hand's deck comes from `hand_seed(master_seed, index)` so any match replays.

## Bot protocol v1

Server -> bot: one JSON object per line on stdin, each with `"v": 1` and `"type"`:
`match_start`, `hand_start` (your `hole`), `action_request`, `hand_end`, `match_end`.

```json
{"v":1,"type":"action_request","hand_no":42,"seat":0,"street":"flop","hole":["Ah","Kd"],
 "board":["7c","8d","Ks"],"pot":12,"to_call":4,"min_raise":8,"max_raise":194,
 "legal":["F","C","R"],"stacks":[...],"bets":[...],"button":1,"history":[...],"time_ms":1000}
```

Bot -> server: **one line per `action_request`, a single capital letter**:

| Reply  | Meaning                         |
|--------|---------------------------------|
| `F`    | fold                            |
| `X`    | check                           |
| `C`    | call                            |
| `R 24` | raise **to** 24 (street total)  |
| `A`    | all-in                          |

JSON `{"action":"R","amount":24}` is also accepted. Lowercase is tolerated.
- Illegal or malformed = check if free, else fold; counted as `illegal`.
- Time cap: **5 ms per decision** (`POKBOT_TIME_MS`) plus a 1 s time bank per match
  (`POKBOT_TIMEBANK_MS`); then check/fold, counted as a timeout. Live websocket bots get an
  extra `POKBOT_LIVE_NETWORK_MS` (150) for round-trip latency. Humans get 60 s.
- Each tournament has a wall-clock budget (`POKBOT_TOURNAMENT_BUDGET_S`, 1500 s) split evenly
  over the rounds left (`rounds_needed`); a table stops starting new hands at its deadline and
  ranks on what was played (`stopped_early` in the summary).
- Late replies to timed-out requests are discarded (subprocess: skip-count; websocket: `req` id
  echoed by the client).
- Cards on the wire are `Ah`, `Td`; people see `A♥`, `10♦` (red suits red) via `display.py`.
- History entries use the same letters plus `SB` / `BB` for blinds.
- Schema changes bump `v`; support `v` and `v-1` at the same time.

## Server API

- `POST /api/auth/device`, `POST /api/auth/poll` — GitHub device login.
- `POST /api/register` — dev only (see above).
- `POST /api/bots?name=X` (Bearer key, body = zip from `bundle.pack`) — upload/replace;
  `DELETE /api/bots/{name}`; `GET /api/bots`.
- `GET /api/schedule`, `/api/leaderboard`, `/api/tournaments`, `/api/games`, `/api/whoami`.
- `WS /ws/play?token=` — first message `{"type":"hello","name","kind":"bot"|"human","game"}`;
  server sends `lobby`, `info`, `event` (tournament/table events) and protocol messages;
  client replies `{"type":"action","reply":"C","req":<id>}`.
- `WS /ws/watch?token=` — spectators (token optional; needed to chat).
- `GET /api/online` — `{online, playing, spectators, names}`.

## Chat and presence

- One global lobby room on every websocket (`/ws/play` and `/ws/watch`). Client sends
  `{"type":"chat","text":...}`; everyone gets `{"type":"chat","name","text","system","at"}`.
  New connections get `chat_history` (last 50). Errors come back as `chat_error`.
- Only signed-in users (valid token) can talk; the name is the account name, never
  client-supplied. Text is stripped of control/bidi characters, max 280 chars, 5 messages per
  10 s per connection. The TUI escapes rich markup before rendering.
- The hub posts system messages for tournament start and the champion.
- Presence: `online` = distinct signed-in users + anonymous connections; `playing` = lobby +
  participants. Broadcast as `presence` on every connect/disconnect and tournament transition.
- TUI: `t` focuses the chat box, `esc` leaves it; the top bar shows `● N online · M playing`.
  Chat and presence are in-memory in the Hub, so they share its one-process limit.
- Server caps inbound websocket frames at 64 KB and pings every 20 s (`main.py`).
- Event fan-out (`hub.py`): other players' hole cards are nulled until showdown; bot-only
  tables are down-sampled to ~10 hands/s per table for display; tables with a human are sent
  in full. Players see their own table; everyone sees the final table.

## Sandbox (security-critical)

Uploaded code is untrusted. Default `POKBOT_SANDBOX=docker`: `--network=none --read-only`,
tmpfs `/tmp`, 512 MB, 1 CPU, 64 pids, `--cap-drop=ALL`, `no-new-privileges`, uid 65534, bot
mounted read-only, `core/src` mounted read-only for the SDK. Uploads return 503 when no sandbox
is available. `POKBOT_SANDBOX=unsafe-local` runs uploads as plain subprocesses — dev only,
never on a shared server. Never pass server secrets into bot environments. Bundles: 20 MB max,
zip-slip checked in `bundle.unpack`.

## Live terminal view (textual, `cli/tui.py`)

- Table panel: board, pot, street, seats with dealer `D`, stack, bet, cards, last action
  letter, hand delta. Hero highlighted gold. Humans never see opponents' hole cards before
  showdown.
- Side: standings (chips, bb/100, VPIP, PFR), hero chip sparkline, progress bar.
- Log: actions spelled out (`C (Call 2)`) at human tables; hand summaries every 1,000 hands at
  bot tables.
- Action bar for humans: keys `F X C A`, `R` opens an amount box prefilled with the min raise.
- Lobby countdown to the next :00/:30. `WinnerScreen`: ASCII WINNER banner + fireworks.
- Renders at most every 100 ms; human tables are paced (0.45 s per action, 1.6 s after a hand).
- Keys: `q` quit, `space` pause, `w` switch table. `--plain`/non-TTY uses `plain.py`.

## Engine correctness

Tests cover: evaluator ordering incl. wheel and 7-card best-five, heads-up blind/acting order,
min-raise, short all-in not reopening betting, coercion of illegal actions, side pots,
hypothesis chip conservation for 2-9 seats, determinism, duplicate zero-sum, tournament always
yields one winner for 1..40 entrants, all-human tables get no [BOT]s, scheduler alignment,
validate pass/fail, server register/upload/auth/install.sh. Do not change expected values in
an engine test without checking the actual poker rule.

## Conventions

- Chips are ints. Cards are ints 0..51 inside the engine (`rank = c // 4`, `suit = c % 4`),
  two-char strings on the wire.
- Server settings come from `POKBOT_*` env vars in `server/config.py`; no magic numbers in handlers.
- New features need a test. Protocol changes need a test that an old-`v` bot still plays.
- Persistence is a JSON file (`data/state.json`); move to SQLite before running more than one
  server process.

## Deployment

Planned: Vercel for the site, `install.sh` and wheels; one AWS Lightsail instance ($12 plan)
for `pokbot-server` and the Docker/gVisor sandbox, ~$15/month total, paid from AWS free credits
(Lightsail 3-month trial + new-account credits cover about the first year). See `docs/DEPLOY.md`
(not implemented yet).

## Not done yet

- Omaha, Seven-Card Stud, Five-Card Draw engines.
- Reconnect-within-30 s for dropped live players (today: every decision after a drop folds).
- Ratings beyond titles/chips; replay command; persistent hand logs (JSONL).
- Building the docker images ahead of time and a real sandbox test in CI.
