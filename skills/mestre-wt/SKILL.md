---
name: mestre-wt
description: For a session that manages Git worktrees. Installs mestre-harness into every worktree it creates, with one shell command.
disable-model-invocation: true
---

# mestre-harness in worktrees

For the rest of this session, every time you create a Git worktree, by any method, install mestre-harness into it as soon as it exists:

```bash
"__MESTRE_SETUP__" --quiet <absolute-worktree-path>
```

- Run exactly that one command per new worktree. Do not copy, link, or read harness files yourself
- No output means it worked. If it prints a warning or error, pass it on to the user and keep going with the worktree; do not try other commands to work around it
- It only adds untracked, Git-ignored files: `.claude/agents/mestre-*.md`, `.claude/skills/mestre-*/`, and `.agent-work/`. The worktree stays clean in `git status`, and these files never block removing it
- Removing a worktree needs no harness step. Its `.agent-work/` is deleted with it, so mention that if the user may still want that task's plan or review
- Worktrees that existed before this session are not touched unless the user asks. The same command installs or updates any of them

Request: $ARGUMENTS

If the request above is empty, confirm in one line that worktree setup is active and continue with whatever the user asks next.
