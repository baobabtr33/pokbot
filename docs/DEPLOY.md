# Deploying pokbot: Vercel + AWS

Status: plan, not yet implemented. Domains below use `pokbot.dev` as a placeholder.

## Topology

| Host | Serves | Why |
|---|---|---|
| **Vercel** `pokbot.dev` | `web/` intro page, `install.sh`, `/dist/*` wheels; rewrites `/api/*` to AWS | Static + CDN; free/cheap; preview deploys |
| **AWS Lightsail** `api.pokbot.dev` | `pokbot-server` (REST, `/ws/play`, `/ws/watch`, scheduler, chat), Docker + gVisor sandbox for uploaded bots | Needs long-lived websockets, an in-memory 30-minute scheduler, and Docker for untrusted code |

Vercel Functions cannot run the game server: they are short-lived, cannot hold websockets
for a tournament, and cannot launch containers. Vercel rewrites do not proxy websockets, so
the CLI and the web page connect to `wss://api.pokbot.dev/ws/*` directly.

**URL rule:** `install.sh` writes `https://api.pokbot.dev` into the CLI config; the CLI uses
that one URL for all HTTP and websocket traffic. Downloads come from `https://pokbot.dev/dist`.

## Code changes before launch

1. **URL split**
   - `web/install.sh`: add `SERVER="${POKBOT_SERVER:-__POKBOT_SERVER__}"`; keep `BASE` for
     `$BASE/dist/*`; write `{"server": "$SERVER"}` into the CLI config.
   - `cli/src/pokbot_cli/config.py`: `DEFAULT_SERVER = "https://api.pokbot.dev"`.
   - `web/index.html`: keep relative `/api/...` fetches (Vercel rewrite); add
     `<meta name="pokbot-api" content="__POKBOT_SERVER__">` for any direct `wss://` use,
     falling back to `location.origin` in local dev.
   - `server/src/pokbot_server/config.py`: add `POKBOT_SITE_URL`; prod sets
     `POKBOT_PUBLIC_URL=https://api.pokbot.dev`.
   - `server/src/pokbot_server/app.py`: `/install.sh` substitutes both placeholders (self-hosting
     keeps working); mount `web/` only when present; Origin allowlist on websockets
     (`https://pokbot.dev` or no Origin for the CLI); `Cache-Control: public, s-maxage=10` on
     schedule/leaderboard/online.
   - New `scripts/build_site.sh`: install uv if missing, run `build_dist.sh`, copy `web/` to
     `public/`, substitute `__POKBOT_BASE__` and `__POKBOT_SERVER__`, fail if any `__POKBOT_`
     remains. Add `public/` to `.gitignore`.
2. **`server/src/pokbot_server/main.py`**: `proxy_headers=True, forwarded_allow_ips="127.0.0.1"`;
   keep `ws_ping_interval=20`; exactly one worker (the Hub is in memory).
3. **Tournament limits**: done: 5 ms per-decision cap (`POKBOT_TIME_MS`), 150 ms network
   allowance for live bots (`POKBOT_LIVE_NETWORK_MS`), 1 s time bank, and a 25-minute
   tournament budget split across rounds (`POKBOT_TOURNAMENT_BUDGET_S`). Still to do: a
   `POKBOT_MAX_CONTAINERS` semaphore around `_play_table` (today every table starts at once).
4. **Sandbox** (`server/src/pokbot_server/sandbox.py`): `--runtime=runsc` (gVisor) via
   `POKBOT_DOCKER_RUNTIME`; pinned `pokbot-py` / `pokbot-node` images with `pokbot_core` baked
   in (drop the `core/src` mount); `--name pokbot-<tid>-<bot>` and
   `--label pokbot.tournament=<tid>`; `docker rm -f` on close; orphan reaper at startup and on
   a 5-minute systemd timer.
5. **Abuse limits**: done: GitHub sign-in required for `/ws/play` and chat. To do: max 3 `/ws/play` and 5 `/ws/watch`
   per IP/token; 5 uploads/hour and 3 bots per user; chat already rate-limited (5 per 10 s).
   Client IPs forwarded by Vercel (`x-forwarded-for`) are spoofable, so token-based limits are
   the real ones.

## Vercel

`vercel.json` at the repo root:

```json
{
  "buildCommand": "sh scripts/build_site.sh",
  "outputDirectory": "public",
  "git": { "deploymentEnabled": { "main": false } },
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://api.pokbot.dev/api/:path*" }
  ],
  "headers": [
    { "source": "/install.sh", "headers": [
      { "key": "Content-Type", "value": "text/x-shellscript; charset=utf-8" },
      { "key": "Cache-Control", "value": "no-cache" } ] },
    { "source": "/dist/(VERSION|SHA256SUMS)", "headers": [
      { "key": "Cache-Control", "value": "no-cache" } ] },
    { "source": "/dist/(.*)\\.whl", "headers": [
      { "key": "Cache-Control", "value": "public, max-age=31536000, immutable" } ] }
  ]
}
```

Project settings: production branch `release`; env `SITE=https://pokbot.dev`,
`API=https://api.pokbot.dev`. DNS for `api` is an A record to the EC2 Elastic IP, managed in
Vercel DNS. Hobby is non-commercial only; use Pro ($20/member) for anything commercial.

