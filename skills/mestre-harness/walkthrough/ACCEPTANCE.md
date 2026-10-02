# Future walkthrough acceptance cases

These are requirements for tooling that has not been built. This draft validates interfaces only.

## Git fidelity

- [ ] Add/modify/delete/rename/type-change cases show correct old/new paths, blobs, modes, ranges, and exact sanitized text
- [ ] The full intended base/final range is represented; multiple local milestone commits are not reduced to `HEAD~1`
- [ ] Merge history and non-ancestor or unrelated ranges require explicit resolution rather than accidental partial coverage
- [ ] Staged/unstaged/untracked user work is preserved, excluded, and disclosed; intended uncommitted task edits block final generation
- [ ] Working-tree contents changing after capture cannot silently replace source from committed objects
- [ ] SHA-1/SHA-256 formats, spaces/newlines/leading dashes in paths, CRLF, empty files, no-final-newline files, and invalid text encodings are handled explicitly
- [ ] Binary, symlink, submodule, generated, oversized, and secret-bearing files have honest coverage/omission metadata
- [ ] Git external diff/textconv/filter settings and malicious path input cannot execute unwanted code or redirect extraction

## Integrity and narrative

- [ ] Mismatched base/final/reviewed-base/reviewed-final SHA or stale evidence hash fails closed
- [ ] Missing/duplicate evidence IDs, impossible ranges, false blob/side identity, and incomplete file coverage fail validation
- [ ] Later code edits require refreshed review/evidence; no old report is relabeled as current
- [ ] Synthetic fixtures cannot be rendered as a real reviewed report
- [ ] Every code-specific narrative claim has a relevant reference; arbitrary source paths/URLs are rejected
- [ ] The displayed check status comes from evidence; not-run/blocked/failed cannot become passed
- [ ] A narrative that drops or contradicts failed checks, remaining findings, exclusions, redactions, or limits cannot hide them: the renderer displays those directly from the manifest
- [ ] Changes-requested/incomplete review blocks a final walkthrough, while disclosed non-blocking findings remain visible
- [ ] Important omissions or unreadable/oversized evidence prevent an unqualified completeness claim

## Redaction and HTML safety

- [ ] Secrets in new, old, deleted, unchanged-context, metadata, and check-log text are redacted before authoring/rendering
- [ ] HTML, comments, attributes, embedded JSON, and sidecars contain no hidden unredacted content
- [ ] Redaction preserves original line references or explicitly omits the unrepresentable range
- [ ] Test strings containing script tags, closing tags, quotes, ampersands, event handlers, CSS, dangerous URLs, and terminal control sequences display inertly
- [ ] Filenames, headings, and evidence IDs cannot become executable markup, attributes, paths, or external requests
- [ ] No external CDN, scripts, fonts, images, analytics, runtime fetches, source maps, forms, or local-resource reads exist
- [ ] CSP is defense in depth; escaping remains correct without relying on it

## Reader quality and local operation

- [ ] One copied HTML file opens with network disabled and makes zero network requests
- [ ] Plain-language overview makes sense without opening code
- [ ] Reading order explains reasons and behavior, with optional deeper code
- [ ] Keyboard navigation, focus, headings, native disclosures, long lines, and narrow windows are usable
- [ ] Tests, tradeoffs, unresolved findings, review range, and dirty-state/coverage limits are visible
- [ ] Spot checks compare displayed lines with actual Git objects; narrative is reviewed for factual claims separately from schema validity
