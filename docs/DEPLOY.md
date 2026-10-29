# Deploying pokbot on AWS

Everything runs on **one AWS Lightsail instance**: the intro site, `install.sh`, the installer
wheels, the REST API, websockets, the 30-minute scheduler, chat, and the Docker + gVisor
sandbox for uploaded bots. Caddy terminates TLS in front of it. No Vercel, no load balancer.

```
users ──https/wss──▶ Caddy :443 ──▶ pokbot-server 127.0.0.1:8000 ──▶ docker --runtime=runsc (bots)
                     (Lightsail $12: 2 GB, 2 vCPU burst, 60 GB SSD, static IPv4)
```

Why this shape:
- The server keeps the lobby, scheduler and chat in memory and holds websockets open for a
  whole tournament, so it needs a long-running VM (not Lambda/Fargate/Vercel functions).
- Uploaded bots are untrusted and run in Docker containers started by the server, which needs
  a real Docker daemon on the host. The server runs on the host (not in a container) because
  `sandbox.docker_argv` bind-mounts host paths.
- Bots get 5 ms per decision and tournaments a 25-minute budget, so the box is idle most of the
  half hour. A small burstable instance is enough; Lightsail throttles instead of billing when
  burst credits run out, so there are no surprise charges.

## Current deployment

| | |
|---|---|
| Site | https://baobabtr33.github.io/pokbot (GitHub Pages) |
| Game server | https://100-58-68-17.sslip.io (API, websockets, installer) |
| Instance | Lightsail `pokbot`, us-east-1a, `small_3_0` ($12), Ubuntu 24.04 |
| Static IP | `pokbot-ip` = 100.58.68.17 |
| SSH key | Lightsail key pair `pokbot-deploy` → `~/.ssh/pokbot-lightsail.pem` |
| IAM | inline policy `pokbot-deploy` on the deploying IAM user |
| Budget | `pokbot-real-spend`: email when non-credit spend > $1/month |
| GitHub login | not configured yet (`POKBOT_GITHUB_CLIENT_ID` unset) |

Redeploy: `POKBOT_HOST=100-58-68-17.sslip.io POKBOT_SITE_URL=https://baobabtr33.github.io/pokbot scripts/aws/deploy.sh`
(the site URL is stored in `/etc/pokbot/env`, so later deploys can leave it out)

## Files

| Path | What it does |
|---|---|
| `scripts/aws/iam-policy.json` | Permissions the deploying IAM user needs (Lightsail + Budgets) |
| `scripts/aws/provision.sh` | Creates key pair, instance (with `deploy/bootstrap.sh` as user-data), static IP, firewall, optional $1 budget alert. Idempotent. Prints the public host. |
| `deploy/bootstrap.sh` | First boot: Docker, gVisor (`runsc`), Caddy, uv, `pokbot` user, base images, swap |
| `scripts/aws/deploy.sh` | Ships committed `HEAD` + freshly built wheels; runs `remote_install.sh` over SSH |
| `deploy/remote_install.sh` | Unpack release, `uv sync --frozen`, write `/etc/pokbot/env`, install units, switch `current`, restart, health-check, roll back on failure. Refuses while a tournament is running. Keeps 5 releases. |
| `deploy/pokbot.service` | systemd unit: one `pokbot-server` process as user `pokbot` |
| `deploy/Caddyfile` | TLS + reverse proxy (websockets, 21 MB uploads) |
| `deploy/pokbot-reaper.{service,timer}` | Every 5 min, removes bot containers running longer than a slot |

## First deploy

1. **IAM permissions** for the deploying user (once):
   ```
   aws iam put-user-policy --user-name <you> --policy-name pokbot-deploy \
     --policy-document file://scripts/aws/iam-policy.json
   ```
2. **GitHub OAuth app** (once): github.com → Settings → Developer settings → OAuth Apps →
   New. Any homepage/callback URL; tick **Enable Device Flow**. Copy the **Client ID** (there
   is no secret to copy; the device flow does not use one).
