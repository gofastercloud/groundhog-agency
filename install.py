#!/usr/bin/env python3
"""Install the groundhog-agency harness into a git repository.

    python3 install.py /path/to/repo [--owner @you] [--vendors all] [--force] [--dry-run]

Copies vendor-agnostic skills, roles, and templates, plus optional Claude Code
hooks/settings. Existing files are never overwritten without --force. An
existing .claude/settings.json is never touched, even with --force: the harness
settings are written next to it and the installer exits 2, because until you
merge them the permission rules, sandbox and subagent guard are off. Refuses to
write through symlinks or outside the target. Runs no network or gh commands.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "harness"
MARKER = "<!-- groundhog-agency:start -->"
LEGACY_MARKER = "<!-- groupthinQ:start -->"
OWNER_RE = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9._-]+)?")
ALL_VENDORS = ("claude", "antigravity", "codex")

AGENCY_LABELS: tuple[tuple[str, str, str], ...] = (
    ("agent-authored", "0E8A16", "Authored by an autonomous agent"),
    ("ready-for-agent", "5BD4F5", "Implementer may pick this up"),
    ("in-progress", "FACC15", "Claimed by an implementer"),
    ("needs-human", "FB923C", "Waiting on the maintainer"),
    ("spec", "8B5CF6", "Specification issue"),
)
HUMAN_REVIEW_LABEL: tuple[str, str, str] = (
    "needs-human-review",
    "D93F0B",
    "Requires human review before merge",
)

# Shared files always installed regardless of vendor.
SHARED_FILES: list[tuple[str, str]] = [
    ("templates/ticket.md", ".github/ISSUE_TEMPLATE/ticket.md"),
]

# Vendor-agnostic .agents/ layout (used by Antigravity, Codex, and as canonical reference).
AGENTS_FILES: list[tuple[str, str]] = [
    ("core/orchestrator.md", ".agents/orchestrator.md"),
    ("core/roles/implementer.md", ".agents/roles/implementer.md"),
    ("core/roles/reviewer.md", ".agents/roles/reviewer.md"),
    ("core/roles/scout.md", ".agents/roles/scout.md"),
    ("core/skills/spec/SKILL.md", ".agents/skills/spec/SKILL.md"),
    ("core/skills/next/SKILL.md", ".agents/skills/next/SKILL.md"),
    ("core/skills/handoff/SKILL.md", ".agents/skills/handoff/SKILL.md"),
]

# Claude Code adapter files (.claude/).
CLAUDE_FILES: list[tuple[str, str]] = [
    (".claude/agents/implementer.md", ".claude/agents/implementer.md"),
    (".claude/agents/reviewer.md", ".claude/agents/reviewer.md"),
    (".claude/agents/scout.md", ".claude/agents/scout.md"),
    ("core/skills/spec/SKILL.md", ".claude/skills/spec/SKILL.md"),
    ("core/skills/next/SKILL.md", ".claude/skills/next/SKILL.md"),
    ("core/skills/handoff/SKILL.md", ".claude/skills/handoff/SKILL.md"),
    (".claude/hooks/gtq.py", ".claude/hooks/gtq.py"),
    (".claude/hooks/dash.py", ".claude/hooks/dash.py"),
    ("core/orchestrator.md", ".claude/groundhog-agency.md"),
]

# Backwards-compatible aggregate list for callers/tests inspecting FILES.
FILES: list[tuple[str, str]] = [*SHARED_FILES, *AGENTS_FILES, *CLAUDE_FILES]
CREATE_IF_MISSING: list[tuple[str, str]] = [("templates/STATUS.md", "STATUS.md")]


@dataclass
class Result:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmerged: bool = False


class UnsafePath(Exception):
    pass


def _write_atomic(dst: Path, data: bytes) -> None:
    """Write via a temp file and rename: never writes through a link or truncates in place."""
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _check_dest(target: Path, dst: Path) -> None:
    """Refuse a destination that is, or sits under, a symlink, is hard-linked, or resolves outside target."""
    root = target.resolve()
    p = dst
    while p != target and p != p.parent:
        if p.is_symlink():
            raise UnsafePath(f"{p} is a symlink")
        p = p.parent
    if not dst.resolve().is_relative_to(root):
        raise UnsafePath(f"{dst} resolves outside {root}")
    if dst.is_file() and dst.stat().st_nlink > 1:
        raise UnsafePath(f"{dst} is hard-linked elsewhere")


def _copy(src: Path, dst: Path, rel: str, force: bool, dry: bool, res: Result, target: Path) -> None:
    _check_dest(target, dst)
    if dst.exists() and not force:
        if dst.read_bytes() != src.read_bytes():
            res.skipped.append(f"{rel} (exists and differs; --force to overwrite)")
        return
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(dst, src.read_bytes())
        shutil.copymode(src, dst)
    res.written.append(rel)


def _append_block(target: Path, rel: str, template: str, label: str, dry: bool, res: Result) -> None:
    dst = target / rel
    _check_dest(target, dst)
    block = (HARNESS / "templates" / template).read_text(encoding="utf-8")
    existing = dst.read_text(encoding="utf-8") if dst.exists() else ""
    if MARKER not in existing and LEGACY_MARKER not in existing:
        if not dry:
            text = existing.rstrip() + "\n" + block if existing else block.lstrip()
            _write_atomic(dst, text.encode("utf-8"))
        res.written.append(f"{rel} ({label})")


def parse_vendors(raw: str | None) -> tuple[str, ...]:
    if not raw or raw.strip().lower() == "all":
        return ALL_VENDORS
    chosen = tuple(dict.fromkeys(v.strip().lower() for v in raw.split(",") if v.strip()))
    bad = [v for v in chosen if v not in ALL_VENDORS]
    if bad or not chosen:
        raise SystemExit(
            f"--vendors must be 'all' or a comma-separated subset of {', '.join(ALL_VENDORS)}, not {raw!r}"
        )
    return chosen


def install(
    target: Path,
    force: bool = False,
    dry: bool = False,
    owner: str | None = None,
    vendors: tuple[str, ...] = ALL_VENDORS,
) -> Result:
    if not (target / ".git").exists():
        raise SystemExit(f"{target} is not a git repository")
    if owner is not None and not OWNER_RE.fullmatch(owner):
        raise SystemExit(f"--owner must look like @user or @org/team, not {owner!r}")
    res = Result()
    want_claude = "claude" in vendors
    want_agents = "antigravity" in vendors or "codex" in vendors

    for src_rel, dst_rel in SHARED_FILES:
        _copy(HARNESS / src_rel, target / dst_rel, dst_rel, force, dry, res, target)
    if want_agents:
        for src_rel, dst_rel in AGENTS_FILES:
            _copy(HARNESS / src_rel, target / dst_rel, dst_rel, force, dry, res, target)
    if want_claude:
        for src_rel, dst_rel in CLAUDE_FILES:
            _copy(HARNESS / src_rel, target / dst_rel, dst_rel, force, dry, res, target)
    for src_rel, dst_rel in CREATE_IF_MISSING:
        _copy(HARNESS / src_rel, target / dst_rel, dst_rel, False, dry, res, target)

    if want_claude:
        settings_src = HARNESS / ".claude" / "settings.json"
        settings_dst = target / ".claude" / "settings.json"
        if settings_dst.exists() and settings_dst.read_bytes() == settings_src.read_bytes():
            pass  # already installed
        elif settings_dst.exists():
            side = ".claude/settings.groundhog-agency.json"
            _copy(settings_src, target / side, side, True, dry, res, target)
            res.unmerged = True
            res.notes.append(
                "PROTECTIONS OFF: your existing .claude/settings.json was left unchanged, so the "
                "harness permission rules, sandbox, cost settings and hooks (including the subagent "
                "guard) are not active. Merge .claude/settings.groundhog-agency.json into it by hand "
                "(env, permissions, sandbox, hooks), then delete it and re-run the installer."
            )
        else:
            _copy(settings_src, settings_dst, ".claude/settings.json", False, dry, res, target)

    _append_block(target, "AGENTS.md", "agents-block.md", "vendor-agnostic agency workflow", dry, res)
    if want_claude:
        _append_block(target, "CLAUDE.md", "claude-block.md", "compact instructions block", dry, res)

    codeowners = target / ".github" / "CODEOWNERS"
    _check_dest(target, codeowners)
    if owner is None:
        res.notes.append("No --owner given: .github/CODEOWNERS not written. Re-run with --owner @you.")
    elif codeowners.exists():
        if "/.claude/" not in codeowners.read_text(encoding="utf-8"):
            res.notes.append(
                "Existing .github/CODEOWNERS left unchanged: add the lines from "
                "harness/templates/CODEOWNERS for .claude/, .agents/, .github/workflows/, "
                "AGENTS.md and CLAUDE.md."
            )
    else:
        text = (HARNESS / "templates" / "CODEOWNERS").read_text(encoding="utf-8").format(owner=owner)
        if not dry:
            codeowners.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(codeowners, text.encode("utf-8"))
        res.written.append(".github/CODEOWNERS")

    gitignore = target / ".gitignore"
    _check_dest(target, gitignore)
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if not any(line.strip().strip("/") == ".agency" for line in lines):
        if not dry:
            _write_atomic(gitignore, ("\n".join([*lines, ".agency/"]) + "\n").encode("utf-8"))
        res.written.append(".gitignore (.agency/)")
    return res


NEXT_STEPS = """\
Next steps (run these yourself; the installer makes no network calls):
  agent-gh label create agent-authored  --color 0E8A16 --description "Authored by an autonomous agent" --force
  agent-gh label create ready-for-agent --color 5BD4F5 --description "Implementer may pick this up" --force
  agent-gh label create in-progress     --color FACC15 --description "Claimed by an implementer" --force
  agent-gh label create needs-human     --color FB923C --description "Waiting on the maintainer" --force
  agent-gh label create spec            --color 8B5CF6 --description "Specification issue" --force
  # Check Groundhog agent-git policy for this repo (autonomous vs human-review):
  groundhog agent-git status
  # Protect the default branch (PR required, signed commits, no force-push, no deletion):
  gh api -X POST 'repos/{{owner}}/{{repo}}/rulesets' --input '{ruleset}'
  # Monitor (Claude Code or Codex):
  python3 .claude/hooks/dash.py up --vendor claude   # or --vendor codex
