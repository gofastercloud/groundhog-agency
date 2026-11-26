# groupthinQ

A minimal, cost-aware software-agency harness for [Claude Code](https://code.claude.com). One human, one orchestrator session, and three small subagents work through GitHub Issues: spec, implement, review, draft PR, and a human merges.

Everything runs inside Claude Code. The harness is markdown, JSON settings and one stdlib Python file.

```
/spec <idea>   →  tickets (GitHub Issues)
/next          →  implementer (worktree) → reviewer → draft PR
you            →  merge
/handoff       →  STATUS.md, then /clear
```

## Install

Requirements: Claude Code 2.1.311 or later, `gh` authenticated, `python3` 3.9 or later on `PATH` (macOS's built-in one is fine), a git repo with a verify command (e.g. `make verify`), and optionally `tmux` for the monitor layout.

```bash
python3 install.py /path/to/your/repo --owner @you --dry-run
python3 install.py /path/to/your/repo --owner @you
```

The installer copies agents, skills, hooks and templates. It skips files you've changed (`--force` overwrites them) and refuses to write through symlinks. It never touches an existing `.claude/settings.json`, even with `--force`: it writes `settings.groupthinq.json` next to it and exits 2, because until you merge it the permission rules, sandbox, cost settings and subagent guard are all off. `--owner` writes `.github/CODEOWNERS` for the harness and CI. It then prints the `gh label create` and branch-ruleset commands to run.

The orchestrator runs on Sonnet with Opus as its advisor (`model` and `advisorModel` in the harness settings); change both there if you want something else.

## What's in it

| Path (in your repo) | Purpose |
|---|---|
| `.claude/agents/implementer.md` | Sonnet, medium effort, own worktree, 40 turns, TDD, commits without pushing |
| `.claude/agents/reviewer.md` | Sonnet, read-only, fresh context, runs verify, PASS/FAIL against the ticket |
| `.claude/agents/scout.md` | Haiku, read-only lookups that return `file:line` pointers |
| `.claude/skills/{spec,next,handoff}` | The workflow |
| `.claude/groupthinq.md` | Orchestrator rules, injected into the main session only |
| `.claude/hooks/gtq.py` | `brief` (SessionStart), `guard` (UserPromptSubmit), `pretool` (PreToolUse: usage stop; subagents can't push, read credentials or edit the harness; the orchestrator can't merge or push protected changes), `posttool` (records the scheduled resume), `statusline`, `ledger` (SessionEnd), `report` (CLI) |
| `.claude/hooks/dash.py` | Terminal dashboard: usage, tickets, agent runs, worktrees, spend. `dash.py up` opens tmux with Claude Code beside it |
| `.claude/settings.json` | Cost controls, permissions, sandbox, hooks, status line |
| `.github/ISSUE_TEMPLATE/ticket.md` | Ticket shape: criteria, verify, files, blockers, size |
| `STATUS.md` | Current work block, at most 40 lines |

[PLAN.md](PLAN.md) explains each cost control and the evidence behind it.

## Monitor

```bash
python3 .claude/hooks/dash.py up       # tmux: Claude Code (orchestrator) left, dashboard right
python3 .claude/hooks/dash.py          # dashboard only, refreshes every 5s
python3 .claude/hooks/dash.py --once   # one snapshot
python3 .claude/hooks/dash.py up --code-mode   # orchestrator in experimental Code Mode (Claude Code 2.1.323+)
```

The dashboard reads local files, plus `gh` and `git` once a minute. It uses no Claude tokens.

## Usage stop

The status line records your Claude subscription usage (5-hour, weekly and any spend limit). When any limit reaches 90% (`GTQ_USAGE_STOP_PERCENT`), the harness pauses:
- new prompts are blocked;
- every tool call in the orchestrator and all subagents is denied;
- the orchestrator schedules a one-off resume for just after the reset, then stops.

Start a prompt with `override:` to work anyway for 30 minutes (not `!override`: a leading `!` is Claude Code's bash mode). Overrides are logged to `.agency/overrides.log`.

The resume is an in-session job, so it only fires while that Claude Code session stays open. Otherwise, the first prompt after the reset just works. If the usage state looks tampered with (a symlink, a directory, bad JSON), the harness stays paused until you delete `.agency/usage.json`.

## Spend report

```bash
python3 .claude/hooks/gtq.py report --days 7 --by agent_type   # this repo, from transcripts
python3 .claude/hooks/gtq.py report --ledger .agency/ledger.csv --by ticket
python3 .claude/hooks/gtq.py report --all --by project          # every project
```

Figures are API list prices. On a subscription plan they are an API-equivalent measure, not your bill.

## Develop

```bash
uv sync && make verify    # ruff, pyright, pytest
```

MIT licensed.
