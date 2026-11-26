"""The terminal dashboard (harness/.claude/hooks/dash.py)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest
from test_gtq import rec, write_jsonl

DASH_PATH = Path(__file__).resolve().parents[1] / "harness" / ".claude" / "hooks" / "dash.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dash", DASH_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dash"] = mod
    spec.loader.exec_module(mod)
    return mod


dash = _load()
gtq = dash.gtq


def make_snapshot(project: Path, **kw: object) -> object:
    now = time.time()
    base: dict[str, object] = {
        "project": project, "now": now, "windows": [], "pause": None, "usage_age_s": None,
        "tickets": {label: [] for label in dash.LABELS}, "agents": [], "worktrees": [],
        "spend_today": 0.0, "spend_week": 0.0,
    }  # fmt: skip
    return dash.Snapshot(**(base | kw))


def test_render_empty(tmp_path: Path) -> None:
    text = "\n".join(dash.render(make_snapshot(tmp_path)))
    for section in ("USAGE", "TICKETS", "AGENTS", "WORKTREES", "SPEND"):
        assert section in text
    assert "no subscription usage yet" in text and "PAUSED" not in text


def test_render_usage_bars_and_pause(tmp_path: Path) -> None:
    now = time.time()
    w = [gtq.Window("five_hour", 50, now + 3600), gtq.Window("seven_day", 95, now + 86400)]
    pause = gtq.Pause((w[1],), w[1].resets_at, 90.0)
    lines = dash.render(make_snapshot(tmp_path, windows=w, pause=pause))
    assert lines[1].startswith("*** PAUSED")
    five = next(line for line in lines if line.strip().startswith("5h"))
    assert "[##########..........]" in five and "STOP" not in five
    assert "STOP" in next(line for line in lines if line.strip().startswith("7d"))


def test_render_tickets_agents_and_errors(tmp_path: Path) -> None:
    run = dash.AgentRun("implementer", "abc12345", "t-4-login", 12, 48_000, 0.8, "Edit", 30)
    tickets = {
        "in-progress": [(4, "Login")],
        "needs-human": [],
        "ready-for-agent": [(5, "Logout"), (6, "Reset")],
    }
    text = "\n".join(
        dash.render(make_snapshot(tmp_path, tickets=tickets, agents=[run], worktrees="git: boom"))
    )
    assert "#4 Login" in text and re.search(r"ready-for-agent\s+2\s+#5 Logout, #6 Reset", text)
    assert "live implementer" in text and "t-4-login" in text and "Edit" in text
    assert "git: boom" in text


def test_render_truncates_to_width(tmp_path: Path) -> None:
    tickets = {label: [(1, "x" * 300)] for label in dash.LABELS}
    assert max(len(line) for line in dash.render(make_snapshot(tmp_path, tickets=tickets), 60)) <= 60


def test_agent_runs_reads_recent_transcripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gtq, "PROJECTS_DIR", tmp_path / "projects")
    project = tmp_path / "repo"
    tdir = gtq.project_dir_for(str(project))
    main = write_jsonl(tdir / "sess.jsonl", [rec("m", read=1000, model="claude-sonnet-5-5")])
    sub = rec("s", read=5000, branch="t-9-x")
    sub["message"]["content"] = [{"type": "tool_use", "name": "Bash"}]  # type: ignore[index]
    agent = write_jsonl(main.with_suffix("") / "subagents" / "agent-xyz.jsonl", [sub])
    agent.with_suffix(".meta.json").write_text(json.dumps({"agentType": "implementer"}))
    runs = dash.agent_runs(project, time.time(), dash.Cache())
    by_type = {r.agent_type: r for r in runs}
    assert set(by_type) == {"main", "implementer"}
    assert by_type["implementer"].last_tool == "Bash" and by_type["implementer"].branch == "t-9-x"
    assert dash.agent_runs(project, time.time() + 3600, dash.Cache()) == []  # stale runs drop off


def test_collect_survives_missing_gh_and_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gtq, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    snap = dash.collect(tmp_path, dash.Cache())
    assert all(isinstance(v, str) and "unavailable" in v for v in snap.tickets.values())
    assert isinstance(snap.worktrees, str)
    assert "TICKETS" in "\n".join(dash.render(snap))


def test_up_without_tmux_prints_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dash.shutil, "which", lambda _: None)
    assert dash.cmd_up(tmp_path) == 1
    out = capsys.readouterr().out
    assert "brew install tmux" in out and "claude" in out and "dash.py" in out


def test_session_name_is_safe() -> None:
    assert dash.session_name(Path("/x/my repo;rm -rf")) == "gtq-my-repo-rm--rf"


def test_render_strips_terminal_escapes(tmp_path: Path) -> None:
    evil = "Fix \x1b]52;c;ZXZpbA==\x07 bug \x1b[2J\u202e"
    tickets = {"in-progress": [(1, evil)], "needs-human": [], "ready-for-agent": []}
    run = dash.AgentRun("x\x1b[31m", "c", "t-1-\x9bboom", 1, 1, 0.0, "Edit", 1)
    text = "\n".join(dash.render(make_snapshot(tmp_path, tickets=tickets, agents=[run], worktrees="\x1b[H")))
    assert not any(ch in text for ch in "\x1b\x07\x9b\u202e")


def test_render_shows_untrusted_usage_state(tmp_path: Path) -> None:
    (tmp_path / ".agency").mkdir()
    (tmp_path / ".agency" / "usage.json").write_text("{bad")
    snap = dash.Snapshot(
        **{**make_snapshot(tmp_path).__dict__, "pause": gtq.pause_state(tmp_path, time.time())}
    )
    assert "can't be trusted" in "\n".join(dash.render(snap))


@pytest.mark.parametrize(
    ("version", "expected"),
    [((2, 1, 311), None), (None, None), ((2, 1, 323), "env CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1 claude"),
     ((3, 0, 0), "env CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1 claude")],
)  # fmt: skip
def test_code_mode_launch_needs_a_recent_claude(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], version: object, expected: object
) -> None:
    monkeypatch.setattr(dash, "claude_version", lambda: version)
    assert dash.claude_command(True) == expected
    if expected is None:
        assert "claude update" in capsys.readouterr().out
    assert dash.claude_command(False) == "claude"
