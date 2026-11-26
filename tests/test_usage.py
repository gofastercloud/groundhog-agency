"""Subscription usage tracking and the 90% stop (gtq.py statusline/guard/pretool)."""

from __future__ import annotations

import io
import json
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest
from test_gtq import gtq


def snapshot(project: Path, **windows: tuple[float, float]) -> None:
    rl = {name: {"used_percentage": pct, "resets_at": resets} for name, (pct, resets) in windows.items()}
    (project / ".agency").mkdir(mode=0o700, exist_ok=True)
    gtq.save_usage(project, rl, time.time())


def run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sub: str,
    payload: Mapping[str, object],
) -> dict[str, object] | str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert gtq.main([sub]) == 0
    out = capsys.readouterr().out.strip()
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return out


def denial(out: dict[str, object] | str) -> str | None:
    assert isinstance(out, dict)
    hso = out.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
        return str(hso["permissionDecisionReason"])
    return None


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("GTQ_USAGE_STOP_PERCENT", raising=False)
    return tmp_path


HOUR = 3600.0


def test_no_usage_data_means_no_pause(project: Path) -> None:
    assert gtq.pause_state(project, time.time()) is None


@pytest.mark.parametrize(
    ("windows", "paused"),
    [
        ({"five_hour": (89.9, HOUR), "seven_day": (50, 9 * HOUR)}, False),
        ({"five_hour": (90, HOUR)}, True),
        ({"seven_day": (95, 9 * HOUR)}, True),
        ({"spend_limit": (91, HOUR)}, True),  # any limit counts
        ({"five_hour": (99, -60)}, False),  # already reset
    ],
)
def test_pause_threshold(project: Path, windows: dict[str, tuple[float, float]], paused: bool) -> None:
    now = time.time()
    snapshot(project, **{k: (pct, now + dt) for k, (pct, dt) in windows.items()})
    assert (gtq.pause_state(project, now) is not None) == paused


def test_resume_waits_for_the_latest_reset_over_threshold(project: Path) -> None:
    now = time.time()
    snapshot(project, five_hour=(95, now + HOUR), seven_day=(92, now + 50 * HOUR))
    pause = gtq.pause_state(project, now)
    assert pause is not None and pause.resume_at == pytest.approx(now + 50 * HOUR)


