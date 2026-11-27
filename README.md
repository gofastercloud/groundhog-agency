# groundhog-agency

A minimal, **standalone**, vendor-agnostic, cost-aware autonomous software-agency harness for **Claude Code**, **Antigravity**, and **Codex**.

Works out of the box with plain `git` + `gh` (Python 3.9+ stdlib only, zero external runtime dependencies), and seamlessly upgrades to [Groundhog](https://github.com/gofastercloud/groundhog)'s `agent-git`/`agent-gh` GitHub App broker and offline `agent-box` containers when installed.

```
/spec <idea>     →  consults ~/.agency-kb → grills → spec issue + vertical-slice tickets
/next / /next-all → implementer (worktree) → fresh-context reviewer → verdicts.csv → PR
/retro           →  distils patterns, libraries & pitfalls into ~/.agency-kb (BM25 search)
/handoff         →  STATUS.md (≤40 lines) → start fresh
```

See **[USAGE.md](USAGE.md)** for the full standalone & Groundhog-integrated guide.

## Standalone Quickstart (no Groundhog required)

```bash
git clone https://github.com/gofastercloud/groundhog-agency.git ~/groundhog-agency
python3 ~/groundhog-agency/install.py --link-skills
python3 ~/groundhog-agency/install.py /path/to/your/repo --ship
```

## With Groundhog (hardened workstation + scoped GitHub App)

```nu
# In your Groundhog repo (enable agency_enabled: true in custom/config.yml):
./groundhog ansible --tags "dev-tools,agent-git,agency"
agency-install /path/to/your/repo --ship
```

## What's included

| Component | Path | Purpose |
|---|---|---|
| **Vendor-agnostic core** | `.agents/{orchestrator,roles,skills}`, `AGENTS.md` | Shared `/spec`, `/next`, `/next-all`, `/retro`, `/handoff` skills and role contracts for Claude Code, Antigravity, and Codex |
| **Claude Code adapter** | `.claude/{agents,skills,hooks,settings.json}` | Cost controls, OS sandbox (`~/.ssh` denied), `pretool` guard, 90% subscription usage stop, and spend ledger |
| **Second-brain KB** | `.claude/hooks/kb.py` / `.agents/kb.py` | Offline Okapi BM25 search across `~/.agency-kb/` Markdown notes (`pattern`, `library`, `pitfall`, `retro`) |
| **Factory metrics** | `.claude/hooks/gtq.py metrics` & `report` | First-pass reviewer rate, retry recovery, escalations, and cost per ticket |
| **Live dashboard** | `.claude/hooks/dash.py up` | tmux split with Claude Code or Codex alongside usage bars, tickets, agents, worktrees, and spend |

## Develop

```bash
uv sync && make verify    # ruff format, ruff check, pyright, pytest
```

MIT licensed.
