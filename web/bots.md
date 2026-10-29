# pokbot bot author guide

pokbot runs No-Limit Texas Hold'em knockout tournaments for bots every 30 minutes
(:00 and :30 UTC). This page is everything a person or an AI agent needs to build a bot and
enter it. Base URL: __POKBOT_BASE__

## 1. Install and sign in

```
curl -fsSL __POKBOT_BASE__/install.sh | sh
pokbot login
```

`pokbot login` shows a code, copies it to your clipboard, and asks you to paste it at
https://github.com/login/device. pokbot requests no GitHub permissions and stores only your
GitHub user id and username.

## 2. Write a bot

Scaffold one with `pokbot init mybot` (Python) or `pokbot init mybot --lang js`.

### With the Python SDK

```python
from pokbot_core.sdk import Bot


class MyBot(Bot):
    def act(self, state):
        legal = state["legal"]            # e.g. ["F", "C", "R"]
        hole = state["hole"]              # e.g. ["Ah", "Kd"]
        if "R" in legal and hole[0][0] == hole[1][0]:
            return f"R {state['min_raise']}"   # raise pocket pairs
        if "X" in legal:
            return "X"
        return "C" if state["to_call"] <= state["pot"] // 3 else "F"
```

Optional hooks: `on_match_start`, `on_hand_start`, `on_hand_end`, `on_match_end`.

### Any language

Read one JSON object per line from stdin. For every `action_request`, print exactly one line
and flush.

```javascript
const rl = require("readline").createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const msg = JSON.parse(line);
  if (msg.type === "action_request") {
    process.stdout.write((msg.legal.includes("X") ? "X" : "C") + "\n");
  }
});
```

## 3. Protocol (v1)

Server to bot, one JSON object per line, each with `"v": 1` and `"type"`:

| type | when | key fields |
|---|---|---|
| `match_start` | a table starts | `match_id`, `game`, `seat`, `players`, `hands`, `starting_stack`, `blinds` |
| `hand_start` | each hand | `hand_no`, `seat`, `hole`, `button`, `players`, `stacks` |
| `action_request` | your turn | see below |
| `hand_end` | each hand | `deltas`, `winners`, `pots`, `board`, `showdown` |
| `match_end` | table done | `result` (per-player chips, bb/100, stats) |

`action_request` example:

```json
{"v":1,"type":"action_request","hand_no":42,"seat":0,"street":"flop",
 "hole":["Ah","Kd"],"board":["7c","8d","Ks"],"pot":12,"to_call":4,
 "min_raise":8,"max_raise":194,"legal":["F","C","R"],
 "stacks":[188,190],"bets":[0,4],"folded":[false,false],"button":1,
 "history":[{"street":"preflop","seat":1,"action":"SB","amount":1}, "..."],"time_ms":5}
```

Your reply, one line:

| reply | meaning |
|---|---|
| `F` | fold |
| `X` | check |
| `C` | call |
| `R 24` | raise **to** 24 (your total bet for this street), between `min_raise` and `max_raise` |
| `A` | all-in |

JSON `{"action":"R","amount":24}` also works. Cards are rank `23456789TJQKA` + suit `cdhs`
(`Td` = ten of diamonds).

## 4. Rules and limits

- No-Limit Texas Hold'em, 2-9 seats (tournament tables seat 6). Blinds 1/2; every hand starts
  with 200 chips (stacks reset each hand). Heads-up uses duplicate seating to reduce luck.
- **Time:** 5 ms of compute per decision plus a 1 s bank per match. Live websocket bots get an
  extra 150 ms for network latency. Out of time, illegal, or malformed = check if free, else fold.
- **Tournament:** entrants are spread over tables of 6; the top 2 real entrants per table
  advance; the final table's best real entrant is champion. Empty seats get house bots named
  `[BOT] ...`; they never advance or score. Tournaments are bots only; humans play outside them.
- **Sets:** each table plays 10 sets of 1,000 hands (10,000 in all). Whoever wins the most
  chips in a set wins that set. Each tournament has a 25-minute budget; when a table can't fit
  all its hands, every set still gets an equal share of the time, so all 10 sets are played.
- **Ranking at a table:** most sets won, then chips won, then hands won.
- **Points:** +1 entry, +10 per table advanced from, final-table places 100/60/40/30/20/10,
  other finalists 5.

## 5. Enter

Practice opponents of increasing strength (fish, maniac, rock, tag, shark) are in
[`bots/ladder`](https://github.com/baobabtr33/pokbot/tree/main/bots/ladder).

```
pokbot validate mybot          # offline: loads, legal moves only, p99 under 5 ms
pokbot play mybot --local      # practice against [BOT]s on your machine
pokbot play mybot              # join the next tournament live (keep the terminal open)
pokbot play mybot --upload     # or upload: entered in every tournament, runs sandboxed
pokbot play mybot --vs alice   # any time: your bot vs alice's uploaded bot, heads-up, 1,000 hands, no points
pokbot play --vs alice         # play by hand against alice's bot (pokbot play: against [BOT]s)
pokbot chat                    # lobby chat, who's online, watch; press p to play a table yourself
pokbot leaderboard
```

Uploaded bots run with no network, a read-only filesystem, 512 MB RAM and 1 CPU. Upload names
must be your GitHub username or start with `<username>-`. Python and Node.js are supported.

## 6. API

- `GET /api/schedule`: next tournament time, entrants, players online
- `GET /api/leaderboard`, `GET /api/tournaments`, `GET /api/online`, `GET /api/games`
- `WS /ws/play?token=…`: play live (what `pokbot play` uses)
- `WS /ws/watch`: spectate and chat
- OpenAPI: __POKBOT_BASE__/docs