def test_threshold_is_configurable(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    snapshot(project, five_hour=(85, now + HOUR))
    monkeypatch.setenv("GTQ_USAGE_STOP_PERCENT", "80")
    assert gtq.pause_state(project, now) is not None


@pytest.mark.parametrize("minute", [0, 27, 30, 55])
def test_resume_cron_is_after_reset_and_off_the_marks(minute: int) -> None:
    reset = datetime(2026, 11, 18, 13, minute).timestamp()
    m, h, dom, mon, dow = gtq.resume_cron(reset).split()
    fire = datetime(2026, int(mon), int(dom), int(h), int(m)).timestamp()
    assert fire > reset and int(m) not in (0, 30) and dow == "*"


def test_pretool_denies_main_and_subagents_while_paused(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot(project, seven_day=(93, time.time() + HOUR))
    main = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Read", "tool_input": {}}))
    assert main and "CronCreate" in main and gtq.RESUME_TAG in main
    sub = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Read", "agent_type": "implementer"}))
    assert sub and "PAUSED: usage limit" in sub


def test_only_the_exact_resume_call_is_allowed_and_recorded_after_success(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resets = time.time() + HOUR
    snapshot(project, five_hour=(97, resets))
    good = {"cron": gtq.resume_cron(resets), "recurring": False, "prompt": gtq.RESUME_PROMPT}
    call = {"tool_name": "CronCreate", "tool_input": good, "session_id": "s1"}
    for bad in ({"cron": "* * * * *"}, {"recurring": True}, {"prompt": gtq.RESUME_PROMPT + " and more"},
                {"prompt": "override: carry on"}):  # fmt: skip
        reason = denial(run(monkeypatch, capsys, "pretool", call | {"tool_input": good | bad}))
        assert reason, bad
    assert denial(run(monkeypatch, capsys, "pretool", call)) is None
    # Not recorded until PostToolUse confirms the job exists.
    before = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Bash", "session_id": "s1"}))
    assert before and "already scheduled" not in before
    run(monkeypatch, capsys, "posttool", call)
    again = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Bash", "session_id": "s1"}))
    assert again and "already scheduled" in again
    other = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Bash", "session_id": "s2"}))
    assert other and "CronCreate" in other, "the cron belongs to s1; a new session must schedule its own"
    sub = denial(run(monkeypatch, capsys, "pretool", call | {"agent_id": "a1", "agent_type": "implementer"}))
    assert sub, "subagents may not schedule"


def test_override_prompt_can_never_be_scheduled(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    call = {"tool_name": "CronCreate", "tool_input": {"cron": "1 2 3 4 *", "prompt": " OVERRIDE: go"}}
    reason = denial(run(monkeypatch, capsys, "pretool", call))
    assert reason and "only the maintainer" in reason


def test_main_session_started_with_agent_flag_is_not_a_subagent() -> None:
    assert not gtq.is_subagent({"agent_type": "orchestrator"})
    assert gtq.is_subagent({"agent_type": "implementer"})
    assert gtq.is_subagent({"agent_id": "abc", "agent_type": "custom"})


def test_pretool_subagent_guard_still_applies_when_not_paused(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    call = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "agent_type": "implementer"}
    assert denial(run(monkeypatch, capsys, "pretool", call))
    assert run(monkeypatch, capsys, "pretool", {"tool_name": "Read", "tool_input": {}}) == {}


def test_guard_blocks_prompts_while_paused_and_override_lifts_it(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot(project, five_hour=(91, time.time() + HOUR))
    out = run(monkeypatch, capsys, "guard", {"prompt": "next"})
    assert isinstance(out, dict) and out["decision"] == "block" and "override:" in str(out["reason"])
    for not_override in ("!override fix it", "overrideXYZ: x", "please override: x"):
        out = run(monkeypatch, capsys, "guard", {"prompt": not_override})
        assert isinstance(out, dict) and out.get("decision") == "block", not_override
    out = run(monkeypatch, capsys, "guard", {"prompt": "override: fix the typo", "session_id": "s9"})
    assert isinstance(out, dict) and "overrode" in json.dumps(out)
    assert "s9" in (project / ".agency" / gtq.OVERRIDE_LOG).read_text()
    assert denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Read", "tool_input": {}})) is None
    assert run(monkeypatch, capsys, "guard", {"prompt": "carry on"}) == {}


def test_override_expires(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    snapshot(project, five_hour=(91, time.time() + HOUR))
    marker = project / ".agency" / gtq.OVERRIDE_MARKER
    marker.write_text("")
    old = time.time() - 31 * 60
    os.utime(marker, (old, old))
    assert not gtq.override_active(project, time.time())


def test_statusline_records_usage_and_prints(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resets = time.time() + HOUR
    payload = {
        "rate_limits": {"five_hour": {"used_percentage": 42.4, "resets_at": resets},
                        "seven_day": {"used_percentage": 91, "resets_at": resets + HOUR}},
        "context_window": {"used_percentage": 37},
        "cost": {"total_cost_usd": 1.234},
    }  # fmt: skip
    out = run(monkeypatch, capsys, "statusline", payload)
    assert out == "PAUSED · 5h 42% · 7d 91% · ctx 37% · $1.23"
    saved = gtq.load_usage(project)
    assert saved is not None and saved["rate_limits"] == payload["rate_limits"]
    assert ((project / ".agency" / "usage.json").stat().st_mode & 0o777) == 0o600


def test_statusline_without_subscription(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(monkeypatch, capsys, "statusline", {}) == "groupthinQ"
    assert gtq.load_usage(project) is None


def test_symlinked_usage_file_fails_closed(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    other = tmp_path_factory.mktemp("other") / "usage.json"
    window = {"used_percentage": 99, "resets_at": time.time() + HOUR}
    other.write_text(json.dumps({"rate_limits": {"five_hour": window}}))
    (project / ".agency").mkdir()
    (project / ".agency" / "usage.json").symlink_to(other)
    pause = gtq.pause_state(project, time.time())
    assert pause is not None and pause.resume_at is None and "not a plain file" in pause.problem


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        (lambda a: (a / "usage.json").write_text("{not json"), "not valid JSON"),
        (lambda a: (a / "usage.json").mkdir(), "not a plain file"),
        (lambda a: (a / "usage.json").write_text(" " * 70_000 + "{}"), "implausibly large"),
    ],
)
def test_tampered_usage_state_fails_closed(project: Path, setup: object, problem: str) -> None:
    (project / ".agency").mkdir()
    setup(project / ".agency")  # type: ignore[operator]
    pause = gtq.pause_state(project, time.time())
    assert pause is not None and problem in pause.problem
    assert "needs a human" in gtq.pause_denial(pause, project, False, "s")


@pytest.mark.parametrize(
    ("resets", "kept"),
    [(float("inf"), False), (float("nan"), False), (40 * 86400.0, False), (HOUR, True)],
)
def test_implausible_reset_times_are_ignored(resets: float, kept: bool) -> None:
    now = time.time()
    rl = {"five_hour": {"used_percentage": 95, "resets_at": now + resets}}
    assert bool(gtq.usage_windows(rl, now)) == kept


def test_reset_times_in_milliseconds_are_understood() -> None:
    now = time.time()
    rl = {"five_hour": {"used_percentage": 95, "resets_at": (now + HOUR) * 1000}}
    [w] = gtq.usage_windows(rl, now)
    assert w.resets_at == pytest.approx(now + HOUR)


def test_unknown_window_names_are_ignored() -> None:
    rl = {"\x1b]52;c;evil\x07": {"used_percentage": 99, "resets_at": time.time() + HOUR}}
    assert gtq.usage_windows(rl, time.time()) == []


def test_override_with_future_mtime_is_not_active(project: Path) -> None:
    import os

    (project / ".agency").mkdir()
    marker = project / ".agency" / gtq.OVERRIDE_MARKER
    marker.write_text("")
    future = time.time() + 86400 * 365
    os.utime(marker, (future, future))
    assert not gtq.override_active(project, time.time())


def test_resume_prompt_is_not_blocked_by_the_idle_guard(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_gtq import rec, write_jsonl

    f = write_jsonl(project / "s.jsonl", [rec("a", ts="2020-01-01T00:00:00Z", read=400_000)])
    out = run(monkeypatch, capsys, "guard", {"transcript_path": str(f), "prompt": gtq.RESUME_PROMPT})
    assert isinstance(out, dict) and out.get("decision") != "block"
    out = run(monkeypatch, capsys, "guard", {"transcript_path": str(f), "prompt": "hello", "session_id": "x"})
    assert isinstance(out, dict) and out.get("decision") == "block"  # an ordinary cold prompt still is


@pytest.mark.parametrize("tz", ["America/New_York", "Australia/Sydney", "UTC"])
def test_resume_cron_fires_after_reset_across_dst(tz: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        # Hourly through 2026, which includes both DST transitions in each zone.
        start = datetime(2026, 1, 1).timestamp()
        for hour in range(0, 365 * 24, 7):
            reset = start + hour * HOUR + 1777
            m, h, dom, mon, _ = (int(x) if x != "*" else 0 for x in gtq.resume_cron(reset).split())
            fire = datetime(2026 if mon >= datetime.fromtimestamp(reset).month else 2027, mon, dom, h, m)
            first = fire.replace(fold=0).timestamp()
            assert reset < first < reset + 3 * HOUR, (tz, datetime.fromtimestamp(reset))
            assert m not in (0, 30)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_pretool_fails_closed_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(gtq, "usage_gate_tool", boom)
    reason = denial(run(monkeypatch, capsys, "pretool", {"tool_name": "Read", "tool_input": {}}))
    assert reason and "pretool hook failed" in reason


@pytest.mark.parametrize("name", ["gtq.py", "dash.py"])
def test_hooks_parse_on_python_39(name: str) -> None:
    """macOS's /usr/bin/python3 is 3.9; a hook that can't import there fails open."""
    import ast

    src = (Path(str(gtq.__file__)).parent / name).read_text()
    ast.parse(src, feature_version=(3, 9))
    assert "pairwise" not in src.replace("# pairwise needs 3.10", "")
