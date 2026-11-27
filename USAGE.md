# groundhog-agency — Complete Usage Guide

`groundhog-agency` works **standalone on any macOS or Linux machine** (only Python 3.9+, `git`, and `gh` required) and optionally upgrades to **Groundhog**'s scoped GitHub App broker (`agent-git`/`agent-gh`) and offline `agent-box` containers when present.

---

## 1. Installation

### Standalone (no Groundhog required)

```bash
git clone https://github.com/gofastercloud/groundhog-agency.git ~/groundhog-agency

# Optional: link global skills into Claude Code, Antigravity, and Codex
python3 ~/groundhog-agency/install.py --link-skills

# Onboard a target repository (writes harness, checks readiness, creates labels & ruleset)
python3 ~/groundhog-agency/install.py /path/to/your/repo --ship
```

In standalone mode (`git` + `gh` with your personal login), `--ship` opens a **draft PR** for human review.

### With Groundhog (recommended hardened setup)

Enable `agent_git_enabled: true` and `agency_enabled: true` in Groundhog's `custom/config.yml`, then run:

```nu
./groundhog ansible --tags "dev-tools,agent-git,agency"
agency-install /path/to/your/repo --ship
```

With Groundhog installed, `agency-install` automatically uses `agent-git`/`agent-gh` (1-hour single-repo GitHub App tokens + dedicated signing-only SSH key), detects `autonomous` vs `human-review` repo policies, and `implementer`/`reviewer` default to offline `agent-box` containers.

---

## 2. Core Workflow

| Command | What it does |
|---|---|
| `/spec <idea>` | Consults `~/.agency-kb` for past patterns/pitfalls, grills you (≤10 Qs), creates a `spec` issue + small vertical-slice tickets (`S`/`M`, acceptance criteria, expected files, blockers). |
| `/next [N]` | Claims up to `N` (default 2, max 3) unblocked, file-disjoint `ready-for-agent` tickets → spawns `implementer`s in isolated worktrees → fresh-context `reviewer` grades each diff → logs verdict → ships PR. |
| `/next-all [max]` | Runs `/next` continuously batch-by-batch (default cap 10) until the backlog is drained, a blocker or human-review cap (3 draft PRs) is hit, or context/usage limits trigger. |
| `/retro [scope]` | Reviews recent PRs and `.agency/verdicts.csv`, distils reusable patterns, library notes, and pitfalls into `~/.agency-kb/`, and suggests `AGENTS.md` updates. |
| `/handoff` | Rewrites `STATUS.md` (≤40 lines), prints today's spend, and resets cleanly for a fresh session. |

---

## 3. Cross-Project Knowledge Base ("Second Brain")

Learnings live as plain, git-friendly Markdown files with YAML frontmatter in `~/.agency-kb/` (override with `AGENCY_KB_DIR`), searchable offline via Okapi BM25 (`kb.py` — zero vector DB, zero API keys):

```bash
# Search before starting a feature (also done automatically by /spec)
python3 .claude/hooks/kb.py search "ffmpeg sidechain ducking" --top 5

# Add a learning manually (or let /retro do it after a build)
python3 .claude/hooks/kb.py add \
  --kind library \
  --title "vhs Catppuccin Mocha terminal recordings" \
  --tags "cli,video,vhs" \
  --project demo-builder \
  --body "Use Set Theme Catppuccin Mocha and Sleep 500ms after Enter for clean pacing."

# List recent notes
python3 .claude/hooks/kb.py list --kind pitfall
```

Categories: `pattern`, `library`, `pitfall`, `retro`. You can `git init ~/.agency-kb` and sync it to a private repo if you want your second brain across machines.

---

## 4. Factory Quality Metrics & Spend Reports

Every reviewer verdict is logged to `.agency/verdicts.csv`, and Claude Code session spend is logged to `.agency/ledger.csv`:

```bash
# First-pass reviewer rate, retry recovery, escalations, and avg cost per ticket
python3 .claude/hooks/gtq.py metrics

# Token & API-equivalent spend breakdown
python3 .claude/hooks/gtq.py report --days 7 --by agent_type
python3 .claude/hooks/gtq.py report --ledger .agency/ledger.csv --by ticket
```

---

## 5. Live Terminal Dashboard

```bash
python3 .claude/hooks/dash.py up --vendor claude             # tmux: Claude Code left, dashboard right
python3 .claude/hooks/dash.py up --vendor claude --code-mode # experimental Code Mode (Claude Code 2.1.323+)
python3 .claude/hooks/dash.py up --vendor codex              # tmux: Codex left, dashboard right
python3 .claude/hooks/dash.py --once                         # one snapshot
```

---

## 6. Installer Flags Reference

- `--dry-run`: preview files, readiness checks, labels, ruleset, and PR without changing anything.
- `--offline`: file-only install (no GitHub API calls).
- `--vendors all|claude,antigravity,codex`: install only selected vendor adapters.
- `--owner @user`: explicit CODEOWNERS handle (auto-detected from `origin` when omitted).
- `--human-review`: force 1 required approval + CODEOWNERS review + `needs-human-review` label.
- `--no-labels` / `--no-ruleset`: skip individual GitHub setup steps.
- `--link-skills`: symlink skills globally into `~/.claude/skills/`, `~/.gemini/antigravity/skills/`, and `~/.codex/skills/`.
- `--ship`: commit, push, and open (or squash-merge on autonomous repos) the install PR.
