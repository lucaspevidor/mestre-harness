# Local Git to HTML walkthrough design

## Status and purpose

**Implemented as [walkthrough.py](walkthrough.py), version 1.** This document is the specification it follows; [Version 1 limits](#version-1-limits) lists what it does not do yet. The script is one Python 3 file with no dependencies and three commands: `extract`, `validate`, and `render`.

The goal is one private, self-contained HTML file that explains the exact final reviewed change. A reader gets a short plain-language account first and can expand real code/diff evidence when useful. It works from a local file with the network disabled.

## Division of responsibility

1. **Main session:** records scope, selects/captures the base, arranges implementation and independent review, freezes the final revision, and verifies the result
2. **Deterministic extractor (`walkthrough.py extract`):** reads real Git objects/diffs, captures verification evidence, applies redactions, and produces the evidence bundle
3. **Walkthrough author:** reads sanitized evidence and writes structured prose, references, and reading order only
4. **Deterministic renderer (`walkthrough.py render`):** validates provenance and references, injects escaped evidence, and writes offline HTML

The LLM does not manufacture code, reconstruct a patch, choose an unverified revision, or supply executable HTML. A schema-valid narrative alone is not proof that its explanations are correct.

## Reader experience

- Header: task title, base/final identifiers, review status, and a clear snapshot/staleness notice
- Short overview: what changed, why, and the effect someone will notice
- Ordered behavior sections: before, after, reason, and optional deeper explanation
- Expandable evidence: actual unified diff or source at the labeled base/final revision, with file path and original line numbers
- Verification: which checks ran, what they establish, and what failed, was blocked, or was not run
- Limits: unresolved review findings, tradeoffs, redactions, unsupported content, and dirty-worktree exclusions
- File coverage: every changed file accounted for, including mechanical/generated changes and omissions

Use readable typography, generous spacing, visible focus, accessible headings, and native `details`/`summary` controls. Do not require JavaScript. Wrap prose and allow horizontal scrolling in code. Browser find and fragment links are sufficient for version one; no search service, server, CDN, analytics, fonts, remote images, or external fetches.

## Capturing the correct change

### Base selection

Capture the full base commit ID before implementation, plus the selection reason and current Git state. For a new change, current HEAD is the default. For an existing branch or PR, agree on the intended comparison base and inspect ancestry/merge-base as needed. A stale local remote-tracking branch is not evidence of the latest remote state. Do not fetch without appropriate authorization or silently substitute `HEAD~1`.

A non-Git repository or unborn branch has no valid base commit. Planning can continue, but this commit-to-commit pipeline waits for a legitimate, user-agreed baseline. Do not invent a commit ID or silently initialize the repository.

Use immutable full commit IDs, supporting the repository's Git object format. Verify both resolve to commit objects and that the intended ancestry/scope is valid. If the range contains unrelated commits, stop and resolve the range rather than hiding files with an arbitrary path filter. Capture the exact selected IDs in the plan, execution log, review, manifest, narrative, and HTML.

### Uncommitted and pre-existing changes

Inventory staged, unstaged, and untracked files before work. Preserve other people's changes. The baseline walkthrough is commit-to-commit: it excludes all uncommitted changes and says so prominently. Do not claim that `base..final` represents dirty working-tree edits.

If intended task changes remain uncommitted, the normal final walkthrough is blocked until they are safely committed under the approved workflow. Never commit unrelated work to satisfy this gate. A future worktree-snapshot mode would need a separate design with explicit snapshot identity; it is not part of version one.

A checkout may remain dirty only with explicitly identified excluded changes that do not contaminate task commits or invalidate final check/review evidence. Mixed or unclear ownership is a blocker. Do not stash/reset/clean files automatically.

### Final capture

Freeze `final_sha` after independent review and any bounded repairs. Require the review to name both the exact reviewed base and final revisions; a review of only the last commit does not cover the whole branch range. The review can have disclosed non-blocking findings, but changes-requested or incomplete-evidence status blocks a "final reviewed walkthrough." A partial diagnostic report may be useful only if clearly labeled and explicitly requested; it must not masquerade as final.

Any later edit/commit invalidates the relevant review and walkthrough provenance. Re-extract and re-review affected content; never relabel old evidence with the new SHA. The offline file remains an honest historical snapshot and cannot know automatically that the repository later changed.

## Extractor responsibilities

1. Validate repository identity, full commit IDs, intended range, reviewed base/final SHA and status, and dirty-state exclusions
2. Obtain a complete changed-file inventory and raw Git evidence for the captured base/final pair. Use Git plumbing or commands with equivalent semantics to `git diff --no-ext-diff --no-textconv --no-color` and `git show` of verified objects; do not read mutable working-tree files as committed source
3. Invoke Git using argument arrays, never shell-concatenated narrative/path input. Resolve revisions to object IDs first. Use NUL-delimited path records, literal path handling, and options separating paths from flags. Disable external diff/textconv and avoid any repository-configured conversion/filter execution
4. Preserve old/new paths, status, mode changes, blob IDs, and per-side ranges. Support additions, deletions, renames, type changes, and merge-commit ranges explicitly. Represent binary files, submodules, and symlinks as metadata or omissions; do not dereference paths or follow symlinks outside the repository
5. Extract source ranges and diff hunks deterministically from the Git objects, not from LLM-selected arbitrary files. The author can request an additional relevant range; the extractor validates and adds it under a new evidence ID
6. Decode text under an explicit encoding policy. Preserve original line numbering and CRLF semantics. Mark undecodable/binary content as unsupported rather than silently changing it. Escape unusual path names for display while retaining an unambiguous original identity. Do not treat a file path as a URL or HTML ID
7. Apply reviewed redaction before writing distributable artifacts or sending evidence to the author. Check old and new source, deleted lines, diff context, paths, check logs, and metadata. Omit environment files/credentials and known sensitive paths by default; scan the rest for likely secrets. Pattern scanning is incomplete, so suspected material requires inspection and a conservative omission if uncertain
8. Replace redacted spans with explicit markers while preserving original line numbering. Hash the **sanitized** display text for renderer integrity. Do not retain hidden raw secrets in HTML, JSON, attributes, comments, logs, or sidecar files. Record redaction locations/reasons without repeating secret values
9. Enforce explicit per-file/total limits. Record every omission and its reason. If essential evidence exceeds the limits, stop for a scoped expansion or an explicitly incomplete result; never silently truncate a critical hunk or claim full coverage
10. Produce `evidence.json` using [the evidence schema](evidence.schema.json), validate structural and semantic consistency, and compute the SHA-256 digest of its exact UTF-8 file bytes

`walkthrough.py extract` implements these responsibilities within the limits listed below. Local extraction can operate without network access; no service receives the repository by default.

## Evidence interface

The manifest includes sanitized repository label, base/final commit IDs, object format, base-selection reason, final review, worktree exclusions, changed files, evidence records, and redaction/coverage limitations. Full local absolute paths and remote credentials must not enter the distributable manifest.

Evidence records have opaque generated IDs. Four types are defined:

- **source:** exact sanitized source range from the base or final blob, with side, revision, blob ID, original lines, text, and text hash
- **diff:** a sanitized real hunk with base/final IDs, old/new range, stable hunk label, text, and text hash
- **check:** actual recorded command/method, status, revision applicability, coverage, and sanitized output
- **omission:** a file-level omission or redaction reason, without the omitted secret/content

Check results need trustworthy execution evidence. A historical check is not applicable to final code merely because its text says "passed." Require an exact tested commit or a recorded identical-tree proof; otherwise mark `applies_to_final` false and explain. The extractor must not fabricate or upgrade supplied check results.

`evidence_sha256` is the hash of the exact saved `evidence.json` bytes, including its final newline if present. It is stored in the narrative, not in the manifest itself, avoiding a self-referential hash. File read/serialization changes require a new hash and narrative binding. Evidence text hashes separately verify decoded sanitized text as UTF-8, without Unicode normalization.

## Author interface

The author receives only sanitized evidence and relevant approved rationale. It returns JSON matching [the narrative schema](narrative.schema.json): plain-language summary, ordered sections, evidence IDs, file coverage, test explanations, and limitations. It cannot provide arbitrary source paths, code blocks, raw HTML, executable commands, or remote URLs as references.

Use [sample-evidence.json](sample-evidence.json) and [sample-narrative.json](sample-narrative.json) as a **synthetic, hand-authored interface example**. Their commit/blob IDs, source, diff, and review record are fictional. No repository was extracted or reviewed, and the sample test is explicitly not run. A production renderer must reject synthetic bundles unless explicitly in a clearly labeled demo/test context.

## Renderer validation and safety

Before rendering:

- Parse JSON against the pinned schemas with no automatic network resolution of `$schema` or `$ref`
- Recompute the exact evidence-file hash and all evidence text hashes; require the narrative binding to match
- Verify identical base/final IDs, object format, matching `review.reviewed_base_sha` and `review.reviewed_sha`, acceptable final review status, no production synthetic flag, and unique IDs
- Require each narrative reference to exist and each changed file to be covered; reject duplicate/missing coverage and unresolved IDs
- Validate old/new path and blob presence against file status, source-side/revision/blob identity, nonnegative hunk ranges, valid source line ranges, and line-count/range consistency
- Validate check applicability and displayed status from the evidence itself; the author cannot turn `not-run` into `passed`
- Always derive and display review verdict/unresolved findings, every check's status and applicability, worktree exclusions, redactions, and coverage limits directly from the evidence manifest, even if the narrative omits or contradicts them. Narrative explanation cannot suppress authoritative status or limitations
- Verify any code-range display is exactly the sanitized extractor text; never recover omitted text from the working directory

The renderer selects a fixed trusted template and renders **all** untrusted strings as literal text: prose, code, diff, file names, headings, review notes, check output, and redaction reasons. Use context-appropriate escaping for `&`, `<`, `>`, quotes, and attributes. Do not concatenate untrusted input into markup, scripts, CSS, event handlers, or raw innerHTML. Do not interpret Markdown or HTML from the author.

Generate safe internal fragment IDs from validated opaque identifiers, not paths/titles. All navigation stays within the document. Use fixed inline CSS, no script, no forms, no remote/local-resource links, and a restrictive Content Security Policy compatible with local-file viewing as defense in depth. Escape ANSI/control sequences in displayed logs. A CSP does not replace correct escaping.

Output should be one HTML file with no runtime network or filesystem reads. Copying it to another directory must not break it. Prevent accidental disclosure through absolute paths, hidden payloads, source maps, or included unused artifacts. Keep it local; opening a PR, uploading, or publishing the report needs separate approval.

## Version 1 limits

- **Checks are supplied, not run.** The extractor takes check results from the inputs file and labels them as supplied. A check applies to the final code only when its recorded revision equals `final_sha`; there is no identical-tree proof
- **Redaction is pattern-based.** Files whose names match a sensitive-path list are omitted, and common token formats, private key blocks, quoted secrets, and credentials in URLs are masked. It can miss secrets and can mask harmless text. There is no built-in human review step
- **No diagnostic mode.** A review that requests changes or has incomplete evidence makes `render` refuse; there is no clearly labeled partial report
- **Ancestry is required.** A base that is not an ancestor of the final commit is refused. Merge commits inside the range are fine
- **Diff hunks by default.** Source ranges are added only on request with `--source`. Whole-file additions and deletions are read with a literal pathspec; every other file is diffed blob to blob, so path attributes cannot affect it
- **Metadata instead of content** for binary files, symbolic links, submodules, files that are not valid UTF-8, and anything over the size limits. Generated files are not detected; the author classifies them
- **Commit to commit only.** There is no worktree-snapshot mode. Uncommitted changes are listed and excluded
- **Browser checks are manual.** The automated tests cover structure and escaping. Opening the file with the network disabled, keyboard use, and narrow windows have not been signed off

## Completion evidence

A walkthrough is complete only when [the acceptance cases](ACCEPTANCE.md) hold for it, the exact report is opened locally with network disabled, and code/line/reference fidelity is checked against captured Git objects. Record file hash, base/final IDs, review status, verification limits, and any omissions in the execution log. Never describe a schema-only check as a functioning HTML pipeline.
