# Execution and review record

- Task ID and approved plan version:
- User authority and scope:
- Repository/branch:
- Base SHA and selection reason:
- Initial staged/unstaged/untracked changes and ownership:

## Local milestones

For each milestone: plan/PR IDs, purpose, owned paths, check evidence, full commit SHA, deviations, and outstanding work. Preserve local commit standards and never absorb unrelated changes.

## Check evidence

For each check:

- ID:
- Exact command/method and working directory:
- Revision or precise worktree state under test:
- Time:
- Result: passed / failed / blocked / not run
- Exit status where applicable:
- Relevant summarized output and sanitized evidence path:
- Coverage limits, environment effects, and known unrelated failures:

A check from before a later edit needs re-evaluation. Record never-run checks honestly.

## Review and repair cycles

Record only the stages that ran, and state which did not. First review, then fix and re-review cycles, at most three unless the user set another number. For each: base/review SHA, review report path, reviewer verdict/coverage, a disposition for every finding ID (fixed with its commit, not applicable with its evidence, or needs a decision), rerun checks, and remaining issues. Budget exhaustion is unresolved, not approval.

## Final provenance

- Final SHA and review artifact:
- Base-to-final range includes only intended task? Evidence:
- Remaining worktree changes and exclusions:
- Walkthrough manifest/narrative digest, if generated:
- Walkthrough HTML path and validation, if actually implemented:
- Unresolved findings/decisions/check limits:
- Push/PR/publication: no action unless explicitly authorized; record actual outcome:
