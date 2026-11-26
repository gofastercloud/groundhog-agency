# groupthinQ: MVP plan

18 Nov 2026. This replaces `Running an agent agency on Claude research brief and plan.md`, which is kept for reference.

## Bottom line

One maintainer, one orchestrator session, three subagents, three skills and four hooks, all inside Claude Code. GitHub Issues and `STATUS.md` hold the state. Every change is reviewed by a fresh-context agent and merged by the human.

The scarce resources are your review time and the orchestrator's context. The design spends neither on anything a cheaper agent or a file can hold.

Baseline from 5 weeks of the author's logs: $867 at API list prices. 66% went to main (orchestrator) sessions running at 400k–967k context, 21% to implementers, and 7% to `general-purpose` agents. The harness targets all three.

## The loop

```
/spec <idea>   grill (max 10 Qs) → spec issue → ticket issues (S/M, acceptance criteria, verify cmd, Blocked by)
/next          pick unblocked tickets (max 3, disjoint files)
                 → implementer (Sonnet, worktree, TDD, commit, no push)
                 → reviewer (Sonnet, fresh context, runs verify, PASS/FAIL)
                 → FAIL: one retry with findings, then needs-human
                 → PASS: push branch, draft PR, label needs-human
you            merge (always a human)
/handoff       STATUS.md ≤40 lines → /clear → next session starts from it
```

## Roles

| Role | Model / effort | Limits | Why |
|---|---|---|---|
| Orchestrator (your session) | Sonnet 5.5, with Opus 5.5 as `advisorModel` | hands off at about 150k tokens; auto-compacts at 300k | Plans and routes; never writes product code. Half Opus's per-token price; the advisor covers the hard calls |
| `implementer` | Sonnet / medium | worktree, 40 turns, no push | Most of the tokens; one ticket per run |
| `reviewer` | Sonnet / medium | read-only tools, 25 turns | Generator never grades itself; skips style nits |
| `scout` | Haiku / low | read-only, no CLAUDE.md, 15 turns | Replaces Explore (Opus on subscriptions) and general-purpose (61k-token start) |

## Cost controls (all in `harness/.claude/settings.json` and the agent files)

