# Launching pokbot

Drafts and a checklist for getting people (and their agents) to enter bots. Nothing here has
been posted; every item is for the maintainer to do by hand.

## Before posting (blockers)

1. **GitHub login** is on (done 2026-10-29).
2. **Own domain (later).** The site is on GitHub Pages (`baobabtr33.github.io/pokbot`), which
   is fine to launch with, but the install command still shows the sslip.io server, which
   looks temporary and breaks if the IP changes. Buy a short domain, add an A record for
   100.58.68.17, and redeploy with `POKBOT_HOST=<domain>`; then set `POKBOT_API` in
   `.github/workflows/pages.yml` to the new host.
3. **Seed the field.** Upload two or three example bots (e.g. `bots/examples/pairs.py`) under
   your own account so the first visitors find real opponents and a non-empty leaderboard.
4. **Public GitHub repo** with the README, topics `poker`, `poker-bot`, `ai`, `bot-competition`,
   `texas-holdem`, `cli`, `llm-agents`, a social preview image (`web/og.png`) and a LICENSE.

## Search and crawlers

- Google Search Console and Bing Webmaster Tools: verify the domain, submit
  `/sitemap.xml`.
- Check the share card with the X/LinkedIn/Facebook preview debuggers.
- `/llms.txt` and `/bots.md` are plain text meant for LLM agents and coding assistants.

## Where to post

| Place | Angle |
|---|---|
| Hacker News (Show HN) | terminal-first, 5 ms cap, any language, agents can enter |
| r/programming, r/Python, r/poker (check rules), r/MachineLearning (Saturday thread) | bot-writing challenge |
| r/LocalLLaMA, AI agent Discords | "point your coding agent at /llms.txt and enter a bot" |
| dev.to / Hashnode | tutorial: a 20-line bot that beats the house bots |
| X / Bluesky / Mastodon | 20-second terminal recording of a final table and the fireworks |
| awesome-poker, awesome-bots, awesome-cli-apps, awesome-llm-agents lists | one-line PR |
| Product Hunt | after the domain and a few weeks of leaderboard history |

Post during US morning on a weekday; be in the comments for the first two hours.

## Drafts

### Show HN

> **Show HN: pokbot – poker bot tournaments every 30 minutes, from the terminal**
>
> I built a small arena where bots play No-Limit Hold'em knockout tournaments at :00 and :30.
> A bot is any program that reads JSON lines on stdin and prints `F`, `X`, `C`, `R 24` or `A`.
> Each decision gets 5 ms, so it's about strategy rather than compute. Tables of six, top two
> advance, bot tables play up to 10,000 hands so variance mostly washes out.
>
> `curl -fsSL <host>/install.sh | sh`, `pokbot login` (GitHub device code, no scopes), then
> `pokbot play mybot`. You can also upload a bot (it runs in gVisor with no network) or play
> by hand in the TUI. There's an `/llms.txt` if you want your coding agent to write one.
>
> Would love feedback on the protocol and on what makes a bot competition fun to come back to.

### Reddit / dev.to

> **I made a poker bot tournament that runs every 30 minutes, and your bot can be 5 lines**
>
> (the minimal Python bot from the README, then a 20-line pocket-pair raiser, then how to
> validate, practice locally, and enter; end with the leaderboard link)

### Short post (X / Bluesky)

> Poker bot tournaments every 30 minutes. Any language, 5 ms per decision, top 2 per table
> advance, fireworks for the champion. Point your agent at <host>/llms.txt.
> `curl -fsSL <host>/install.sh | sh`

## Keep people coming back

- Post each champion to the lobby chat (done) and later to a Discord webhook or an X account.
- Weekly "season" leaderboard reset with an all-time board kept.
- A public replay/hand-history page per tournament (shareable links help SEO too).
- Named starter bots of increasing strength as stepping stones.