3. **Provision** (~2 min, then ~5 min of first-boot setup on the instance):
   ```
   AWS_REGION=us-east-1 BUNDLE=small_3_0 POKBOT_ALERT_EMAIL=you@example.com scripts/aws/provision.sh
   ```
   It prints a host like `3-91-20-7.sslip.io` (sslip.io resolves it to the static IP, so Caddy
   can get a Let's Encrypt certificate without a domain).
4. **Deploy:**
   ```
   POKBOT_HOST=3-91-20-7.sslip.io POKBOT_GITHUB_CLIENT_ID=<client id> scripts/aws/deploy.sh
   ```
5. **Check:** `curl -fsSL https://<host>/install.sh | sh`, then `pokbot login`,
   `pokbot schedule`, `pokbot chat`.

Later deploys: commit, then `POKBOT_HOST=<host> scripts/aws/deploy.sh`. If a tournament is
running it exits with code 75; run it again after the tournament.

## Static site on GitHub Pages

The site pages (`index.html`, `llms.txt`, `bots.md`, `og.png`, sitemap) are also published to
GitHub Pages by `.github/workflows/pages.yml` on every push to `main` that touches `web/`.
`scripts/build_pages.py` fills `__POKBOT_SITE__` with the Pages URL and `__POKBOT_BASE__` with
the game server (`POKBOT_API` in the workflow). The installer, wheels, API and websockets stay
on the server.

Deploy the server with `POKBOT_SITE_URL=<pages url>` once Pages is live: the server's `/`,
`/llms.txt`, `/bots.md` and `/sitemap.xml` then 301 to Pages (one canonical copy for search
engines), and `/api` allows cross-origin GETs from the Pages origin so the homepage can show
the schedule, online count and leaderboard. `POKBOT_SITE_URL=none` undoes it.

Setup (once): repo Settings, Pages, Source: GitHub Actions.

## Own domain

Point an A record (e.g. `pokbot.example`) at the static IP, then deploy with
`POKBOT_HOST=pokbot.example`. Caddy gets the certificate; `/etc/pokbot/env` is updated with the
new public URL, and new installs write it into the CLI config. Existing CLIs keep the old host
until users run the installer again or edit `~/.config/pokbot/credentials`.

## On the instance

- Config: `/etc/pokbot/env` (`POKBOT_PUBLIC_URL`, `POKBOT_DATA=/var/lib/pokbot/data`,
  `POKBOT_SANDBOX=docker`, `POKBOT_DOCKER_RUNTIME=runsc`, `POKBOT_MAX_PARALLEL_TABLES=1`,
  `POKBOT_GITHUB_CLIENT_ID`). Edit, then `sudo systemctl restart pokbot`.
- Logs: `journalctl -u pokbot -f`, `journalctl -u caddy`. First boot:
  `/var/log/cloud-init-output.log`.
- Data: `/var/lib/pokbot/data` (`state.json`, uploaded bots). Back it up (below).
- SSH: `ssh -i ~/.ssh/pokbot-lightsail.pem ubuntu@<ip>`. Port 22 is open only to the IP that
  ran `provision.sh`; re-run it to update after your IP changes.

## Backups and alerts

- Lightsail **automatic snapshots** (instance → Snapshots → enable daily; ~$3/month for 60 GB).
- `provision.sh` with `POKBOT_ALERT_EMAIL` creates an AWS Budgets alert that emails when real
  (non-credit) spend passes $1 in a month. Confirm the subscription email AWS sends.
- Lightsail metric alarms (free): CPU and status-check alarms from the instance's Metrics tab.

## Cost, paid from AWS free credits

Checked October 2026; confirm in your account (Billing → Credits; Lightsail pricing page).

- New AWS accounts (created on or after 15 July 2025) get $100 in credits at sign-up and up to
  $100 more for onboarding activities; credits expire 12 months after account creation. Pick
  the **Paid plan** (or upgrade before month 6): the Free plan closes the account after
  6 months.
- Lightsail gives **3 months free** on the $5, $7 and $12 Linux bundles (one instance).

| Months | Lightsail $12 | Snapshots | Paid from |
|---|---|---|---|
| 1–3 | $0 (trial) | ~$3/month | credits |
| 4–12 | $12/month | ~$3/month | credits (~$150 for the year) |
| 13+ | $12/month | ~$3/month | card: **~$15/month** |

To stretch credits further: `BUNDLE=micro_3_0` ($7, 1 GB) for a soft launch. Startups can apply
to AWS Activate for more credits.

## Before going big

- Rate limits per IP on `/api` and websocket connection counts (Caddy `rate_limit` plugin and
  per-IP caps in `app.py`).
- Pre-built bot images with `pokbot_core` baked in (replace the `core/src` mount).
- SQLite instead of `data/state.json`; then separate runner instances for bot containers; then
  Redis + Postgres to run more than one server process.
- Reconnect-within-30 s for dropped live players; hand logs in S3.
