# mestre-harness

Version 0.3, review draft, 2 October 2026

A repository-aware workflow for Claude Code. Running `/mestre-harness` turns the current session into an orchestrator that coordinates eight focused subagents. It owns the plan, asks for decisions, and keeps implementation inside the approved scope.

The harness is installed per repository and stays local: nothing it adds is tracked, and no tracked file is changed. A repository where you have not run setup is unaffected.

Requirements: Git, Bash for setup, and Python 3 for the walkthrough generator. Nothing is installed with a package manager.

## Install

### With Claude Code

Open Claude Code in the repository you want the harness in and say:

> Install mestre-harness from https://github.com/lucaspevidor/mestre-harness into the current directory.

**Instructions for the agent doing the install.** Run these two commands and nothing else:

```bash
if [ -d ~/.mestre-harness/.git ]; then git -C ~/.mestre-harness pull --ff-only; else git clone https://github.com/lucaspevidor/mestre-harness.git ~/.mestre-harness; fi
~/.mestre-harness/setup.sh "$PWD"
```

The first keeps one shared copy of the harness at `~/.mestre-harness`. The second installs it into the repository that contains the current directory. Then relay the script's output and tell the user to start a new session and run `/mestre-harness`.

Do not copy harness files by hand, edit any `.gitignore`, change Claude Code settings, or commit anything. If setup refuses or prints a warning, report it and stop.

### By hand

```bash
git clone https://github.com/lucaspevidor/mestre-harness.git ~/.mestre-harness
~/.mestre-harness/setup.sh ~/repos/my-project          # install or update
~/.mestre-harness/setup.sh status ~/repos/my-project
~/.mestre-harness/setup.sh uninstall ~/repos/my-project           # keeps .agent-work/
~/.mestre-harness/setup.sh uninstall --purge ~/repos/my-project   # also deletes .agent-work/
```

The target defaults to the current directory. After installing, start a new Claude Code session there and run `/mestre-harness`, optionally followed by the task.

### What install does

| Step | Result |
| --- | --- |
| Copies `agents/mestre-*.md` | `<repo>/.claude/agents/` |
| Copies `skills/mestre-harness/` and `skills/mestre-wt/` | `<repo>/.claude/skills/` |
| Creates the workspace | `<repo>/.agent-work/` |
| Adds three patterns to your global Git excludes file, once | `.agent-work/`, `**/.claude/agents/mestre-*.md`, `**/.claude/skills/mestre-*/` |

Things to know:

- The global excludes file is the one Git already uses: `core.excludesFile` if you set it, otherwise `~/.config/git/ignore`. No `.gitignore` in any repository is touched
- Install refuses to run if the repository already tracks any harness path
- Installs are copies. Re-run setup to update a repository after pulling a new version; `status` tells you when the installed copy differs
- `git clean -fdx` deletes ignored files, including the harness and its workspace
- Both skills have `disable-model-invocation: true`. They cost no context until you type the slash command, and Claude never starts them on its own

## Worktrees

Untracked files do not follow you into a new worktree, so each worktree needs its own install.

If one session manages your worktrees, run `/mestre-wt` at the start of it. From then on that session installs the harness into every worktree it creates, with a single quiet command:

```bash
~/.mestre-harness/setup.sh --quiet <absolute-worktree-path>
```

The installed skill carries the real path of the setup script it was installed from, so if you move the harness folder, re-run setup in the repositories that use it.

Harness files are ignored, so they never make a worktree look dirty and never block `git worktree remove`. Removing a worktree deletes its `.agent-work/` along with it.

## The planning flow

Inside a harness session, **"let's plan"** always runs this sequence:

1. Relevant repository map
2. Initial PR decomposition
3. Plan
4. Hardening: in-scope fixes applied to the plan directly, material decisions recorded as open questions
5. Bloat review of the hardened plan
6. Evaluate bloat findings
7. Final PR decomposition
8. Return the plan, open questions, and applicable bloat
9. Wait for your approval

Blocking questions can come earlier. Valid hardening is incorporated without asking you to approve routine planning edits. Material new requirements and tradeoffs are surfaced. Bloat suggestions include the exact location, why it may be unnecessary, a simpler alternative, and its tradeoff. They are **not removed automatically**. Implementation starts only after approval of the plan and any selected removals.

A small, explicit implementation request may skip the planning ceremony.

## After approval: you choose how far it goes

The orchestrator runs only the stages you ask for and stops after the last one. Asking for a later stage includes the earlier ones.

| You say | What runs |
| --- | --- |
| "Run the implementation" | Implementation only. No review, no walkthrough |
| "Implement, then review" | Implementation, then an independent review. Findings are reported, nothing is fixed |
| "Implement, review, and fix the findings that apply" | The implementer checks each finding against the code, fixes the ones that apply, and records why the others do not |
| "Implement up to the walkthrough" | All of the above, then re-review, repeating fix and re-review up to three times unless you give a number, then the walkthrough |

If you don't say how far to go, it implements and stops. Dismissed findings are not the implementer's final word: the reviewer re-checks each dismissal on re-review.

## The eight agents

