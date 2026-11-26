#!/usr/bin/env python3
"""Install the groupthinQ harness into a git repository.

    python3 install.py /path/to/repo [--owner @you] [--force] [--dry-run]

Copies agents, skills, hooks and templates. Existing files are never
overwritten without --force. An existing .claude/settings.json is never
touched, even with --force: the harness settings are written next to it and
the installer exits 2, because until you merge them the permission rules,
sandbox and subagent guard are off. Refuses to write through symlinks or
outside the target. Runs no network or gh commands.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "harness"
MARKER = "<!-- groupthinQ:start -->"
OWNER_RE = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9._-]+)?")

# (source relative to harness/, destination relative to the target repo)
FILES: list[tuple[str, str]] = [
    (".claude/agents/implementer.md", ".claude/agents/implementer.md"),
    (".claude/agents/reviewer.md", ".claude/agents/reviewer.md"),
    (".claude/agents/scout.md", ".claude/agents/scout.md"),
    (".claude/skills/spec/SKILL.md", ".claude/skills/spec/SKILL.md"),
    (".claude/skills/next/SKILL.md", ".claude/skills/next/SKILL.md"),
    (".claude/skills/handoff/SKILL.md", ".claude/skills/handoff/SKILL.md"),
    (".claude/hooks/gtq.py", ".claude/hooks/gtq.py"),
    (".claude/hooks/dash.py", ".claude/hooks/dash.py"),
    (".claude/groupthinq.md", ".claude/groupthinq.md"),
    ("templates/ticket.md", ".github/ISSUE_TEMPLATE/ticket.md"),
]
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


def install(target: Path, force: bool = False, dry: bool = False, owner: str | None = None) -> Result:
    if not (target / ".git").exists():
        raise SystemExit(f"{target} is not a git repository")
    if owner is not None and not OWNER_RE.fullmatch(owner):
        raise SystemExit(f"--owner must look like @user or @org/team, not {owner!r}")
    res = Result()
    for src_rel, dst_rel in FILES:
        _copy(HARNESS / src_rel, target / dst_rel, dst_rel, force, dry, res, target)
    for src_rel, dst_rel in CREATE_IF_MISSING:
        _copy(HARNESS / src_rel, target / dst_rel, dst_rel, False, dry, res, target)

    settings_src = HARNESS / ".claude" / "settings.json"
    settings_dst = target / ".claude" / "settings.json"
    if settings_dst.exists() and settings_dst.read_bytes() == settings_src.read_bytes():
        pass  # already installed
    elif settings_dst.exists():
        side = ".claude/settings.groupthinq.json"
        _copy(settings_src, target / side, side, True, dry, res, target)
        res.unmerged = True
        res.notes.append(
            "PROTECTIONS OFF: your existing .claude/settings.json was left unchanged, so the "
            "harness permission rules, sandbox, cost settings and hooks (including the subagent "
            "guard) are not active. Merge .claude/settings.groupthinq.json into it by hand "
            "(env, permissions, sandbox, hooks), then delete it and re-run the installer."
        )
    else:
        _copy(settings_src, settings_dst, ".claude/settings.json", False, dry, res, target)

    claude_md = target / "CLAUDE.md"
    _check_dest(target, claude_md)
    block = (HARNESS / "templates" / "claude-block.md").read_text(encoding="utf-8")
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if MARKER not in existing:
        if not dry:
            text = existing.rstrip() + "\n" + block if existing else block.lstrip()
            _write_atomic(claude_md, text.encode("utf-8"))
        res.written.append("CLAUDE.md (compact instructions block)")

    codeowners = target / ".github" / "CODEOWNERS"
    _check_dest(target, codeowners)
    if owner is None:
        res.notes.append("No --owner given: .github/CODEOWNERS not written. Re-run with --owner @you.")
    elif codeowners.exists():
        if "/.claude/" not in codeowners.read_text(encoding="utf-8"):
            res.notes.append(
                "Existing .github/CODEOWNERS left unchanged: add the lines from "
                "harness/templates/CODEOWNERS for .claude/, .github/workflows/ and CLAUDE.md."
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
  gh label create ready-for-agent --color 5BD4F5 --description "Implementer may pick this up"
  gh label create in-progress     --color FACC15 --description "Claimed by an implementer"
  gh label create needs-human     --color FB923C --description "Waiting on the maintainer"
  gh label create spec            --color 8B5CF6
  # Implementer worktrees start without untracked files: list any they need
  # (e.g. .env.example, local config) in .worktreeinclude.
  # Protect the default branch: changes only via PR, no force-push, no deletion.
  gh api -X POST 'repos/{{owner}}/{{repo}}/rulesets' --input '{ruleset}'
  # Approvals are 0 because PRs are opened with your own login and GitHub won't let
  # you approve your own PR. Raise to 1 (and require code-owner review) once a bot
  # account or GitHub App opens the PRs.
  # Use HTTPS git auth (gh auth setup-git): the sandbox blocks ~/.ssh.
  # Monitor: python3 .claude/hooks/dash.py up   (tmux: Claude Code left, dashboard right)
Then start Claude Code in the repo and run: /spec <your idea>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    p.add_argument("target", type=Path)
    p.add_argument("--force", action="store_true", help="overwrite harness files that differ")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--owner", help="GitHub @user or @org/team for .github/CODEOWNERS")
    a = p.parse_args(argv)
    try:
        res = install(a.target.resolve(), a.force, a.dry_run, a.owner)
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
    print(NEXT_STEPS.format(ruleset=HARNESS / "templates" / "ruleset.json"))
    return 2 if res.unmerged else 0


if __name__ == "__main__":
    sys.exit(main())
