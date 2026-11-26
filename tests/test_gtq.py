"""Tests for the groupthinQ cost tooling (harness/.claude/hooks/gtq.py)."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

GTQ_PATH = Path(__file__).resolve().parents[1] / "harness" / ".claude" / "hooks" / "gtq.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gtq", GTQ_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gtq"] = mod
    spec.loader.exec_module(mod)
    return mod


gtq = _load()

T0 = "2026-11-18T00:00:00.000Z"


def rec(
    req: str,
    *,
    ts: str = T0,
    model: str = "claude-sonnet-5-5",
    inp: int = 0,
    w5: int = 0,
    w1h: int = 0,
    read: int = 0,
    out: int = 0,
    branch: str = "",
    sidechain: bool = False,
) -> dict[str, object]:
    return {
        "type": "assistant",
        "requestId": req,
        "timestamp": ts,
        "isSidechain": sidechain,
        "gitBranch": branch,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": w5 + w1h,
                "cache_read_input_tokens": read,
                "output_tokens": out,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": w5,
                    "ephemeral_1h_input_tokens": w1h,
                },
            },
        },
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# ------------------------------------------------------------------ pricing


def test_usd_uses_per_type_prices() -> None:
    r = gtq.Request("claude-opus-5-5", 0, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert r.usd == pytest.approx(4.0 + 5.0 + 8.0 + 0.20 + 20.0)


def test_unknown_model_costs_zero_and_is_flagged() -> None:
    r = gtq.Request("mystery", 0, 10**6, 0, 0, 0, 0)
    assert r.usd == 0.0 and not r.priced


@pytest.mark.parametrize(
    ("model", "key"),
    [
        ("claude-opus-5-5", "claude-opus-5-5"),
        ("claude-opus-5-5-20261101", "claude-opus-5-5"),
        ("claude-sonnet-5-5[1m]", "claude-sonnet-5-5"),
        ("claude-opus-5", None),
        ("gpt-5", None),
    ],
)
def test_price_key_prefix_match(model: str, key: str | None) -> None:
    assert gtq.price_key(model) == key


def test_haiku_long_prompt_tier() -> None:
    short = gtq.Request("claude-haiku-5-5", 0, 100_000, 0, 0, 0, 0)
    long = gtq.Request("claude-haiku-5-5", 0, 100_001, 0, 0, 0, 0)
    assert short.usd == pytest.approx(0.01)
    assert long.usd == pytest.approx(100_001 * 0.50 / 1e6)


# ------------------------------------------------------------------ parsing


def test_iter_requests_dedupes_streamed_blocks(tmp_path: Path) -> None:
    f = write_jsonl(tmp_path / "s.jsonl", [rec("a", read=10), rec("a", read=10), rec("b", out=5)])
    reqs = list(gtq.iter_requests(f))
    assert [r.read for r in reqs] == [10, 0]
    assert reqs[1].out == 5


def test_iter_requests_skips_synthetic_and_junk(tmp_path: Path) -> None:
    f = tmp_path / "s.jsonl"
    f.write_text(
        "not json\n"
        + json.dumps(rec("a", model="<synthetic>", read=1))
        + "\n"
        + json.dumps({"type": "user", "usage": 1})
        + "\n"
    )
    assert list(gtq.iter_requests(f)) == []


def test_load_session_includes_subagents(tmp_path: Path) -> None:
    main = write_jsonl(tmp_path / "sess.jsonl", [rec("m1", model="claude-opus-5-5", read=100)])
    sub = write_jsonl(
        tmp_path / "sess" / "subagents" / "agent-abc.jsonl",
        [rec("s1", read=50, branch="t-42-login")],
    )
    sub.with_suffix(".meta.json").write_text(json.dumps({"agentType": "implementer"}))
    chains = gtq.load_session(main)
    assert [(c.chain, c.agent_type) for c in chains] == [("main", "main"), ("abc", "implementer")]
    assert chains[1].branch == "t-42-login"


def test_project_dir_encoding() -> None:
    d = gtq.project_dir_for("/Users/x/Code/a.b/.claude/worktrees/w")
    assert d.name == "-Users-x-Code-a-b--claude-worktrees-w"


@pytest.mark.parametrize(
    ("branch", "ticket"),
    [
        ("t-42-login", "42"),
        ("agent/t-7-fix", "7"),
        ("t-9", "9"),
        # Only the implementer's t-<N>- convention counts: numbers elsewhere are not tickets.
        ("issue/17", ""),
        ("feat/123-thing", ""),
        ("release-2026-q4", ""),
        ("main", ""),
        ("", ""),
    ],
)
def test_ticket_of(branch: str, ticket: str) -> None:
    assert gtq.ticket_of(branch) == ticket


# ------------------------------------------------------------------ waste


def test_find_rebuilds_flags_idle_rewrite() -> None:
    prev = gtq.Request("claude-opus-5-5", 0, 0, 0, 0, 500_000, 10)
    cold = gtq.Request("claude-opus-5-5", 2 * 3600, 0, 0, 500_100, 0, 10)
    warm = gtq.Request("claude-opus-5-5", 60, 0, 1_000, 0, 500_000, 10)
    [rb] = gtq.find_rebuilds([prev, cold])
    assert rb.reason == "idle >1h" and rb.tokens == 500_000
    assert gtq.find_rebuilds([prev, warm]) == []


def test_find_rebuilds_model_switch() -> None:
    a = gtq.Request("claude-opus-5-5", 0, 0, 0, 0, 100_000, 1)
    b = gtq.Request("claude-sonnet-5-5", 30, 0, 100_000, 0, 0, 1)
    assert gtq.find_rebuilds([a, b])[0].reason == "model switch"


@pytest.mark.parametrize(
    ("gap_s", "w5", "w1h", "reason"),
    [(600, 100_000, 0, "idle >5m"), (600, 0, 100_000, "prefix changed"), (30, 100_000, 0, "prefix changed")],
)
def test_find_rebuilds_reasons(gap_s: int, w5: int, w1h: int, reason: str) -> None:
    a = gtq.Request("claude-sonnet-5-5", 0, 0, 0, 0, 100_000, 1)
    b = gtq.Request("claude-sonnet-5-5", gap_s, 0, w5, w1h, 0, 1)
    [rb] = gtq.find_rebuilds([a, b])
    assert rb.reason == reason


def test_find_rebuilds_ignores_small_writes() -> None:
    a = gtq.Request("claude-sonnet-5-5", 0, 0, 0, 0, 15_000, 1)
    b = gtq.Request("claude-sonnet-5-5", 4000, 0, 15_000, 0, 0, 1)
    assert gtq.find_rebuilds([a, b]) == []


def test_tail_records_drops_partial_first_line(tmp_path: Path) -> None:
    f = write_jsonl(tmp_path / "s.jsonl", [{"n": i, "pad": "x" * 50} for i in range(100)])
    got = gtq.tail_records(f, max_bytes=500)
    assert got and all(isinstance(r.get("n"), int) for r in got)
    assert got[-1]["n"] == 99
    assert len(gtq.tail_records(f)) == 100  # whole file: nothing dropped


# ------------------------------------------------------------------ ledger


def test_ledger_roundtrip_keeps_latest_row(tmp_path: Path) -> None:
    main = write_jsonl(tmp_path / "sess.jsonl", [rec("m1", read=100, out=10)])
    ledger = tmp_path / ".agency" / "ledger.csv"
    [chain] = gtq.load_session(main)
    gtq.append_ledger(ledger, [gtq.chain_row(chain, 0)])
    write_jsonl(main, [rec("m1", read=100, out=10), rec("m2", read=200, out=10)])
    [chain] = gtq.load_session(main)
    gtq.append_ledger(ledger, [gtq.chain_row(chain, 1)])
    with ledger.open() as fh:
        assert len(list(csv.DictReader(fh))) == 2
    [row] = gtq.read_ledger(ledger)
    assert row["requests"] == "2" and row["cache_read"] == "300"


def test_cmd_ledger_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main = write_jsonl(tmp_path / "sess.jsonl", [rec("m1", read=100)])
    (tmp_path / "proj").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(main)})))
    assert gtq.main(["ledger"]) == 0
    ledger = tmp_path / "proj" / ".agency" / "ledger.csv"
    [row] = gtq.read_ledger(ledger)
    assert row["project"] == "proj"
    assert (ledger.stat().st_mode & 0o777) == 0o600
    assert (ledger.parent.stat().st_mode & 0o777) == 0o700


@pytest.mark.parametrize(
    ("value", "safe"),
    [("=HYPERLINK(1)", "'=HYPERLINK(1)"), ("+1", "'+1"), ("@x", "'@x"), ("-2", "'-2"),
     ("t-1-ok", "t-1-ok"), (42, 42)],
)  # fmt: skip
def test_csv_safe(value: object, safe: object) -> None:
    assert gtq.csv_safe(value) == safe


def test_ledger_neutralises_formula_branch(tmp_path: Path) -> None:
    main = write_jsonl(tmp_path / "s.jsonl", [rec("m1", read=1, branch='=HYPERLINK("http://x")')])
    [chain] = gtq.load_session(main)
    ledger = tmp_path / "l.csv"
    gtq.append_ledger(ledger, [gtq.chain_row(chain, 0)])
    assert gtq.read_ledger(ledger)[0]["branch"].startswith("'=")


def test_append_ledger_refuses_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    link = tmp_path / "ledger.csv"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        gtq.append_ledger(link, [{"session": "s", "chain": "main"}])
    assert victim.read_text() == "keep"


def test_agency_dir_refuses_symlink(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".agency").symlink_to(tmp_path / "elsewhere")
    assert gtq.agency_dir(proj) is None


def test_read_ledger_skips_repeated_headers_and_junk(tmp_path: Path) -> None:
    ledger = tmp_path / "l.csv"
    header = ",".join(gtq.LEDGER_FIELDS)
    ledger.write_text(f"{header}\n{header}\n,,,\nnot,a,row\n")
    assert gtq.read_ledger(ledger) == []


def test_report_days_filters_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time as _time

    def row(name: str, ts: float) -> dict[str, object]:
        ended = gtq.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
        return {"ended_at": ended, "session": name, "chain": "main", "agent_type": name, "usd": 1}

    ledger = tmp_path / "l.csv"
    gtq.append_ledger(ledger, [row("old", _time.time() - 10 * 86400), row("new", _time.time())])
    assert gtq.main(["report", "--ledger", str(ledger), "--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "new" in out and "old" not in out


def test_report_warns_on_unpriced_models(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proj = tmp_path / "-p"
    write_jsonl(proj / "s.jsonl", [rec("a", model="claude-future-9", inp=10)])
    rows = gtq.report_rows_from_transcripts([proj / "s.jsonl"], 0)
    gtq.print_report(rows, "models", 5)
    assert "1 requests used a model with no price" in capsys.readouterr().err


# ------------------------------------------------------------------ guard


def test_main_state_reads_latest_main_request(tmp_path: Path) -> None:
    f = write_jsonl(
        tmp_path / "s.jsonl",
        [
            rec("a", read=10, model="claude-opus-5-5"),
            rec("b", read=999, sidechain=True),
            rec("c", read=20, w5=5, model="claude-opus-5-5"),
        ],
    )
    st = gtq.main_state(f)
    assert st is not None and st.ctx == 25 and st.model == "claude-opus-5-5"


def test_main_state_none_after_compaction(tmp_path: Path) -> None:
    f = write_jsonl(
        tmp_path / "s.jsonl",
        [rec("a", read=900_000), {"type": "system", "subtype": "compact_boundary"}],
    )
    assert gtq.main_state(f) is None


@pytest.mark.parametrize(
    ("ctx", "idle_s", "warned", "action"),
    [
        (50_000, 0, False, "none"),
        (160_000, 0, False, "context"),
        (300_000, 0, False, "context"),
        (300_000, 2 * 3600, False, "block"),
        (300_000, 2 * 3600, True, "context"),  # resend after the warning proceeds
        (50_000, 2 * 3600, False, "none"),  # small context: cold cache is cheap
    ],
)
def test_guard_decision(ctx: int, idle_s: int, warned: bool, action: str) -> None:
    st = gtq.MainState(ctx, 1_000_000.0, "claude-opus-5-5")
    got, msg = gtq.guard_decision(st, 1_000_000.0 + idle_s, warned)
    assert got == action
    assert bool(msg) == (action != "none")


def test_cmd_guard_blocks_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write_jsonl(
        tmp_path / "s.jsonl",
        [rec("a", ts="2020-01-01T00:00:00Z", read=400_000, model="claude-opus-5-5")],
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = json.dumps({"transcript_path": str(f), "session_id": "sess-1"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    gtq.main(["guard"])
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    gtq.main(["guard"])
    second = json.loads(capsys.readouterr().out)
    assert "decision" not in second
    assert "additionalContext" in second["hookSpecificOutput"]


def test_cmd_guard_rearms_after_a_new_idle_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import os
    import time as _time

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    f = tmp_path / "s.jsonl"
    payload = json.dumps({"transcript_path": str(f), "session_id": "sess-2"})
    write_jsonl(f, [rec("a", ts="2020-01-01T00:00:00Z", read=400_000, model="claude-opus-5-5")])
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    gtq.main(["guard"])
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    # The maintainer carried on (warned 3h ago, last request 2h ago), then went idle again.
    marker = tmp_path / ".agency" / "guard-sess-2"
    three_h_ago = _time.time() - 3 * 3600
    os.utime(marker, (three_h_ago, three_h_ago))
    two_h_ago = gtq.datetime.fromtimestamp(_time.time() - 2 * 3600).astimezone().isoformat()
    write_jsonl(f, [rec("b", ts=two_h_ago, read=400_000, model="claude-opus-5-5")])
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    gtq.main(["guard"])
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_cmd_guard_never_blocks_without_safe_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "elsewhere").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".agency").symlink_to(tmp_path / "elsewhere")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    f = write_jsonl(
        tmp_path / "s.jsonl",
        [rec("a", ts="2020-01-01T00:00:00Z", read=400_000, model="claude-opus-5-5")],
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(f)})))
    gtq.main(["guard"])
    out = json.loads(capsys.readouterr().out)
    # A symlinked .agency is tampering: the usage stop fails closed and nothing is written through it.
    assert out["decision"] == "block" and "can't be trusted" in out["reason"]
    assert list((tmp_path / "elsewhere").iterdir()) == []


# ------------------------------------------------------------------ pretool


@pytest.mark.parametrize(
    ("tool", "tool_input", "denied"),
    [
        ("Edit", {"file_path": "/r/src/app.py"}, False),
        ("Edit", {"file_path": "/r/.claude/settings.json"}, True),
        ("Write", {"file_path": "/r/.claude/hooks/gtq.py"}, True),
        ("Edit", {"file_path": "/r/.claude/agents/reviewer.md"}, True),
        ("Write", {"file_path": "/r/CLAUDE.md"}, True),
        ("Edit", {"file_path": "/r/docs/CLAUDE.md"}, True),
        ("Write", {"file_path": "/r/STATUS.md"}, True),
        ("Edit", {"file_path": "/r/.github/workflows/ci.yml"}, True),
        ("Write", {"file_path": "/r/.github/CODEOWNERS"}, True),
        ("Bash", {"command": "echo '* @x' > .github/CODEOWNERS"}, True),
        ("NotebookEdit", {"notebook_path": "/r/.claude/skills/x.ipynb"}, True),
        ("Bash", {"command": "make verify"}, False),
        ("Bash", {"command": "git commit -m 'x' && git push origin t-1-x"}, True),
        ("Bash", {"command": "git -C . push"}, True),
        ("Bash", {"command": "gh issue view 12"}, False),
        ("Bash", {"command": "gh pr diff 3 | head"}, False),
        ("Bash", {"command": "gh issue comment 12 --body hi"}, True),
        ("Bash", {"command": "gh api repos/o/r/issues"}, True),
        ("Bash", {"command": "gh pr create --draft"}, True),
        ("Bash", {"command": "cat .claude/settings.json"}, False),
        ("Bash", {"command": "echo x >> .claude/settings.json"}, True),
        ("Bash", {"command": "sed -i '' s/a/b/ CLAUDE.md"}, True),
        ("Bash", {"command": 'python3 -c \'open(".github/workflows/x","w")\''}, True),
        ("Read", {"file_path": "/r/.claude/settings.json"}, False),
    ],
)
def test_subagent_tool_denial(tool: str, tool_input: dict[str, object], denied: bool) -> None:
    assert (gtq.subagent_tool_denial(tool, tool_input) is not None) == denied


def test_cmd_pretool_only_guards_subagents(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    call = {"tool_name": "Bash", "tool_input": {"command": "git commit -m wip"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call)))
    gtq.main(["pretool"])
    assert capsys.readouterr().out == ""
    push = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "agent_type": "implementer"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(push)))
    gtq.main(["pretool"])
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"


def test_guard_ignores_missing_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert gtq.main(["guard"]) == 0
