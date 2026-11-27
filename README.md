# groundhog-agency

A minimal, vendor-agnostic, cost-aware software-agency harness tightly coupled to [Groundhog](https://github.com/gofastercloud/groundhog) and its `agent-git` / `agent-gh` GitHub App broker. Works across **Claude Code**, **Antigravity**, and **Codex**.

One maintainer, one orchestrator session, and three focused roles (`implementer`, `reviewer`, `scout`) work GitHub Issues through to verified PRs — respecting each repository's `agent-git` policy (`autonomous` squash-merge vs `human-review` draft PR).

```
/spec <idea>   →  spec issue + vertical-slice tickets (GitHub Issues)
/next          →  implementer (worktree, agent-git) → reviewer → PR (auto-merge or human-review)
/handoff       →  STATUS.md (≤40 lines) → start fresh
```

## Dependencies (managed by Groundhog)

All runtime and dev dependencies (`python3`, `uv`, `ruff`, `pyright`, `gh`, `tmux`, `agent-git`, `agent-gh`, and agent skill links) are managed by Groundhog's `agent_git` and `agency` Ansible roles:

```nu
# In your Groundhog repo (enable agency_enabled: true in custom/config.yml):
./groundhog ansible --tags "agent-git,agency"
```

## Install into a target repo

```nu
# Full setup (files, CODEOWNERS, GitHub labels, signed-commit branch ruleset, App check):
agency-install /path/to/your/repo --dry-run
agency-install /path/to/your/repo

# Same, and commit + open/merge the setup PR via agent-git/agent-gh:
agency-install /path/to/your/repo --ship

# Offline / file-only install (no GitHub API calls):
agency-install /path/to/your/repo --owner @you --offline

# Restrict to specific AI vendors:
agency-install /path/to/your/repo --vendors claude,antigravity,codex
```

`install.py` auto-detects `owner/repo` from `origin` and the repo's policy (`autonomous` vs `human-review`) from `~/.config/groundhog/agent-git.json`, writes the shared `.agents/` core and `.claude/` adapter, creates all standard labels, and reconciles the `main` branch ruleset (`required_signatures`, PR required, no force-push/deletion, plus 1 approval + CODEOWNERS review on `human-review` repos). It never overwrites an existing `.claude/settings.json` (writes `settings.groundhog-agency.json` and exits `2` until merged) and refuses symlinks/hardlinks.

## Architecture

| Layer | Path | Purpose |
|---|---|---|
| **Vendor-agnostic core** | `.agents/orchestrator.md`, `.agents/roles/{implementer,reviewer,scout}.md`, `.agents/skills/{spec,next,handoff}/SKILL.md`, `AGENTS.md` | Shared workflow, role contracts, and Agent Skills used by Antigravity, Codex, and Claude Code |
| **Claude Code adapter** | `.claude/agents/*.md`, `.claude/skills/*`, `.claude/hooks/{gtq.py,dash.py}`, `.claude/settings.json` | Cost controls, OS sandbox (`~/.ssh` denied), `pretool` guard, subscription usage stop, and spend ledger |
| **GitHub App broker** | Groundhog `agent-git` / `agent-gh` | 1-hour single-repo tokens, dedicated signing key in `~/.config/groundhog/`, `agent/*` branch enforcement, and per-repo merge policy |

## Monitor & Spend Report

```nu
python3 .claude/hooks/dash.py up --vendor claude             # tmux: Claude Code left, dashboard right
python3 .claude/hooks/dash.py up --vendor claude --code-mode # experimental Code Mode (Claude Code 2.1.323+)
python3 .claude/hooks/dash.py up --vendor codex              # tmux: Codex left, dashboard right
python3 .claude/hooks/dash.py --once                         # one snapshot

python3 .claude/hooks/gtq.py report --days 7 --by agent_type
python3 .claude/hooks/gtq.py report --ledger .agency/ledger.csv --by ticket
```

## Develop

```nu
uv sync; make verify    # ruff format, ruff check, pyright, pytest
```

MIT licensed.
