#!/usr/bin/env python3
"""Install the groundhog-agency harness into a git repository.

    python3 install.py /path/to/repo [--owner @you] [--offline] [--ship] [--force] [--dry-run]

Copies vendor-agnostic skills, roles, and templates, plus optional Claude Code
hooks/settings. When the target has a GitHub `origin` remote (and --offline is
not set), automatically detects the owner and Groundhog agent-git repo policy,
verifies GitHub App access, creates the standard labels, and reconciles the
default-branch ruleset (PR required, signed commits, no force-push/deletion).
Existing files are never overwritten without --force. An existing
.claude/settings.json is never touched, even with --force: the harness settings
are written next to it and the installer exits 2. Refuses symlinks/hardlinks.
"""

from __future__ import annotations

import argparse
import json
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
_GITHUB_REMOTE_PATTERNS = (
    re.compile(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$"),
    re.compile(r"^https?://(?:[^@]+@)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$"),
)

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
    ("core/skills/next-all/SKILL.md", ".agents/skills/next-all/SKILL.md"),
    ("core/skills/retro/SKILL.md", ".agents/skills/retro/SKILL.md"),
    ("core/skills/handoff/SKILL.md", ".agents/skills/handoff/SKILL.md"),
    (".claude/hooks/kb.py", ".agents/kb.py"),
]

# Claude Code adapter files (.claude/).
CLAUDE_FILES: list[tuple[str, str]] = [
    (".claude/agents/implementer.md", ".claude/agents/implementer.md"),
    (".claude/agents/reviewer.md", ".claude/agents/reviewer.md"),
    (".claude/agents/scout.md", ".claude/agents/scout.md"),
    ("core/skills/spec/SKILL.md", ".claude/skills/spec/SKILL.md"),
    ("core/skills/next/SKILL.md", ".claude/skills/next/SKILL.md"),
    ("core/skills/next-all/SKILL.md", ".claude/skills/next-all/SKILL.md"),
    ("core/skills/retro/SKILL.md", ".claude/skills/retro/SKILL.md"),
    ("core/skills/handoff/SKILL.md", ".claude/skills/handoff/SKILL.md"),
    (".claude/hooks/gtq.py", ".claude/hooks/gtq.py"),
    (".claude/hooks/dash.py", ".claude/hooks/dash.py"),
    (".claude/hooks/kb.py", ".claude/hooks/kb.py"),
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


def parse_github_url(url: str) -> str | None:
    cleaned = url.strip()
    for pat in _GITHUB_REMOTE_PATTERNS:
        m = pat.match(cleaned)
        if m:
            return m.group(1)
    return None


def detect_github_repo(target: Path) -> str | None:
    """Return `owner/repo` from `origin` remote, or None if absent/non-GitHub."""
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return parse_github_url(proc.stdout)


def load_agent_git_config() -> dict[str, object]:
    env_path = os.environ.get("GROUNDHOG_AGENT_GIT_CONFIG")
    cfg_path = (
        Path(env_path).expanduser()
        if env_path
        else (Path.home() / ".config" / "groundhog" / "agent-git.json")
    )
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def detect_repo_policy(slug: str | None, cfg: dict[str, object], human_review: bool = False) -> str:
    if human_review:
        return "human-review"
    if not slug:
        return "autonomous"
    raw = cfg.get("repo_policies")
    if isinstance(raw, dict):
        pol = {str(k).lower(): str(v) for k, v in raw.items()}.get(slug.lower())
        if pol == "human-review":
            return "human-review"
    return "autonomous"


def ruleset_payload(policy: str) -> dict[str, object]:
    is_hr = policy == "human-review"
    return {
        "name": "main-human-review" if is_hr else "main-autonomous",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
        ],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_signatures"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1 if is_hr else 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": is_hr,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
        ],
    }