| Control | Setting | Waste it targets |
|---|---|---|
| Compact sooner | `CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000` | orchestrator re-reading 400k–967k every turn |
| Hand off at ticket boundaries | `/handoff` skill and guard hook (warns at 150k, insists at 250k) | the same, without waiting for compaction |
| Summarise when idle | `promptCacheTtl: 1h` with idle compaction at ≥100k | cache rebuilt after breaks (up to 951k rewritten) |
| Block a cold resume once | guard hook: past 60 min idle with ≥100k context, blocks one prompt and suggests `/clear` | same, when idle compaction didn't run (machine asleep) |
| No forks | `CLAUDE_CODE_FORK_SUBAGENT=0`, deny `Agent(fork)` | forks run on Opus and carry the full parent context |
| No general-purpose or Explore agents | deny `Agent(general-purpose)`, `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1` | heavy agent start-up and Opus lookups |
| Cheap default for any agent | `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, explicit `model` per agent | agents inheriting Opus |
| Bounded agents | `maxTurns`, `effort` per agent; spawn depth 1; max 3 concurrent | 50-request implementer runs |
| Short subagent cache | `subagentPromptCacheTtl: 5m` | paying 2× writes for agents that never idle |
| No polling | deny `watch`, `sleep`, `gh pr checks --watch`, `gh run watch`; `crossSessionInbound: hold`; goal check-ins off | context re-sent on every poll or wake-up |
| Smaller tool output | `bashOutputMaxChars: 10000`, file reads and MCP output capped at 10k tokens | large outputs carried for the rest of the session |
| Lean prompts | orchestrator rules injected by a SessionStart hook (main session only); CLAUDE.md gains only compact instructions | rules re-read by every subagent |
| No accidental premium | fast mode disabled; workflows advisory-capped at `small` | 2× fast-mode rates; large fan-outs |
| Protect your subscription | status line records usage; at 90% of any limit the hooks block prompts and deny every tool call, and the orchestrator schedules a resume after the reset | agency work eating the usage you need for chat |
| Measure everything | SessionEnd ledger (`.agency/ledger.csv`), `gtq.py report` | spend you can't see won't come down |

## Monitoring

`dash.py up` opens tmux with Claude Code (the orchestrator, the Chief of Staff for this repo) on the left and the dashboard on the right: usage bars, tickets by label, agent runs from the last 30 minutes, worktrees and spend. It's deliberately plain: no server, no port, no tokens. Building Claude Code into the dashboard itself was rejected as too much code for too little gain; tmux gives the real interface for free.

Limits of the usage stop:
- **Freshness:** usage is only as fresh as the last status-line refresh, so it can lag by a turn. 90% leaves room for that.
- **The resume needs the session open:** it uses an in-session `CronCreate` job, so it only fires while that Claude Code session is open and the machine is awake.
- **No subscription, no stop:** if no usage data exists (an API key rather than a subscription), the stop is off.
- **Fails safe:** if the `pretool` hook crashes, it denies the call rather than letting it through.

## Code Mode (experimental, opt-in)

Claude Code 2.1.323 added Code Mode. Claude writes JavaScript that makes several tool calls in one step (in parallel, chained, with output filtered before Claude reads it), run by a tool called `REPL`. Source: code.claude.com/docs/en/code-mode.

**Why it might pay:**
- Each request re-reads the whole context, so collapsing many tool round trips into one should cut cache reads.
- Filtering output in code keeps big results out of the context.

The docs make no savings claim, so treat this as unproven until the ledger shows it.

**How the harness supports it:**
- `dash.py up --code-mode` starts the orchestrator with `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`. Project settings can't turn it on, by design, so the launching shell does. The launcher refuses Claude Code older than 2.1.323.
- Inner tool calls still pass through the permission rules and the `pretool` hook under their usual names. `pretool` already splits compound commands, which is how Code Mode delivers shell calls (`cd 'app' && git push`).
- `pretool` also checks the `REPL` code itself and refuses direct `require`/`import`/`process`/`fetch`/`eval` use. That keeps everything going through the tool helpers the checks can see. It's a tripwire, like the other command checks.
- The guard ignores `source: poll_event`, so it never withholds output from background code.
- The `pretool` hook has `onFailure: "block"`, so a failed or timed-out hook refuses the call. Code Mode does this anyway; now every session does.

**Limits that matter here:**
- Terminal only: not in the Desktop app, `-p`, the SDK or cloud sessions, and no Remote Control.
- A Code Mode session can only be resumed with Code Mode on.
- Code stops after 10 minutes unless run in the background, which needs auto or bypassPermissions mode.
- In auto mode the classifier reviews the code, not each call. The harness doesn't rely on the classifier.
- Whether subagents run in Code Mode isn't documented. Agent teams need 2.1.326.

**Experiment (phase 2):** run the same kind of tickets with and without `--code-mode`, and compare requests, cache reads and cost per merged PR in the ledger. Keep it only if cost drops and the reviewer's first-pass rate holds.

## Memory: what lives where

- **Repo `CLAUDE.md`**: the repo's rules and verify command (under 200 lines), plus the compact instructions block. Every agent reads it.
- **`.claude/groupthinq.md`**: orchestrator rules, injected into the main session only.
- **`STATUS.md`**: the current work block, at most 40 lines, rewritten (not appended) by `/handoff`. This is the cold-start state.
- **GitHub Issues and PRs**: specs, tickets, decisions, review verdicts. This is the durable ledger.
- **`.agency/ledger.csv`**: tokens and dollars per session and per agent run (gitignored).
- **Not used**: auto memory as a ledger (it's machine-local), and chat history.

## Work ordering

1. A ticket fits one fresh implementer session. Bigger than M means split it.
2. Pick tickets whose blockers are closed, in this order: unblockers, then riskiest, then smallest.
3. Run tickets in parallel only when their expected files don't overlap. Run anything touching shared interfaces, schemas, lockfiles or CI on its own.
4. Have no more PRs in flight than you'll review today. The default limit is 3.

## Security

Controls are layered, because prompts alone don't stop a model that has been talked into something. In order of strength:

- **Sandbox** (OS-level; covers every Bash command, so `cat .env` can't sidestep a Read deny rule):
  - on, fails closed if unavailable, and has no unsandboxed escape hatch;
  - can't read `.env*`, keys, `~/.ssh`, `~/.aws`, `~/.git-credentials`, `~/.gnupg`, `~/.kube`, cloud CLI config, `~/.netrc` or registry tokens;
  - can't write `.agency/` (the usage and override state), `.git/hooks`, `.git/config`, `.mcp.json`, CLAUDE.md, or any copy of the harness or CI, including copies inside worktrees;
  - network limited to GitHub and the PyPI/npm registries.
- **Permission rules** (every session; they also cover the Read, Edit and Write tools, which the sandbox doesn't):
  - deny reading credentials;
  - deny editing the harness, `.agency/`, `.git/`, `.mcp.json`, `CLAUDE.local.md` and CODEOWNERS;
  - deny merging, `gh api`/`gist`/`auth`/`secret`/`release`, `git credential`, `printenv`, force-push, deletion and default-branch pushes;
  - ask before CI and CLAUDE.md edits, `gh repo`/`workflow`, `git remote` and package installs.
- **`pretool` hook**, failing closed (`|| exit 2` covers a missing or crashing `python3`):
  - **Orchestrator:** no merge or raw API. Pushes only as `git push -u origin <ticket-branch>`, never to the default branch or HEAD, never forced. The branch's diff is checked mechanically first, and any change to `.claude/`, `.github/`, `.git*`, CLAUDE.md, STATUS.md, `.mcp.json`, symlinks, submodules or file modes is refused. GitHub runs a pushed branch's workflows with repository secrets, so this check has to happen before the push, not at review.
  - **Subagents:** no push, GitHub read-only, no credentials or environment dumps, no writes to protected paths.
  - **Everyone:** the usage stop.
- **Prompt injection:**
  - `/next` works only issues your account opened;
  - agents treat ticket text, code and STATUS.md as data;
  - the reviewer fails diffs outside the ticket's expected files.

**What these controls are not:** the command checks in `pretool` are a tripwire for accidents and naive injections. They are not a boundary, because a shell can always disguise a command (`g=git; $g push`). The boundaries are the sandbox, the permission rules, the credentials an agent can reach, and the GitHub ruleset.

**The residual risk of using your own login:**
- **Your `gh` token can still leak.** `gh` needs its own config inside the sandbox, so a sandboxed command can still use your `gh` token. A subagent fully taken over by injected text could reach GitHub as you.
- **GitHub won't stop a merge.** With 0 required approvals (you can't approve your own PRs), anything holding the token can merge.
- **The fix:** a bot account or GitHub App with a fine-grained token scoped to the one repo (contents, issues and pull requests; no admin, no workflows). Then require 1 approval and code-owner review. Do this before running unattended.

**Your side** (the installer prints these):
- **Protect the default branch:** apply `harness/templates/ruleset.json` (changes only via a PR, no force-push, no deletion, no bypass list). Add a required status check for your CI.
- **Git auth:** use HTTPS through `gh auth setup-git`, since the sandbox blocks `~/.ssh`.
- **CODEOWNERS:** `install.py --owner @you` writes it.

Hooks are stdlib Python and run on Python 3.9 or later (macOS's built-in `python3` is fine), with `-I` so the repo can't shadow the standard library. Their state lives in `.agency/` (0700, written atomically, symlinks and other tampering fail closed), and ledger cells are neutralised against spreadsheet formulas. The installer makes no network calls and refuses symlinked or hard-linked destinations.

**Phase 0 must confirm, in this exact configuration:**
1. Claude Code loads the settings file without complaint (check that `advisorModel`, `crossSessionInbound`, `workflowSizeGuideline` and `subagentPromptCacheTtl` are recognised).
2. Hooks run outside the Bash sandbox, so they can write `.agency/`. If the status line shows "usage not saved", they don't.
3. Subagent hook input carries `agent_id` and `CLAUDE_PROJECT_DIR` points at the main repo inside worktrees.
4. An implementer can create its worktree and commit, `gh` works inside the sandbox, and the orchestrator can push a branch and open a draft PR.
5. The denied commands really are denied.
6. A scheduled resume fires through `UserPromptSubmit`.

## Phases

**Phase 0 (1 evening).** Install on one repo and work through the checklist at the end of the security section. Then do the first `/spec` and three tickets through `/next`.
**Gate:** three PRs merged without hand-written code, and ledger rows for each.

**Phase 1 (2 weeks).** Use it on real work and run `gtq.py report --by agent_type` weekly. Track cost per merged PR, the reviewer's first-pass rate, and your review minutes.
**Gate:** cost per merged PR is known, and main sessions stay under 300k context.

**Phase 2 (only on evidence).** Each of these is one change, measured against the ledger:
- the Sonnet-plus-advisor orchestrator against a plain Opus one, if reviewer first-pass rates or your review minutes get worse;
- implementer `effort: low` on S tickets;
- best-of-2 implementers on tickets that FAILED twice;
- a dynamic workflow for one-off audits or migrations.

Keep a change only if cost per merged PR drops and the revert rate doesn't rise. Re-test the harness on each model release and delete what no longer pays.

## Cut from the original plan

- **Local and cheap models** (Gemma, Jev via OpenRouter, the `cheap-brain` MCP server): everything runs through Claude Code now. Haiku covers cheap lookups.
- **Cross-repo Chief of Staff, voice, Telegram, Pushover and Remote Control set-up**: these are interface choices, not harness features. Remote Control works with this as it is.
- **Sandcastle containers and the AWS/Fargate tier**: worktrees plus the Claude Code sandbox are enough for the MVP.
- **Golden sets, monthly stack-ranking, best-of-N by default**: deferred until the ledger has a baseline (phase 2).
- **Pocock's 27 skills**: kept the shape (grill, spec, tickets, implement, review, handoff) as three local skills, with no supply-chain dependency.
- **Agent teams** (still experimental, about 7× tokens) and the client-site runbook.

## Decisions (18 Nov 2026)

1. **Orchestrator:** Sonnet 5.5 with Opus 5.5 as advisor, set in the harness settings (overrides your user model in harness repos only).
2. **Ledger:** `.agency/` stays gitignored.
3. **Sandbox network:** GitHub (`github.com`, `api.github.com`) plus the PyPI and npm registries, nothing else. Add hosts per repo only when a blocked request shows they're needed; never wildcards.
4. **GitHub auth:** your normal `gh` login for now. Revisit a fine-grained token or GitHub App before running unattended, which is also when PR approvals go to 1.
5. **Branch protection and CODEOWNERS:** yes, via the ruleset template and `install.py --owner`.
