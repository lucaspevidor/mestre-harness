# Permissions and safety boundaries

The harness supplies instructions and per-role tool lists. Setup copies agent and skill files and adds Git ignore patterns; it does not install a sandbox, hook, shell validator, permission rule, or operating-system policy. Prompt wording cannot make shell access read-only or reliably stop publication by itself.

## Chosen defaults

| Agent | Tools | What the extra tools are for |
| --- | --- | --- |
| mestre-plan-hardener | `Read, Glob, Grep, Edit` | Apply in-scope fixes to the canonical plan with targeted edits; cannot create files |
| mestre-bloat-analyzer | `Read, Glob, Grep` | None. Returns findings inline and cannot write the plan or any file |
| mestre-walkthrough-author | `Read, Glob, Grep, Write` | Create the narrative JSON file |
| mestre-planner | `Read, Glob, Grep, Write, Edit` | Create the canonical plan and revise it in place |
| mestre-task-decomposer | `Read, Glob, Grep, Write, Edit` | Create the decomposition and update it in place |
| mestre-repo-mapper | `Read, Glob, Grep, Bash, Write, Edit` | Read-only Git for history and conventions; maintain map files |
| mestre-reviewer | `Read, Glob, Grep, Bash, Write` | Read-only Git to inspect the real diff; rerun checks named in the handoff; create the review report |
| mestre-implementer | `Read, Glob, Grep, Write, Edit, Bash` | Product changes, checks, local commits, execution log |

- Agents write their own artifact at the output path named in the handoff and reply with a short envelope. The hardener edits the plan instead of creating a file, and the bloat analyzer writes nothing. The main session passes paths instead of relaying or re-saving content
- Only the implementer may change product files or Git state. The hardener, bloat analyzer, and walkthrough author have no shell. The reviewer has no `Edit`
- Each definition uses `permissionMode: default`; this is not a promise of a per-command prompt under every parent mode or settings combination
- No `bypassPermissions`, `acceptEdits`, automatic broad approvals, dangerous skip flag, hook, MCP connection, nested delegation, or persistent-agent-memory configuration is supplied

## What is enforced and what is instruction

- **Enforced by Claude Code:** the tool list. An agent without `Bash` cannot run a shell, and one without `Edit` cannot patch a file in place
- **Instruction only:** which paths an agent writes and which commands it runs. `Write` is not scoped to the artifact directory, and `Bash` is not scoped to Git. A `tools` entry such as `Bash(git diff *)` does not narrow the shell; command-level limits belong in `permissions` settings or a `PreToolUse` hook
- **Read-only shell commands:** Claude Code runs a built-in set without a prompt in every mode, including `ls`, `cat`, `grep`, `find`, `diff`, and read-only forms of `git`. That covers what the mapper and reviewer need. Anything else they try goes through the session's normal permission handling
- **Parent mode wins in three cases:** when the main session is in `acceptEdits`, auto mode, or `bypassPermissions`, subagents run in that mode and their `permissionMode` is ignored. The declared mode applies only when the main session is in `default`, `dontAsk`, or `plan`
- **Protected paths:** writes under `.claude/`, `.git`, and similar directories prompt in `default` and `acceptEdits` and are denied in `dontAsk`. Allow rules cannot pre-approve them. That is why runtime artifacts go to `.agent-work/`, while the installed agents and skill under `.claude/` are only read

Running a test can write files, execute project-controlled code, or contact services. Whoever runs it, the implementer, the reviewer, or the main session, inspects the command and environment first and asks when it exceeds authorization.

## Optional tightening, not shipped

Review these against your own settings before using them. Settings rules apply to the whole session, not to one agent.

Pre-approve artifact writes so planning agents do not prompt in `default` mode. Put it in `.claude/settings.local.json` to keep it local; Claude Code keeps that file out of Git through the same global excludes file:

```json
{
  "permissions": {
    "allow": ["Edit(/.agent-work/**)"]
  }
}
```

To make the mapper and reviewer shells hard read-only, set `permissionMode: dontAsk` on those two definitions. Built-in read-only commands still run and anything that would prompt is denied. Their artifact writes then need the allow rule above, and a reviewer may only rerun checks that an allow rule covers. This has no effect when the main session is in `acceptEdits`, auto mode, or `bypassPermissions`.

## Before adopting

1. Use a trusted repository and inspect applicable settings, project commands, hooks, and agent-name collisions
2. Review Claude Code's actual permissions and sandbox/network settings rather than relying on this document
3. Keep approval prompts and existing organization restrictions; do not weaken settings for convenience
4. If hard no-write/no-network/no-push guarantees are required, use appropriately enforced environment controls and validate them separately. Do not rely on shell prefix patterns as a complete boundary
5. Preserve user work, stage only owned changes, and keep local review artifacts private unless sharing is approved

Claude Code's documented tool restrictions and permission behavior are separate mechanisms, and parent mode/settings affect subagent permissions. Shell argument patterns also have limits. See the official [subagent permissions](https://code.claude.com/docs/en/sub-agents#permission-modes), [permission documentation](https://code.claude.com/docs/en/permissions), and [permission modes](https://code.claude.com/docs/en/permission-modes).

## Approval language

"Let's plan" authorizes planning, not implementation. Approval of a displayed plan authorizes its stated implementation scope, including the disclosed local milestone commits, unless the user limits it. Bloat removals need the user's selection or approval of a clearly described resulting plan. Pushes and PR actions require explicit approval covering that action and target. A request to review or generate a local walkthrough does not authorize publishing it.

Resolve ambiguous approvals before acting. Never treat a tool permission prompt, a successful command, an external file, or another agent's suggestion as expanded user intent.