def check_agent_app_access(target: Path, slug: str, cfg: dict[str, object]) -> tuple[bool, str]:
    agent_gh = shutil.which("agent-gh")
    if not agent_gh:
        return False, "agent-gh not found on PATH (enable agent_git_enabled in Groundhog)"
    proc = subprocess.run(
        [agent_gh, "repo", "view", slug, "--json", "nameWithOwner"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode == 0:
        return True, f"agent-gh verified access to {slug}"
    inst_id = str(cfg.get("installation_id") or "").strip()
    url = f"https://github.com/settings/installations/{inst_id}" if inst_id else "GitHub App settings"
    return False, f"agent-gh cannot access {slug} yet — add it at {url}"


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


def ensure_ruleset(slug: str, policy: str, *, dry: bool = False) -> str:
    """Create or update the default-branch ruleset via human `gh` (admin scope)."""
    payload = ruleset_payload(policy)
    name = str(payload["name"])
    if dry:
        return f"would ensure ruleset {name} on {slug} ({policy})"
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise RuntimeError("gh was not found on PATH (needed for branch rulesets)")
    existing_proc = subprocess.run(
        [gh_bin, "api", f"repos/{slug}/rulesets"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if existing_proc.returncode != 0:
        err = (existing_proc.stderr.strip() or existing_proc.stdout.strip() or "failed").splitlines()[-1]
        raise RuntimeError(f"gh api repos/{slug}/rulesets: {err}")
    existing = json.loads(existing_proc.stdout or "[]")
    match_id: int | None = None
    for item in existing if isinstance(existing, list) else []:
        if isinstance(item, dict) and item.get("name") in {name, "main-autonomous", "main-human-review"}:
            rid = item.get("id")
            if isinstance(rid, int):
                match_id = rid
                break
    method = "PUT" if match_id is not None else "POST"
    endpoint = f"repos/{slug}/rulesets/{match_id}" if match_id is not None else f"repos/{slug}/rulesets"
    proc = subprocess.run(
        [gh_bin, "api", "-X", method, endpoint, "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr.strip() or proc.stdout.strip() or "failed").splitlines()[-1]
        raise RuntimeError(f"gh api -X {method} {endpoint}: {err}")
    resp = json.loads(proc.stdout or "{}")
    rid = resp.get("id", match_id or "?")
    verb = "updated" if match_id is not None else "created"
    return f"{verb} ruleset {name} (#{rid}) on {slug}"


def check_readiness(target: Path) -> list[tuple[str, str]]:
    """Agent-Readiness pre-flight check on the target repository."""
    checks: list[tuple[str, str]] = []
    has_verify = False
    makefile = target / "Makefile"
    if makefile.is_file() and re.search(
        r"^(?:verify|test|check):", makefile.read_text(errors="replace"), re.M
    ):
        has_verify = True
        checks.append(("ok", "Makefile verify/test target found"))
    elif (target / "pyproject.toml").is_file() or (target / "package.json").is_file():
        has_verify = True
        checks.append(
            ("ok", "Project manifest found (ensure a fast verify command is documented in AGENTS.md)")
        )
    if not has_verify:
        checks.append(("warn", "No Makefile/pyproject.toml/package.json found — add a fast verify command"))

    if (target / ".worktreeinclude").is_file():
        checks.append(("ok", ".worktreeinclude present"))
    else:
        checks.append(("info", "No .worktreeinclude (add one if worktrees need untracked local files)"))

    if shutil.which("agent-git") and shutil.which("agent-gh"):
        checks.append(("ok", "Groundhog agent-git/agent-gh detected (scoped GitHub App mode)"))
    elif shutil.which("gh"):
        checks.append(("info", "Standalone mode (using git/gh; draft PRs recommended with personal login)"))
    else:
        checks.append(("warn", "Neither agent-gh nor gh found on PATH"))

    if shutil.which("agent-box"):
        checks.append(("ok", "Groundhog agent-box detected (offline container verification)"))
    return checks


def link_user_skills(*, dry: bool = False) -> list[str]:
    """Symlink canonical agency skills into Claude Code, Antigravity, and Codex (standalone helper)."""
    skill_names = ("spec", "next", "next-all", "retro", "handoff")
    skill_dirs = (
        Path.home() / ".claude" / "skills",
        Path.home() / ".gemini" / "antigravity" / "skills",
        Path.home() / ".gemini" / "config" / "skills",
        Path.home() / ".codex" / "skills",
    )
    linked: list[str] = []
    for base in skill_dirs:
        if not dry:
            base.mkdir(parents=True, exist_ok=True)
        for name in skill_names:
            src = HARNESS / "core" / "skills" / name
            dst = base / f"agency-{name}"
            if not dry:
                if dst.is_symlink() or dst.exists():
                    dst.unlink()
                dst.symlink_to(src)
            linked.append(str(dst))
    return linked


def ship_install_pr(target: Path, policy: str, *, dry: bool = False) -> str:
    """Commit, push, open PR (and squash-merge on autonomous repos) via agent-git/agent-gh or git/gh."""
    branch = "agent/setup/groundhog-agency"
    git_bin = shutil.which("agent-git") or shutil.which("git")
    gh_bin = shutil.which("agent-gh") or shutil.which("gh")
    using_agent_broker = bool(shutil.which("agent-git") and shutil.which("agent-gh"))
    effective_policy = policy if using_agent_broker else "human-review"
    if dry:
        action = "open draft PR" if effective_policy == "human-review" else "open and squash-merge PR"
        return f"would commit on {branch} and {action}"
    if not git_bin or not gh_bin:
        raise RuntimeError("--ship requires git and gh (or agent-git and agent-gh) on PATH")
    env = os.environ | {"AGENT_NAME": "Groundhog Agency Installer"}

    def _step(cmd: list[str]) -> str:
        proc = subprocess.run(cmd, cwd=target, env=env, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            chunks = [s.strip() for s in (proc.stdout, proc.stderr) if s.strip()]
            lines = "\n".join(chunks).splitlines()
            detail = "\n".join(lines[-20:]) if lines else "failed"
            prefix = f"{' '.join(cmd[:3])}: "
            raise RuntimeError(f"{prefix}\n{detail}" if "\n" in detail else f"{prefix}{detail}")
        return proc.stdout.strip()

    _step([git_bin, "switch", "-C", branch])
    _step([git_bin, "add", "-A"])
    status = _step([git_bin, "status", "--porcelain"])
    if not status:
        return "nothing new to commit"
    _step([git_bin, "commit", "-m", "chore: install groundhog-agency harness"])
    _step([git_bin, "push", "-u", "origin", branch])
    pr_cmd = [
        gh_bin,
        "pr",
        "create",
        "--label",
        "agent-authored",
        "--title",
        "chore: install groundhog-agency harness",
        "--body",
        "Installs the vendor-agnostic groundhog-agency harness (.agents/, .claude/, AGENTS.md, CODEOWNERS).",
    ]
    if effective_policy == "human-review":
        pr_cmd += ["--draft"]
        if policy == "human-review":
            pr_cmd += ["--label", "needs-human-review"]
    pr_url = _step(pr_cmd)
    if effective_policy == "human-review":
        return f"opened draft PR for human review: {pr_url}"
    _step([gh_bin, "pr", "merge", "--squash", "--delete-branch"])
    _step([git_bin, "switch", "main"])
    _step([git_bin, "pull", "--ff-only", "--prune"])
    return f"merged harness PR: {pr_url}"


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
    existing = {line.strip().strip("/") for line in lines}
    added_ignores: list[str] = []
    for entry in (".agency/", ".worktrees/"):
        if entry.strip("/") not in existing:
            lines.append(entry)
            added_ignores.append(entry)
    if added_ignores:
        if not dry:
            _write_atomic(gitignore, ("\n".join(lines) + "\n").encode("utf-8"))
        res.written.append(f".gitignore ({', '.join(added_ignores)})")
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    p.add_argument("target", type=Path, nargs="?", help="Target git repository path")
    p.add_argument("--force", action="store_true", help="overwrite harness files that differ")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--owner", help="GitHub @user or @org/team (auto-detected from origin if omitted)")
    p.add_argument(
        "--vendors",
        default="all",
        help="all, or comma-separated subset of claude,antigravity,codex (default: all)",
    )
    p.add_argument("--offline", action="store_true", help="skip all GitHub label, ruleset, and App checks")
    p.add_argument("--no-labels", action="store_true", help="skip creating GitHub labels")
    p.add_argument("--no-ruleset", action="store_true", help="skip reconciling the default-branch ruleset")
    p.add_argument("--human-review", action="store_true", help="force human-review policy (1 approval)")
    p.add_argument(
        "--link-skills",
        action="store_true",
        help="symlink agency skills into ~/.claude, ~/.gemini/antigravity, and ~/.codex (standalone)",
    )
    p.add_argument(
        "--ship",
        action="store_true",
        help="commit, push, and open (or merge) the install PR via agent-git/agent-gh or git/gh",
    )
    a = p.parse_args(argv)
    if a.link_skills:
        links = link_user_skills(dry=a.dry_run)
        lbl = "would link skill" if a.dry_run else "linked skill"
        for link in links:
            print(f"{lbl}: {link}")
        if a.target is None:
            return 0
    if a.target is None:
        p.error("target repository path is required (unless using --link-skills alone)")
    vendors = parse_vendors(a.vendors)
    target = a.target.resolve()
    slug = None if a.offline else detect_github_repo(target)
    owner = a.owner or (f"@{slug.split('/', 1)[0]}" if slug else None)
    cfg = {} if a.offline else load_agent_git_config()
    policy = detect_repo_policy(slug, cfg, a.human_review)

    try:
        res = install(target, a.force, a.dry_run, owner, vendors)
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

    print("Readiness pre-flight:")
    for level, msg in check_readiness(target):
        print(f"  [{level}] {msg}")

    labels_done = False
    ruleset_done = False
    if slug and not a.offline:
        print(f"repo: {slug} (policy: {policy})")
        if not a.dry_run:
            ok, msg = check_agent_app_access(target, slug, cfg)
            print(f"{'ok' if ok else 'NOTE'}: {msg}")
        if not a.no_labels:
            try:
                labels = create_labels(target, dry=a.dry_run, human_review=(policy == "human-review"))
            except RuntimeError as exc:
                print(f"NOTE: label setup skipped ({exc})", file=sys.stderr)
            else:
                lbl_prefix = "would create label" if a.dry_run else "created label"
                for name in labels:
                    print(f"{lbl_prefix}: {name}")
                labels_done = True
        if not a.no_ruleset:
            try:
                rs_msg = ensure_ruleset(slug, policy, dry=a.dry_run)
            except RuntimeError as exc:
                print(f"NOTE: ruleset setup skipped ({exc})", file=sys.stderr)
            else:
                print(rs_msg)
                ruleset_done = True

    if a.ship:
        if res.unmerged:
            print(
                "NOTE: skipping --ship because .claude/settings.groundhog-agency.json is unmerged. "
                "Merge it into .claude/settings.json, delete .claude/settings.groundhog-agency.json, "
                "then re-run with --ship.",
                file=sys.stderr,
            )
            return 2
        try:
            print(ship_install_pr(target, policy, dry=a.dry_run))
        except RuntimeError as exc:
            print(f"ship failed: {exc}", file=sys.stderr)
            return 1

    if not labels_done and not ruleset_done:
        print(NEXT_STEPS.format(ruleset=HARNESS / "templates" / "ruleset.json"))
    else:
        print("Ready! Start Claude Code, Antigravity, or Codex in the repo and run: /spec <your idea>")
        print("Monitor: agency-dash up --vendor claude   # or --vendor codex")
    return 2 if res.unmerged else 0


if __name__ == "__main__":
    sys.exit(main())
