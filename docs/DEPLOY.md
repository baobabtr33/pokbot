# Deploying pokbot: Vercel + AWS

Status: plan, not yet implemented. Domains below use `pokbot.dev` as a placeholder.

## Topology

| Host | Serves | Why |
|---|---|---|
| **Vercel** `pokbot.dev` | `web/` intro page, `install.sh`, `/dist/*` wheels; rewrites `/api/*` to AWS | Static + CDN; free/cheap; preview deploys |
| **AWS EC2** `api.pokbot.dev` | `pokbot-server` (REST, `/ws/play`, `/ws/watch`, scheduler, chat), Docker + gVisor sandbox for uploaded bots | Needs long-lived websockets, an in-memory 30-minute scheduler, and Docker for untrusted code |

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
3. **Tournament limits** (`core/src/pokbot_core/tournament.py`, `server/.../config.py`):
   `POKBOT_MAX_CONTAINERS` semaphore around `_play_table` (today every table starts at once);
   bot-table `time_ms` ≈ 50 ms; `POKBOT_MATCH_DEADLINE_S` ≈ 1500 to finish a table on current
   standings so one slow bot cannot overrun the 30-minute slot.
4. **Sandbox** (`server/src/pokbot_server/sandbox.py`): `--runtime=runsc` (gVisor) via
   `POKBOT_DOCKER_RUNTIME`; pinned `pokbot-py` / `pokbot-node` images with `pokbot_core` baked
   in (drop the `core/src` mount); `--name pokbot-<tid>-<bot>` and
   `--label pokbot.tournament=<tid>`; `docker rm -f` on close; orphan reaper at startup and on
   a 5-minute systemd timer.
5. **Abuse limits**: require a token for bot `/ws/play`; max 3 `/ws/play` and 5 `/ws/watch`
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

## AWS

- **Compute:** one `c7g.xlarge` (Graviton, 4 vCPU / 8 GB, Ubuntu 24.04 arm64). A Graviton
  vCPU is a full core, so `--cpus=1` per bot is real compute. gVisor supports arm64 (`systrap`
  platform). Base images and wheels are multi-arch / pure Python. Start with
  `POKBOT_MAX_CONTAINERS=3`; scale up in place to `2xlarge`/`4xlarge`.
- **Server on the host, not in a container:** systemd unit runs `pokbot-server` as user
  `pokbot` (in the `docker` group; root-equivalent, so treat the server as trusted).
  `sandbox.docker_argv` bind-mounts host paths, which breaks inside a container.
- **Network:** Elastic IP; security group inbound 80/443 only, no SSH; IMDSv2 required with hop
  limit 1.
- **TLS:** Caddy on the instance (automatic ACME, websockets with no idle cutoff, per-IP rate
  limiting), proxying to `127.0.0.1:8000`. No ALB: it adds ~$18/month and its 60 s idle
  timeout would need raising for tournament websockets.
- **Access:** SSM Session Manager only. Instance role: `AmazonSSMManagedInstanceCore`,
  `ssm:GetParametersByPath` on `/pokbot/prod/*`, `s3:PutObject` on the backup bucket,
  CloudWatch agent.
- **Secrets:** SSM Parameter Store SecureStrings under `/pokbot/prod/`, rendered by
  `ExecStartPre` to `/run/pokbot/env` (tmpfs, 0600) and loaded with `EnvironmentFile=`. Never
  passed into bot containers.
- **Storage:** 30 GB gp3 root + 20 GB gp3 data volume (`POKBOT_DATA`, Docker root). DLM daily
  snapshots (keep 14). Hourly `aws s3 sync` of `data/` to a versioned bucket with a 30-day
  lifecycle.
- **Observability:** CloudWatch agent ships journald + Caddy logs; the server emits EMF metrics
  `TournamentDuration`, `TournamentCrash`, `ActiveContainers`, `WsConnections`. SNS email
  alarms: duration > 1680 s, any crash, no tournament for 65 minutes while entrants > 0,
  disk > 80%, CPU at 100% for 15 minutes. EC2 auto-recover on `StatusCheckFailed_System`.
  External uptime checks on `https://pokbot.dev/install.sh` and
  `https://api.pokbot.dev/api/schedule`.
- **Not used:** Fargate (no Docker/gVisor inside tasks), ECS on EC2 (server would need the
  Docker socket inside a container), Lambda (15-minute cap, no long websockets), API Gateway
  WebSockets (would mean rewriting the Hub).

## Deploy (GitHub Actions, on a `v*` tag)

1. `uv sync --frozen`, `ruff check`, `ruff format --check`, `pytest -q`. A sandbox job on an
   `ubuntu-24.04-arm` runner builds the arm64 bot images and runs a real gVisor sandbox test.
2. Upload the release tarball (repo + `uv.lock`) to S3, using GitHub OIDC to assume a deploy
   role (no long-lived AWS keys).
3. `aws ssm send-command` runs `/opt/pokbot/deploy.sh` on the instance:
   1. Wait until `/api/schedule` reports no running tournament (up to 30 minutes), and abort
      if the next start is under 5 minutes away.
   2. Fetch the tarball, `docker pull` pinned images, `uv sync --frozen`,
      `systemctl restart pokbot`.
   3. Health-check; roll back to the previous release symlink on failure.
4. Push the tag commit to `release`; Vercel builds and deploys the site. The server goes first,
   so new wheels never meet an older server. PRs get Vercel preview deploys.

## Rollout

**MVP, in order:**
1. URL split (`install.sh`, `index.html`, both `config.py` files, `app.py`, `build_site.sh`,
   `vercel.json`).
2. `main.py` proxy settings.
3. Container semaphore, bot time limit, match deadline.
4. Container labels and orphan reaper.
5. Rate limits and Origin checks.
6. AWS (Terraform or a runbook): EC2, EIP, security group, role, SSM parameters, DLM, S3,
   CloudWatch agent and alarms; install Docker, gVisor, Caddy.
7. Register the GitHub OAuth app (device flow on) and set `POKBOT_GITHUB_CLIENT_ID`.
8. Vercel project and DNS.
9. Tag workflow.
10. Soft launch with `POKBOT_BOT_HANDS=2000`; raise to 10,000 once p95 tournament time is
    under 15 minutes.

**Later:** SQLite in place of `store.py`; separate runner hosts (Auto Scaling group of
`c7g`) behind a `RunnerClient` in `sandbox.make_player`; Redis pub/sub for `Hub.on_event`,
lobby and chat fan-out, with a Redis `SET NX` leader for the scheduler, plus RDS Postgres;
Firecracker if gVisor overhead hurts; reconnect handling; hand logs in S3.

## Monthly cost (MVP, us-east-1, approximate)

| Item | Cost |
|---|---|
| `c7g.xlarge` on-demand | ~$106 (~$67 with a 1-year Compute Savings Plan) |
| EBS 50 GB gp3 + snapshots | ~$5 |
| Elastic IP (public IPv4) | ~$3.65 |
| Data transfer | ~$0 (first 100 GB out free; wheels are on Vercel) |
| CloudWatch logs + alarms | ~$3–5 |
| S3 backups | < $1 |
| Vercel | $0 Hobby (non-commercial) or $20/member Pro |
| **Total** | **~$120 on-demand, ~$80 with a Savings Plan, +$20 for Vercel Pro** |
