"""Tests for the cross-project BM25 knowledge base (kb.py) and factory metrics (gtq.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_gtq import gtq

KB_PATH = Path(__file__).resolve().parents[1] / "harness" / ".claude" / "hooks" / "kb.py"


def _load_kb() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kb", KB_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kb"] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load_kb()


def test_kb_add_and_bm25_search(tmp_path: Path) -> None:
    p1 = kb.add_note(
        tmp_path,
        kind="library",
        title="vhs terminal recorder with Catppuccin Mocha",
        tags=["vhs", "cli", "video"],
        project="demo-builder",
        body="Use Set Theme Catppuccin Mocha and Sleep 500ms after Enter.",
    )
    kb.add_note(
        tmp_path,
        kind="pitfall",
        title="ffmpeg sidechain ducking filter ordering",
        tags=["ffmpeg", "audio"],
        project="demo-builder",
        body="Always normalise sample rates before sidechaincompress.",
    )
    assert p1.is_file()
    notes = kb.load_notes(tmp_path)
    assert len(notes) == 2

    hits = kb.search_notes(notes, "sidechaincompress audio ffmpeg")
    assert hits and hits[0][1].kind == "pitfall" and "sidechain" in hits[0][1].title

    lib_hits = kb.search_notes(notes, "terminal catppuccin", kind="library")
    assert len(lib_hits) == 1 and lib_hits[0][1].project == "demo-builder"


def test_kb_cli_search_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        kb.main(
            [
                "--kb-dir",
                str(tmp_path),
                "add",
                "--kind",
                "pattern",
                "--title",
                "Worktree TDD loop",
                "--tags",
                "git,tdd",
                "--project",
                "groundhog-agency",
                "--body",
                "Isolate every ticket in its own git worktree.",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert kb.main(["--kb-dir", str(tmp_path), "search", "worktree", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["title"] == "Worktree TDD loop"


def test_verdict_logging_and_metrics(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        gtq.main(
            ["verdict", "--project", str(tmp_path), "--ticket", "#1", "--attempt", "1", "--result", "PASS"]
        )
        == 0
    )
    assert (
        gtq.main(
            [
                "verdict",
                "--project",
                str(tmp_path),
                "--ticket",
                "2",
                "--attempt",
                "1",
                "--result",
                "FAIL",
                "--reason",
                "missing edge-case test",
            ]
        )
        == 0
    )
    assert (
        gtq.main(
            ["verdict", "--project", str(tmp_path), "--ticket", "2", "--attempt", "2", "--result", "PASS"]
        )
        == 0
    )
    assert (
        gtq.main(
            ["verdict", "--project", str(tmp_path), "--ticket", "3", "--attempt", "1", "--result", "FAIL"]
        )
        == 0
    )
    capsys.readouterr()

    assert gtq.main(["metrics", "--project", str(tmp_path), "--json"]) == 0
    m = json.loads(capsys.readouterr().out)
    assert m["tickets_reviewed"] == 3
    assert m["first_pass"] == 1
    assert m["retry_pass"] == 1
    assert m["escalated"] == 1
    assert m["first_pass_rate"] == pytest.approx(0.333, abs=0.002)