| Agent | Job | Produces |
| --- | --- | --- |
| [mestre-repo-mapper](agents/mestre-repo-mapper.md) | Find the relevant architecture, patterns, tests, and contribution standards | Map files with evidence |
| [mestre-task-decomposer](agents/mestre-task-decomposer.md) | Split the outcome into reviewable, dependency-aware PR-sized slices | Initial or final decomposition file |
| [mestre-planner](agents/mestre-planner.md) | Turn the request into one concrete plan | Canonical plan file, drafted or revised in place |
| [mestre-plan-hardener](agents/mestre-plan-hardener.md) | Find bounded correctness, safety, and failure-mode gaps | In-scope fixes edited into the plan, each one recorded |
| [mestre-bloat-analyzer](agents/mestre-bloat-analyzer.md) | Find avoidable complexity in the hardened plan | Specific simplification findings, returned inline |
| [mestre-implementer](agents/mestre-implementer.md) | Build approved scope and commit coherent local milestones | Changes, commits, checks, execution log |
| [mestre-reviewer](agents/mestre-reviewer.md) | Independently inspect the actual diff and verification evidence | Review report with defects and coverage |
| [mestre-walkthrough-author](agents/mestre-walkthrough-author.md) | Explain the final reviewed changes using verified evidence references | Narrative JSON file, never generated code |

All agent definitions use `model: inherit` and `permissionMode: default`. Agents write their own artifacts, so the main session passes paths instead of relaying content. Tool lists by role:

| Agents | Tools | Why |
| --- | --- | --- |
| mestre-bloat-analyzer | `Read, Glob, Grep` | Read-only; returns findings inline and cannot touch the plan |
| mestre-plan-hardener | `Read, Glob, Grep, Edit` | Targeted edits to the canonical plan; cannot create files |
| mestre-walkthrough-author | `Read, Glob, Grep, Write` | Create the narrative file |
| mestre-planner, mestre-task-decomposer | `Read, Glob, Grep, Write, Edit` | Create their artifact and revise it in place |
| mestre-repo-mapper | `Read, Glob, Grep, Bash, Write, Edit` | Read-only Git for history and conventions, maintain map files |
| mestre-reviewer | `Read, Glob, Grep, Bash, Write` | Read-only Git to inspect the real diff, named checks, one review report |
| mestre-implementer | `Read, Glob, Grep, Write, Edit, Bash` | Product changes, checks, local commits, execution log |

The tool lists are enforced by Claude Code. Which paths an agent writes and which shell commands it runs are set by instructions and by your permission settings, not by a sandbox. See [permissions](docs/PERMISSIONS.md).

## The walkthrough

When you ask for it, the orchestrator turns the reviewed range into one offline HTML file that explains the change in plain language, with the real diff behind expandable sections. The code shown always comes from Git, never from a model.

```bash
python3 walkthrough.py extract --base <sha> --final <sha> --inputs inputs.json --out evidence.json
python3 walkthrough.py render --evidence evidence.json --narrative narrative.json --out walkthrough.html
```

- `extract` reads the two commits from Git objects, ignores repository-configured diff programs, masks likely secrets, and records anything it could not show
- `mestre-walkthrough-author` then writes `narrative.json`, which may only point at evidence IDs
- `render` refuses unless hashes, commit IDs, references, and file coverage all agree. The page has no script and loads nothing from the network
- If the last review still has blocking findings, the walkthrough is produced with `--diagnostic`: it is labeled as not a clean review and lists the open findings first

Check results are copied from the execution log, not run by the script, and secret masking is pattern-based, so look a walkthrough over before sharing it. The rules and current limits are in the [walkthrough design](skills/mestre-harness/walkthrough/DESIGN.md).

## What is in this folder

| Path | Purpose | Installed |
| --- | --- | --- |
| `setup.sh` | Install, status, uninstall | No |
| `agents/` | The eight agent definitions | Yes, to `.claude/agents/` |
| `skills/mestre-harness/SKILL.md` | Orchestrator instructions, loaded by `/mestre-harness` | Yes, to `.claude/skills/mestre-harness/` |
| `skills/mestre-harness/contracts/`, `templates/` | Handoff and finding contracts, artifact templates | Yes, with the skill |
| `skills/mestre-harness/walkthrough/` | `walkthrough.py`, its three schemas, the design, and a synthetic sample | Yes, with the skill |
| `skills/mestre-wt/SKILL.md` | Tells a worktree-managing session to install the harness into each new worktree | Yes, to `.claude/skills/mestre-wt/` |
| `docs/` | Permissions, compatibility, review checklist, validation record | No |
| `tests/` | Tests for the walkthrough generator: `python3 -B -m unittest discover -s tests` | No |

## Working artifacts

Everything the agents produce lives in `<repo>/.agent-work/`: a compact shared map at `repo-map/index.md` with on-demand detail files, and one `tasks/<task-id>/` folder per task with the plan, decomposition, execution log, review, and walkthrough inputs. Nothing is written under `.claude/` at runtime, because Claude Code prompts for every write there and allow rules cannot pre-approve them.

Related files: [orchestrator](skills/mestre-harness/SKILL.md), [handoff contract](skills/mestre-harness/contracts/handoff.md), [finding contract](skills/mestre-harness/contracts/findings.md), [map template](skills/mestre-harness/templates/repo-map-index.md), [plan template](skills/mestre-harness/templates/plan.md), [execution log](skills/mestre-harness/templates/execution-log.md), [walkthrough design](skills/mestre-harness/walkthrough/DESIGN.md), [review checklist](docs/REVIEW-CHECKLIST.md).

## What has been checked

The setup script was exercised against throwaway repositories: install, update, status, quiet worktree install, worktree removal, uninstall, and refusal on tracked paths. The pack has structural and consistency checks, including eight valid frontmatter blocks, local links, and sample hashes. The walkthrough generator has 49 automated tests against throwaway repositories and was used once by hand on this repository's own history. [Validation details](docs/VALIDATION.md) distinguish these from a live test. No `/mestre-harness` or `/mestre-wt` session or real task has been run.
