---
name: full-auto
description: Opt-in one-shot autonomous mode: research KB & codebase, author spec + ready-for-agent tickets without pausing, drain the queue via /next-all, and finish with /retro and STATUS.md. User-invoked only.
disable-model-invocation: true
argument-hint: "<idea or challenge> [--max N]"
---

One-shot `$ARGUMENTS` from idea to merged PRs and retrospective — **only when explicitly invoked by the maintainer**. Never default to this mode on normal `/spec` or `/next` requests.

## 1. Research & Design (no grill pause)
1. **Consult the knowledge base:** run `agency-kb search "<keywords>" --top 5` (or `python3 .claude/hooks/kb.py search ...`) to pull in proven patterns, library notes, and past pitfalls.
2. **Scout the repo:** send `scout` to map the relevant modules, test harness, and verify command.
3. **Make explicit, conservative decisions:** only pause to ask the maintainer if the request is genuinely contradictory or touches destructive/production/IaC/secrets boundaries. Otherwise, choose the simplest idiomatic design and document every assumption and trade-off in the spec issue.

## 2. Spec & Ready Tickets (straight to `ready-for-agent`)
1. Create one GitHub issue labelled `spec` (≤60 lines: goal, non-goals, decisions + why, assumptions, interfaces, risks).
2. Slice into `S`/`M` vertical-slice tickets using `.github/ISSUE_TEMPLATE/ticket.md` (testable acceptance criteria, verify command, expected files, `Blocked by #n`, size).
3. Label every ticket **`ready-for-agent`** immediately (skipping the manual approval pause) and link all tickets from the spec issue.
4. Update `STATUS.md` and emit a 3-line summary of the spec + ticket count before dispatching.

## 3. Execute (`/next-all`)
Run `/next-all` (default cap `10`, or `--max N` if supplied in `$ARGUMENTS`), obeying **every standard safety rail**:
- Isolated worktrees + offline `agent-box` verify (when available) + fresh-context `reviewer` + `gtq.py verdict` logging.
- Respect `agent-git` / `agent-gh` per-repo policy (`autonomous` squash-merge vs `human-review` draft PRs capped at 3).
- Halt immediately on protected-path violations, destructive/CI/production asks, 2× `reviewer` `FAIL`, `BLOCKED:`, ~150k context budget (run `/handoff`), or Claude Code's 90% subscription usage stop.

## 4. Retrospective & Closeout (`/retro`)
Once all tickets in the spec are shipped (or the run halts cleanly):
1. Run `/retro` to record reusable patterns, library notes, and pitfalls into `~/.agency-kb/` and capture `gtq.py metrics`.
2. Rewrite `STATUS.md` (≤40 lines) with final state.
3. Report in ≤10 lines: spec link, PRs merged (or awaiting review), first-pass reviewer rate, KB notes added, and any remaining `needs-human` items.
