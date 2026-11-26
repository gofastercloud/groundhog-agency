# groupthinQ handoff (18 Nov 2026)

**Goal:** an MVP open-source Claude Code agency harness. One maintainer, one orchestrator session (the Chief of Staff for a repo) and three subagents work GitHub Issues through to draft PRs, with every cost control we could find. README.md is the user guide; PLAN.md has the design, security model and phases.

## State
- Built and green: `UV_CACHE_DIR="$TMPDIR/uv-cache" make verify` gives ruff clean, pyright 0 errors, 244 tests passed.
- Not a git repo yet. Nothing is committed. The maintainer must OK `git init` and the first commit.
- Two review rounds are done (code-reviewer and security-reviewer). Every finding was fixed or answered.
- The original research brief (`Running an agent agency on Claude research brief and plan.md`) is kept for reference only.

## Layout
- `harness/.claude/`: settings.json, the agents (implementer, reviewer, scout), the skills (spec, next, handoff), groupthinq.md (orchestrator rules), and the hooks:
  - `hooks/gtq.py`: the brief, guard, pretool, posttool, statusline and ledger hooks, plus the report CLI;
  - `hooks/dash.py`: the terminal dashboard, and `up` for the tmux layout.
- `harness/templates/`: STATUS.md, the ticket template, the CLAUDE.md block, ruleset.json and CODEOWNERS.
- `install.py`: copies the harness into a target repo; flags `--owner @you`, `--force`, `--dry-run`.
- `tests/`: test_gtq, test_usage, test_guards, test_dash, test_harness.

## Decisions made
1. **Orchestrator:** Sonnet 5.5 with Opus 5.5 as `advisorModel`. Subagents: Sonnet, and Haiku for the scout.
2. **Ledger:** `.agency/` is gitignored.
3. **Sandbox network:** GitHub plus the PyPI and npm registries only. Add hosts per repo when a blocked request shows they're needed.
4. **GitHub auth:** the maintainer's own `gh` login for now. The ruleset requires 0 approvals, because you can't approve your own PRs. Move to a bot account or GitHub App with a scoped token before running unattended, then require 1 approval and code-owner review.
5. **Branch protection and CODEOWNERS:** yes, via the ruleset template and `--owner`.
6. **Usage stop:** at 90% of any subscription limit, the hooks block prompts and deny every tool call. The orchestrator schedules a resume after the reset. `override:` lifts the pause for 30 minutes.
7. **Monitor:** a terminal dashboard. `dash.py up` runs Claude Code and the dashboard side by side in tmux.
8. **Code Mode:** opt-in per session with `dash.py up --code-mode`. It needs Claude Code 2.1.323 or later; 2.1.311 is installed, so the maintainer must run `claude update` first.

## Open
- **Code Mode globally:** whether to turn it on in `~/.claude/settings.json`. Recommended no; ask the maintainer.
- **Trusted-issue check:** a body hash taken at `/spec` time to detect later edits to an issue (deferred; solo repo).
- **CI status check:** add a required status check to the ruleset once the target repo's CI check name is known.

## Next: phase 0 on one real repo
Work through PLAN.md's "Phase 0 must confirm" list first:
1. The settings file loads, and `advisorModel`, `crossSessionInbound`, `workflowSizeGuideline`, `subagentPromptCacheTtl` and `onFailure` are recognised.
2. Hooks run outside the Bash sandbox, so they can write `.agency/`. If the status line says "usage not saved", they can't.
3. Subagent hook input has `agent_id`, and `CLAUDE_PROJECT_DIR` points at the main repo from inside worktrees.
4. Worktree creation and commits work, `gh` works in the sandbox, and a push plus a draft PR work.
5. The denied commands really are denied.
6. The scheduled resume fires through `UserPromptSubmit`.

Then run `/spec` and three tickets through `/next`.
**Gate:** three PRs merged with no hand-written code, and ledger rows for each.

## Gotchas
- Inside the sandbox, uv needs `UV_CACHE_DIR="$TMPDIR/uv-cache"`, and installing packages needs pypi.org and files.pythonhosted.org on the network allowlist. pyright is the system install: `pyright --pythonpath .venv/bin/python`.
- The maintainer's global git config signs commits with an SSH key, which the sandbox blocks. Test repos must set `GIT_CONFIG_GLOBAL=/dev/null`.
- Hooks must stay compatible with Python 3.9, because macOS's built-in `python3` is 3.9. ruff's `per-file-target-version` enforces it, and `test_hooks_parse_on_python_39` checks it.
- Maintainer rules: plan first for multi-file changes and wait for an OK; Australian spelling; never weaken tests; ask before anything destructive or non-local; run code-reviewer and security-reviewer before any PR.
- Baseline spend over 5 weeks of logs: about $870 at API list prices. 66% went to main sessions, 21% to implementers and 7% to general-purpose agents. Cache reads cost 0.05× of input on Opus and Sonnet 5.5.
