"""Offline bot checks shared by `pokbot validate` and `pokbot play` (which validates first)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pokbot_core.bundle import BundleError, load_manifest
from pokbot_core.match import Match, MatchConfig
from pokbot_core.players import HouseBot, SubprocessBot, bot_command, describe_argv


@dataclass
class Check:
    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str = ""


@dataclass
class Report:
    target: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    def ok(self, strict: bool = False) -> bool:
        bad = {"fail", "warn"} if strict else {"fail"}
        return not any(c.status in bad for c in self.checks)

    def to_dict(self) -> dict:
        return {"target": self.target, "ok": self.ok(), "checks": [c.__dict__ for c in self.checks]}


async def validate(target: str, hands: int = 500, time_ms: int = 5, game: str = "holdem") -> Report:
    report = Report(target)

    path = Path(target.split(":")[0])
    try:
        if path.is_dir():
            manifest = load_manifest(path)
            report.add("bundle", "pass", f"{manifest['name']} · {manifest['runtime']} · {manifest['entrypoint']}")
        argv = bot_command(target)
        report.add("loads", "pass", "python bot (SDK runner)" if "pokbot_core.sdk.runner" in argv else describe_argv(argv))
    except (FileNotFoundError, ValueError, BundleError) as e:
        report.add("loads", "fail", str(e))
        return report

    totals = {"hands": 0, "illegal": 0, "timeouts": 0, "errors": 0}
    p99 = 0.0
    stderr: list[str] = []
    for seats, n_hands in ((2, max(hands // 2, 2)), (6, max(hands // 2, 1))):
        bot = SubprocessBot("candidate", argv)
        opponents = [HouseBot(f"House{i}", style=("tight", "random", "caller")[i % 3], seed=i) for i in range(seats - 1)]
        cfg = MatchConfig(game=game, hands=n_hands, seed=1234 + seats, time_ms=time_ms, timebank_ms=1000)
        summary = await Match([bot, *opponents], cfg).run()
        me = summary["players"][0]
        for k in totals:
            totals[k] += me[k]
        p99 = max(p99, me["think_p99_ms"])
        stderr += bot.stderr_tail

    report.add(
        "runs",
        "fail" if totals["errors"] else "pass",
        f"{totals['hands']} hands heads-up + 6-max" + (f", {totals['errors']} crashes/disconnects" if totals["errors"] else ""),
    )
    report.add(
        "legal actions",
        "fail" if totals["illegal"] else "pass",
        f"{totals['illegal']} illegal or malformed replies" if totals["illegal"] else "every reply valid (F X C R A)",
    )
    report.add(
        "timing",
        "fail" if totals["timeouts"] else ("warn" if p99 > time_ms * 0.5 else "pass"),
        f"p99 {p99:.1f} ms (limit {time_ms} ms), {totals['timeouts']} timeouts",
    )
    if stderr:
        report.add("stderr", "warn", f"{len(stderr)} lines, last: {stderr[-1][:120]}")
    return report
