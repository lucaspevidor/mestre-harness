# Walkthrough acceptance cases

Status for `walkthrough.py` version 1. Each case ends with how it is covered: **auto** means `tests/test_walkthrough.py` in the harness repository asserts it, **partial** says what is and is not covered, and **manual** means a person has to check it and nobody has signed it off yet.

## Git fidelity

- [ ] Add/modify/delete/rename/type-change cases show correct old/new paths, blobs, modes, ranges, and exact sanitized text (auto, except type changes, which are untested)
- [ ] The full intended base/final range is represented; multiple local milestone commits are not reduced to `HEAD~1` (auto)
- [ ] Merge history and non-ancestor or unrelated ranges require explicit resolution rather than accidental partial coverage (auto: a non-ancestor base is refused, and a merge inside the range is covered)
- [ ] Staged/unstaged/untracked user work is preserved, excluded, and disclosed; intended uncommitted task edits block final generation (partial: uncommitted work is excluded and disclosed (auto); deciding that it was intended task work is the orchestrator's job)
- [ ] Working-tree contents changing after capture cannot silently replace source from committed objects (auto)
- [ ] SHA-1/SHA-256 formats, spaces/newlines/leading dashes in paths, CRLF, empty files, no-final-newline files, and invalid text encodings are handled explicitly (auto, except empty files, which are untested)
- [ ] Binary, symlink, submodule, generated, oversized, and secret-bearing files have honest coverage/omission metadata (partial: binary, symlink, oversized, and sensitive paths are auto; submodules are untested; generated files are not detected)
- [ ] Git external diff/textconv/filter settings and malicious path input cannot execute unwanted code or redirect extraction (auto, with a control showing plain Git does run the configured program)

## Integrity and narrative

- [ ] Mismatched base/final/reviewed-base/reviewed-final SHA or stale evidence hash fails closed (auto)
- [ ] Missing/duplicate evidence IDs, impossible ranges, false blob/side identity, and incomplete file coverage fail validation (auto)
- [ ] Later code edits require refreshed review/evidence; no old report is relabeled as current (partial: the review record must name the exact range (auto); re-reviewing after an edit is the orchestrator's job)
- [ ] Synthetic fixtures cannot be rendered as a real reviewed report (auto)
- [ ] Every code-specific narrative claim has a relevant reference; arbitrary source paths/URLs are rejected (partial: references are validated and the schema has no path or URL fields (auto); whether a claim is relevant and true is manual)
- [ ] The displayed check status comes from evidence; not-run/blocked/failed cannot become passed (auto)
- [ ] A narrative that drops or contradicts failed checks, remaining findings, exclusions, redactions, or limits cannot hide them: the renderer displays those directly from the manifest (auto)
- [ ] Changes-requested/incomplete review blocks a final walkthrough, while disclosed non-blocking findings remain visible (auto)
- [ ] Important omissions or unreadable/oversized evidence prevent an unqualified completeness claim (auto)

## Redaction and HTML safety

- [ ] Secrets in new, old, deleted, unchanged-context, metadata, and check-log text are redacted before authoring/rendering (partial: the listed patterns are auto for added and removed lines, hunk headers, and check output; pattern scanning cannot be complete)
- [ ] HTML, comments, attributes, embedded JSON, and sidecars contain no hidden unredacted content (partial: test secrets are absent from the manifest and the page (auto); there are no sidecar files)
- [ ] Redaction preserves original line references or explicitly omits the unrepresentable range (auto)
- [ ] Test strings containing script tags, closing tags, quotes, ampersands, event handlers, CSS, dangerous URLs, and terminal control sequences display inertly (auto)
- [ ] Filenames, headings, and evidence IDs cannot become executable markup, attributes, paths, or external requests (auto)
- [ ] No external CDN, scripts, fonts, images, analytics, runtime fetches, source maps, forms, or local-resource reads exist (auto)
- [ ] CSP is defense in depth; escaping remains correct without relying on it (auto: the style hash in the policy is checked; escaping is tested separately)

## Reader quality and local operation

- [ ] One copied HTML file opens with network disabled and makes zero network requests (manual: rendered in headless Chrome only; network requests were not measured)
- [ ] Plain-language overview makes sense without opening code (manual)
- [ ] Reading order explains reasons and behavior, with optional deeper code (manual)
- [ ] Keyboard navigation, focus, headings, native disclosures, long lines, and narrow windows are usable (manual)
- [ ] Tests, tradeoffs, unresolved findings, review range, and dirty-state/coverage limits are visible (manual)
- [ ] Spot checks compare displayed lines with actual Git objects; narrative is reviewed for factual claims separately from schema validity (partial: exact hunk text is asserted against real repositories (auto); reviewing narrative claims is manual)