Then start Claude Code, Antigravity, or Codex in the repo and run: /spec <your idea>"""


def create_labels(target: Path, *, dry: bool = False, human_review: bool = False) -> list[str]:
    """Create the standard agency labels on GitHub via agent-gh (or gh fallback)."""
    labels = [*AGENCY_LABELS, HUMAN_REVIEW_LABEL] if human_review else list(AGENCY_LABELS)
    if dry:
        return [name for name, _, _ in labels]
    gh_bin = shutil.which("agent-gh") or shutil.which("gh")
    if gh_bin is None:
        raise RuntimeError("neither agent-gh nor gh was found on PATH")
    created: list[str] = []
    for name, color, desc in labels:
        cmd = [gh_bin, "label", "create", name, "--color", color, "--description", desc, "--force"]
        proc = subprocess.run(cmd, cwd=target, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            err = (proc.stderr.strip() or proc.stdout.strip() or "failed").splitlines()[-1]
            raise RuntimeError(f"{gh_bin} label create {name}: {err}")
        created.append(name)
    return created


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    p.add_argument("target", type=Path)
    p.add_argument("--force", action="store_true", help="overwrite harness files that differ")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--owner", help="GitHub @user or @org/team for .github/CODEOWNERS")
    p.add_argument(
        "--vendors",
        default="all",
        help="all, or comma-separated subset of claude,antigravity,codex (default: all)",
    )
    p.add_argument(
        "--create-labels",
        action="store_true",
        help="create standard GitHub issue/PR labels via agent-gh (or gh)",
    )
    p.add_argument(
        "--human-review",
        action="store_true",
        help="with --create-labels: also create the needs-human-review label",
    )
    a = p.parse_args(argv)
    vendors = parse_vendors(a.vendors)
    target = a.target.resolve()
    try:
        res = install(target, a.force, a.dry_run, a.owner, vendors)
    except UnsafePath as exc:
        print(f"refusing to install: {exc}", file=sys.stderr)
        return 1
    prefix = "would write" if a.dry_run else "wrote"
    for f in res.written:
        print(f"{prefix}: {f}")
    for f in res.skipped:
        print(f"skipped: {f}")
    for n in res.notes:
        print(f"NOTE: {n}")
    if a.create_labels:
        try:
            labels = create_labels(target, dry=a.dry_run, human_review=a.human_review)
        except RuntimeError as exc:
            print(f"label setup failed: {exc}", file=sys.stderr)
            return 1
        lbl_prefix = "would create label" if a.dry_run else "created label"
        for name in labels:
            print(f"{lbl_prefix}: {name}")
    print(NEXT_STEPS.format(ruleset=HARNESS / "templates" / "ruleset.json"))
    return 2 if res.unmerged else 0


if __name__ == "__main__":
    sys.exit(main())
