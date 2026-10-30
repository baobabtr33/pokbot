"""CLI animations (cli/anim.py) and their landing-page port (web/anim.js)."""

import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pokbot_cli import anim

ROOT = Path(__file__).resolve().parents[1]


def test_scenes_keep_their_shape():
    for frames, height in ((anim.splash(), 9), (anim.showdown(), 13), (anim.entered("alice-tag", "at 12:30"), 6)):
        grids = list(frames)
        assert len(grids) > 20
        assert all(g.h == height and all(len(row) == anim.W for row in g.cells) for g in grids)


def test_showdown_ends_with_the_pairs_bot_winning():
    final = list(anim.showdown())[-1].text()
    assert "pairs wins 148" in final
    assert "stack 274" in final and "stack 126" in final  # 200 - 74 + 148, 200 - 74


def test_entered_names_the_bot():
    final = list(anim.entered("alice-tag", "at 12:30"))[-1].text()
    assert "alice-tag" in final and "ENTERED" in final and "at 12:30" in final


def test_animations_off_without_a_terminal(monkeypatch):
    assert not anim.enabled(io.StringIO())
    monkeypatch.setenv("POKBOT_NO_ANIM", "1")
    assert not anim.enabled()


def test_play_restores_the_cursor():
    out = io.StringIO()
    anim.play(iter(list(anim.entered("a", "b"))[-2:]), fps=1000, stream=out)
    assert out.getvalue().startswith("\x1b[?25l") and out.getvalue().endswith("\x1b[?25h")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_web_port_draws_the_same_final_frame(tmp_path):
    harness = tmp_path / "run.js"
    harness.write_text(
        'const fs = require("fs"); const el = { innerHTML: "" };\n'
        "global.document = { getElementById: () => el, hidden: false, addEventListener() {} };\n"
        "global.matchMedia = () => ({ matches: true });\n"
        "global.IntersectionObserver = class { observe() {} };\n"
        "eval(fs.readFileSync(process.argv[2], 'utf8'));\n"
        'process.stdout.write(el.innerHTML.replace(/<[^>]+>/g, ""));\n'
    )
    js = subprocess.run(["node", str(harness), str(ROOT / "web/anim.js")], capture_output=True, text=True, check=True).stdout
    py = list(anim.showdown())[-1].text()

    def table(text: str) -> list[str]:  # sparks are random; compare everything else
        return [re.sub(r"[*+·.]", " ", line).rstrip() for line in text.split("\n")]

    assert table(js) == table(py)
