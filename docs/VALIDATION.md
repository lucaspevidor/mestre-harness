# Draft validation record

Checked 2 October 2026 for version 0.2. Version 0.1 was a pack to merge by hand; 0.2 renames it mestre-harness, moves the orchestrator into a skill, prefixes the agents, and adds a setup script and a worktree skill. Each item says what was actually run on the current files.

## Passed

- Exactly eight uniquely named Markdown agent definitions, each named after its file (run)
- Agent frontmatter uses only name, description, tools, model, permissionMode; the two skills use name, description, disable-model-invocation, and (orchestrator only) argument-hint. Checked with a line-based parser, not a YAML library
- Intended tool lists (run): bloat analyzer read-only; hardener with `Edit`; walkthrough author with `Write`; planner and decomposer with `Write` and `Edit`; mapper with `Bash`, `Write`, `Edit`; reviewer with `Bash` and `Write`; implementer with read/write/edit/shell access; all use model inherit and ordinary permission mode
- Two valid JSON Schema Draft 2020-12 documents and two conforming synthetic samples (checked for 0.1 only; the four JSON files are byte-identical since then)
- Sample evidence byte digest and sanitized text hashes (run). Matching base/final/reviewed-base/reviewed-final IDs, source side/blob identity, line and hunk counts, unique IDs, references, file coverage, and not-run check status (checked for 0.1 only, files unchanged)
- Local Markdown links and anchors resolve within the pack (run)
- All files are ASCII: no curly quotes, arrow glyphs, middle dots, or em dashes (run)
- Setup script against throwaway repositories with Git's global config redirected to a scratch file (run): install of both skills and eight agents, repeat install without duplicate ignore lines, status, drift detection, the setup path rendered into the worktree skill, `--quiet` install into a linked worktree, `git worktree remove` succeeding without `--force` on an installed worktree, uninstall with and without `--purge`, refusal when a harness path is tracked, a non-Git folder, a custom `core.excludesFile`, and `git status` staying empty after install
- Content review of full planning order, bloat approval gate, single-plan ownership, repository evidence standards, local-only commits, independent review, repair bounds, and walkthrough safety requirements (checked for 0.1 only; since then agents save their own artifacts, the mapper and reviewer collect Git evidence, and the hardener edits the plan, so those passages deserve a fresh read)

## Limits

This is static validation plus a scripted setup test, not a runtime test. Setup has not been run against a real repository or your real global excludes file. No target repository was accessed, no real source change was made or reviewed, and no test command from the synthetic example was executed. The example's Git identities and review record are fictional.

No `/mestre-harness` or `/mestre-wt` session, skill-loading check, Claude Code end-to-end run, permission-enforcement test, Git extraction test, secret-redaction test, HTML render, browser-safety test, or offline walkthrough test has occurred. Those checks belong to a later authorized adoption or implementation trial. Schemas cannot establish factual correctness or enforce approval behavior by themselves.

Official compatibility references are in [COMPATIBILITY.md](COMPATIBILITY.md). Future renderer requirements are in [the acceptance checklist](../skills/mestre-harness/walkthrough/ACCEPTANCE.md).
