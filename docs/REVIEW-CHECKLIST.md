# Review before adoption

## Behavior decisions

- [ ] The full flow on "let's plan" matches how you want to work
- [ ] Routine in-scope hardening is incorporated automatically; material requirements/tradeoffs are surfaced
- [ ] Bloat findings are evaluated and shown with exact location, reason, simpler alternative, and tradeoff; removal waits for approval
- [ ] The final decomposition preserves unapproved items in its baseline
- [ ] Small direct implementation requests may use the light path, with checks and independent review
- [ ] Local coherent milestone commits are part of approved implementation; push/PR/publication need separate approval
- [ ] Two repair/re-review cycles are an acceptable escalation threshold; unresolved defects remain visible
- [ ] Artifacts and the map live in `.agent-work/`, untracked, and that retention suits the repository

## Configuration review

- [ ] `setup.sh status` reports the install matches the pack and every harness path is ignored; `git status` shows nothing new
- [ ] Exactly eight `mestre-*` agent definitions and two `mestre-*` skills are installed, with no name collisions
- [ ] `model: inherit` is acceptable, or explicit model changes are separately reviewed
- [ ] Tool lists match the roles: only mapper, reviewer, and implementer have `Bash`; the hardener can only edit; the bloat analyzer is read-only; only the implementer changes product files or Git state
- [ ] Actual permissions are reviewed; nobody mistakes instructions for a shell sandbox
- [ ] Repository rules outrank inferred habits; contradictory instructions are resolved
- [ ] No renderer, extractor, push, or PR is implied to have run

## Planning-only smoke test after adoption

1. Run `/mestre-harness` and give a small representative request beginning with "let's plan"
2. Confirm a relevant map is used/refreshed, then initial decomposition, plan, hardening, hardened-plan bloat review, and final decomposition
3. Confirm each hardening edit is recorded in the plan with a disposition and reason, without a second competing plan
4. If bloat exists, confirm it remains pending your decision and appears in the final response
5. Confirm no product edit, local commit, test script, push, or PR occurs before implementation approval, and that the only files written are artifacts under `.agent-work/`
6. Ask a blocking question or change a material requirement; verify the gate reopens appropriately

## Implementation trial only after approval

Use a disposable/trusted test repository and an approved tiny change. Check that the implementer follows documented conventions, does not absorb pre-existing changes, runs relevant checks, and makes a coherent local commit. Give the reviewer the base and final SHA; confirm it inspects the real diff itself and leaves the worktree and Git state unchanged. Introduce a repair and ensure review/test evidence is refreshed for the new SHA. Stop at the configured bound rather than hiding unresolved findings.

## Future walkthrough acceptance

Use the separate [renderer acceptance checklist](../skills/mestre-harness/walkthrough/ACCEPTANCE.md). This draft's schema checks do not establish Git extraction correctness, secret-redaction completeness, browser safety, or offline rendering.