## AWS: Lightsail, not EC2

Bots get a 5 ms compute cap per decision and each tournament has a 25-minute budget, so the
box sits idle most of the half hour and only needs short bursts. That fits a small burstable
instance; no dedicated cores needed.

- **Compute:** one AWS Lightsail Linux instance, **$12/month plan** (2 GB RAM, 2 vCPU burstable,
  60 GB SSD, 3 TB transfer, public IPv4 included), Ubuntu 24.04. Move to the $24 plan (4 GB)
  when concurrent containers or entrants grow. Lightsail bills a flat price: when burst credits
  run out the CPU is throttled rather than billed, so there are no surprise charges.
  Start with `POKBOT_MAX_CONTAINERS=2`.
- **Server on the host, not in a container:** systemd runs `pokbot-server` as user `pokbot`
  (in the `docker` group; root-equivalent, so treat the server as trusted).
  `sandbox.docker_argv` bind-mounts host paths, which breaks inside a container.
- **Sandbox:** Docker + gVisor (`runsc`) on the instance.
- **Network:** Lightsail static IP (free while attached). Lightsail firewall: 80/443 open; 22
  only from your own IP (or closed, using the Lightsail browser SSH).
- **TLS:** Caddy on the instance (automatic ACME, websockets with no idle cutoff, per-IP rate
  limits), proxying to `127.0.0.1:8000`. No load balancer.
- **Secrets:** `/etc/pokbot/env` (0600) loaded with `EnvironmentFile=`. Never passed into bot
  containers. Only `POKBOT_GITHUB_CLIENT_ID` is needed (device flow has no client secret).
- **Backups:** Lightsail automatic daily snapshots (~$3/month for 60 GB) plus an hourly copy
  of `data/` to an S3 bucket (cents).
- **Monitoring:** Lightsail's built-in CPU/network metric alarms (free) and an external uptime
  check on `https://pokbot.dev/install.sh` and `https://api.pokbot.dev/api/schedule`. Alert on
  a tournament longer than 28 minutes from the server log.
- **Not used:** EC2 `c7g.xlarge` (~$106/month; sized for unbounded compute that the 5 ms cap
  removes), Fargate (no Docker/gVisor inside tasks), Lambda (15-minute cap, no long
  websockets), ALB (~$18/month for one box).
- **Even cheaper:** the $7 plan (1 GB) works for a soft launch with `POKBOT_MAX_CONTAINERS=1`.
  Outside AWS, a Hetzner CAX11 (~€4/month) does the same job.

## Deploy (pull-based; no inbound SSH from CI)

1. GitHub Actions on a `v*` tag: `uv sync --frozen`, `ruff check`, `ruff format --check`,
   `pytest -q`, and a sandbox job that builds the bot images and runs a real gVisor test.
   It publishes the release tarball (repo + `uv.lock`) as a GitHub release asset.
2. On the instance, a systemd timer (`pokbot-deploy.timer`, every 2 minutes) runs
   `/opt/pokbot/deploy.sh`. It checks for a newer release, and only when `/api/schedule` reports
   no running tournament and the next start is more than 5 minutes away it fetches the tarball,
   verifies its checksum, `docker pull`s pinned images, runs `uv sync --frozen`, and
   `systemctl restart pokbot`. It health-checks and rolls back to the previous release symlink
   on failure.
3. Pushing the tag commit to `release` makes Vercel build and deploy the site. Because the
   server updates within minutes and the site deploys from the same tag, wheels and server
   protocol stay in step. PRs get Vercel preview deploys.

## Rollout

**MVP, in order:**
1. URL split (`install.sh`, `index.html`, both `config.py` files, `app.py`, `build_site.sh`,
   `vercel.json`).
2. `main.py` proxy settings.
3. Container semaphore, bot time limit, match deadline.
4. Container labels and orphan reaper.
5. Rate limits and Origin checks.
6. Lightsail instance, static IP, firewall, snapshots, S3 bucket; install Docker, gVisor,
   Caddy and the deploy timer.
7. Register the GitHub OAuth app (device flow on) and set `POKBOT_GITHUB_CLIENT_ID`.
8. Vercel project and DNS.
9. Tag workflow.
10. Soft launch with `POKBOT_BOT_HANDS=2000`; raise to 10,000 once p95 tournament time is
    under 15 minutes.

**Later:** SQLite in place of `store.py`; separate runner instances behind a `RunnerClient` in `sandbox.make_player`; Redis pub/sub for `Hub.on_event`,
lobby and chat fan-out, with a Redis `SET NX` leader for the scheduler, plus Postgres;
Firecracker if gVisor overhead hurts; reconnect handling; hand logs in S3.

## Monthly cost (MVP)

| Item | Cost |
|---|---|
| Lightsail $12 plan (2 GB, 2 vCPU burst, 60 GB SSD, 3 TB transfer, IPv4) | $12 |
| Lightsail automatic snapshots | ~$3 |
| S3 backups of `data/` | < $1 |
| Monitoring (Lightsail alarms + free uptime checker) | $0 |
| Vercel Hobby (non-commercial) | $0 |
| **Total** | **~$15/month** (~$10 on the $7 plan; +$20 if Vercel Pro is needed for commercial use) |
