#!/usr/bin/env python3
"""mestre-harness walkthrough generator.

Turns one reviewed Git range into a single offline HTML explanation:

  extract   read the base-to-final range from Git objects and write evidence.json
  validate  check evidence (and optionally a narrative) and report every problem
  render    validate, then write one self-contained HTML file

Standard library only. The schemas next to this file are the contract; see
DESIGN.md for the reasoning behind each rule enforced here.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent
EVIDENCE_SCHEMA = "evidence.schema.json"
NARRATIVE_SCHEMA = "narrative.schema.json"
INPUTS_SCHEMA = "inputs.schema.json"

ACCEPTED_VERDICT = "no-blocking-findings-in-inspected-scope"
OID_LENGTHS = {"sha1": 40, "sha256": 64}

DEFAULT_CONTEXT_LINES = 3
MAX_BLOB_BYTES = 2_000_000
MAX_FILE_TEXT_BYTES = 60_000
MAX_TOTAL_TEXT_BYTES = 600_000
MAX_CHECK_OUTPUT_BYTES = 20_000


class WalkthroughError(Exception):
    """A fail-closed condition. Carries every message that should be shown."""

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


# ---------------------------------------------------------------------------
# JSON and text helpers
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key %r" % key)
        seen[key] = value
    return seen


def parse_json(data, label):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise WalkthroughError("%s: not valid UTF-8 JSON (%s)" % (label, error))


def read_file(path, label):
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise WalkthroughError("%s: cannot read %s (%s)" % (label, path, error.strerror))


def load_schema(name):
    return parse_json(read_file(SCHEMA_DIR / name, "schema"), name)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def text_sha256(text):
    """Hash of the text as UTF-8, without Unicode normalization."""
    return sha256_hex(text.encode("utf-8"))


def split_lines(text):
    """Split on LF only, the way Git counts lines. A final newline adds no line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def lines_with_endings(text):
    """Like split_lines, but each line keeps the newline it had in the text."""
    lines = split_lines(text)
    endings = ["\n"] * len(lines)
    if lines and not text.endswith("\n"):
        endings[-1] = ""
    return [line + ending for line, ending in zip(lines, endings)]


# ---------------------------------------------------------------------------
# JSON Schema subset
#
# Supports exactly the keywords the bundled schemas use. Anything else raises,
# so a schema change can never be silently ignored.
# ---------------------------------------------------------------------------

_ANNOTATION_KEYWORDS = {"$schema", "title", "description"}
_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}
_DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})"
)


def _anchored(pattern):
    # Python's "$" also matches before a trailing newline; JSON Schema's does not.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def _check_type(value, expected, schema, where):
    if not _TYPE_CHECKS[expected](value):
        return ["%s: expected %s" % (where, expected)]
    return []


def _check_const(value, expected, schema, where):
    return [] if value == expected else ["%s: must be %r" % (where, expected)]


def _check_enum(value, expected, schema, where):
    return [] if value in expected else ["%s: must be one of %s" % (where, ", ".join(map(str, expected)))]


def _check_pattern(value, expected, schema, where):
    if isinstance(value, str) and not re.search(_anchored(expected), value):
        return ["%s: does not match %s" % (where, expected)]
    return []


def _check_format(value, expected, schema, where):
    if expected != "date-time":
        raise WalkthroughError("schema format not supported by this validator: %s" % expected)
    if isinstance(value, str) and not _DATE_TIME.fullmatch(value):
        return ["%s: not an RFC 3339 date-time" % where]
    return []


def _check_min_length(value, expected, schema, where):
    if isinstance(value, str) and len(value) < expected:
        return ["%s: must have at least %d character(s)" % (where, expected)]
    return []


def _check_min_items(value, expected, schema, where):
    if isinstance(value, list) and len(value) < expected:
        return ["%s: must have at least %d item(s)" % (where, expected)]
    return []


def _check_minimum(value, expected, schema, where):
    if _TYPE_CHECKS["integer"](value) and value < expected:
        return ["%s: must be at least %d" % (where, expected)]
    return []


def _check_unique_items(value, expected, schema, where):
    if expected and isinstance(value, list):
        seen = [json.dumps(item, sort_keys=True) for item in value]
        if len(seen) != len(set(seen)):
            return ["%s: items must be unique" % where]
    return []


def _check_required(value, expected, schema, where):
    if not isinstance(value, dict):
        return []
    return ["%s: missing required field %s" % (where, key) for key in expected if key not in value]


def _check_properties(value, expected, schema, where):
    errors = []
    if isinstance(value, dict):
        for key, subschema in expected.items():
            if key in value:
                errors.extend(schema_errors(value[key], subschema, "%s.%s" % (where, key)))
    return errors


def _check_additional_properties(value, expected, schema, where):
    if expected is not False:
        raise WalkthroughError("schema additionalProperties must be false for this validator")
    if not isinstance(value, dict):
        return []
    known = schema.get("properties", {})
    return ["%s: unexpected field %s" % (where, key) for key in value if key not in known]


def _check_items(value, expected, schema, where):
    errors = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, expected, "%s[%d]" % (where, index)))
    return errors


def _check_any_of(value, expected, schema, where):
    if any(not schema_errors(value, branch, where) for branch in expected):
        return []
    return ["%s: matches none of the allowed forms" % where]


def _check_one_of(value, expected, schema, where):
    results = [schema_errors(value, branch, where) for branch in expected]
    matches = sum(1 for errors in results if not errors)
    if matches == 1:
        return []
    if matches > 1:
        return ["%s: matches more than one allowed form" % where]
    # Report the branch selected by its "kind" tag, which is far more useful
    # than "matches none".
    kind = value.get("kind") if isinstance(value, dict) else None
    for branch, errors in zip(expected, results):
        if branch.get("properties", {}).get("kind", {}).get("const") == kind:
            return errors
    return ["%s: unknown kind %r" % (where, kind)]


_KEYWORD_CHECKS = {
    "type": _check_type,
    "const": _check_const,
    "enum": _check_enum,
    "pattern": _check_pattern,
    "format": _check_format,
    "minLength": _check_min_length,
    "minItems": _check_min_items,
    "minimum": _check_minimum,
    "uniqueItems": _check_unique_items,
    "required": _check_required,
    "properties": _check_properties,
    "additionalProperties": _check_additional_properties,
    "items": _check_items,
    "anyOf": _check_any_of,
    "oneOf": _check_one_of,
}


def schema_errors(value, schema, where="$"):
    errors = []
    for keyword, expected in schema.items():
        if keyword in _ANNOTATION_KEYWORDS:
            continue
        check = _KEYWORD_CHECKS.get(keyword)
        if check is None:
            raise WalkthroughError("schema keyword not supported by this validator: %s" % keyword)
        errors.extend(check(value, expected, schema, where))
    return errors


# ---------------------------------------------------------------------------
# Diff hunks
# ---------------------------------------------------------------------------

HUNK_HEADER = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<context>.*)"
)


def _hunk_range(match, side):
    count = match.group(side + "_count")
    return {"start": int(match.group(side + "_start")), "count": 1 if count is None else int(count)}


def parse_hunk(text):
    """Return (old_range, new_range, rows) for one unified-diff hunk.

    rows is a list of (marker, old_line_number, new_line_number, content).
    Raises ValueError when the body does not match the header.
    """
    lines = split_lines(text)
    match = HUNK_HEADER.fullmatch(lines[0]) if lines else None
    if match is None:
        raise ValueError("missing or malformed hunk header")
    old_range, new_range = _hunk_range(match, "old"), _hunk_range(match, "new")
    old_number, new_number = old_range["start"], new_range["start"]
    old_seen = new_seen = 0
    rows = []
    for line in lines[1:]:
        marker, content = line[:1], line[1:]
        if marker == " ":
            rows.append((marker, old_number, new_number, content))
            old_number, new_number = old_number + 1, new_number + 1
            old_seen, new_seen = old_seen + 1, new_seen + 1
        elif marker == "-":
            rows.append((marker, old_number, None, content))
            old_number, old_seen = old_number + 1, old_seen + 1
        elif marker == "+":
            rows.append((marker, None, new_number, content))
            new_number, new_seen = new_number + 1, new_seen + 1
        elif marker == "\\":
            rows.append((marker, None, None, content))
        else:
            raise ValueError("hunk line without a diff marker")
    if (old_seen, new_seen) != (old_range["count"], new_range["count"]):
        raise ValueError("hunk body does not match the line counts in its header")
    return old_range, new_range, rows


# ---------------------------------------------------------------------------
# Semantic validation
# ---------------------------------------------------------------------------

# status -> (old side present, new side present)
_SIDES_BY_STATUS = {
    "added": (False, True),
    "deleted": (True, False),
    "modified": (True, True),
    "renamed": (True, True),
    "copied": (True, True),
    "type-changed": (True, True),
}


def _index_by_id(items, label, errors):
    indexed = {}
    for item in items:
        if item["id"] in indexed:
            errors.append("%s: duplicate id %s" % (label, item["id"]))
        indexed[item["id"]] = item
    return indexed


def _hash_error(record):
    try:
        actual = text_sha256(record["text"])
    except UnicodeEncodeError:
        return "%s: text is not encodable as UTF-8" % record["id"]
    if actual != record["text_sha256"]:
        return "%s: text does not match its recorded hash" % record["id"]
    return None


def _file_errors(file, oid_length):
    errors = []
    for side, present in zip(("old", "new"), _SIDES_BY_STATUS[file["status"]]):
        values = [file[side + "_path"], file[side + "_blob_id"], file[side + "_mode"]]
        if present and any(value is None for value in values):
            errors.append("%s: status %s needs %s path, blob and mode" % (file["id"], file["status"], side))
        if not present and any(value is not None for value in values):
            errors.append("%s: status %s cannot have %s path, blob or mode" % (file["id"], file["status"], side))
        blob = file[side + "_blob_id"]
        if blob is not None and len(blob) != oid_length:
            errors.append("%s: %s blob id does not fit the object format" % (file["id"], side))
    if file["status"] == "renamed" and file["old_path"] == file["new_path"]:
        errors.append("%s: renamed file has identical paths" % file["id"])
    return errors


def _source_errors(record, file, evidence):
    errors = []
    side = "old" if record["side"] == "base" else "new"
    if record["revision"] != evidence[record["side"] + "_sha"]:
        errors.append("%s: revision is not the %s commit" % (record["id"], record["side"]))
    if file is not None and record["blob_id"] != file[side + "_blob_id"]:
        errors.append("%s: blob is not the %s blob of %s" % (record["id"], record["side"], file["id"]))
    start, end = record["lines"]["start"], record["lines"]["end"]
    if start > end:
        errors.append("%s: line range ends before it starts" % record["id"])
    elif len(split_lines(record["text"])) != end - start + 1:
        errors.append("%s: text does not have the %d line(s) its range claims" % (record["id"], end - start + 1))
    return errors


def _diff_errors(record, evidence):
    errors = []
    for key in ("base_sha", "final_sha"):
        if record[key] != evidence[key]:
            errors.append("%s: %s differs from the manifest" % (record["id"], key))
    try:
        old_range, new_range, _ = parse_hunk(record["text"])
    except ValueError as error:
        errors.append("%s: %s" % (record["id"], error))
    else:
        if (old_range, new_range) != (record["old_range"], record["new_range"]):
            errors.append("%s: ranges differ from the hunk header" % record["id"])
    return errors


def check_result_errors(check, label):
    """Rules a check result must satisfy, whether supplied or already recorded."""
    errors = []
    if check["status"] == "not-run" and (check["tested_revision"] is not None or check["exit_code"] is not None):
        errors.append("%s: a not-run check cannot have a tested revision or exit code" % label)
    if check["status"] == "passed" and check["exit_code"] not in (None, 0):
        errors.append("%s: a passed check cannot have a non-zero exit code" % label)
    return errors


def _check_record_errors(record, evidence):
    errors = check_result_errors(record, record["id"])
    if record["applies_to_final"] and record["tested_revision"] != evidence["final_sha"]:
        errors.append("%s: applies_to_final needs tested_revision equal to final_sha" % record["id"])
    return errors


def _linkage_errors(files, records):
    """Files and evidence must point at each other consistently."""
    errors = []
    for file in files.values():
        for evidence_id in file["evidence_ids"]:
            record = records.get(evidence_id)
            if record is None:
                errors.append("%s: lists unknown evidence %s" % (file["id"], evidence_id))
            elif file["id"] not in _record_file_ids(record):
                errors.append("%s: lists %s, which belongs to another file" % (file["id"], evidence_id))
    for record in records.values():
        for file_id in _record_file_ids(record):
            if file_id not in files:
                errors.append("%s: refers to unknown file %s" % (record["id"], file_id))
            elif record["id"] not in files[file_id]["evidence_ids"]:
                errors.append("%s: is not listed by its file %s" % (record["id"], file_id))
    return errors


def _record_file_ids(record):
    return record["file_ids"] if record["kind"] == "check" else [record["file_id"]]


def evidence_errors(evidence):
    """Cross-record checks for a schema-valid evidence manifest."""
    errors = []
    oid_length = OID_LENGTHS[evidence["object_format"]]
    review = evidence["review"]
    commit_ids = {
        "base_sha": evidence["base_sha"],
        "final_sha": evidence["final_sha"],
        "review.reviewed_base_sha": review["reviewed_base_sha"],
        "review.reviewed_sha": review["reviewed_sha"],
    }
    for label, value in commit_ids.items():
        if len(value) != oid_length:
            errors.append("%s: does not fit object format %s" % (label, evidence["object_format"]))
    if review["reviewed_base_sha"] != evidence["base_sha"]:
        errors.append("review: reviewed base differs from base_sha")
    if review["reviewed_sha"] != evidence["final_sha"]:
        errors.append("review: reviewed revision differs from final_sha")

    worktree = evidence["worktree"]
    if (worktree["state"] == "clean") == bool(worktree["excluded_changes"]):
        errors.append("worktree: state and excluded_changes disagree")

    files = _index_by_id(evidence["files"], "files", errors)
    records = _index_by_id(evidence["evidence"], "evidence", errors)
    for file in files.values():
        errors.extend(_file_errors(file, oid_length))
    for record in records.values():
        kind = record["kind"]
        if "text" in record:
            problem = _hash_error(record)
            if problem:
                errors.append(problem)
        if kind == "source":
            errors.extend(_source_errors(record, files.get(record["file_id"]), evidence))
        elif kind == "diff":
            errors.extend(_diff_errors(record, evidence))
        elif kind == "check":
            errors.extend(_check_record_errors(record, evidence))
    errors.extend(_linkage_errors(files, records))
    return errors


def narrative_errors(narrative, evidence, evidence_digest):
    """Checks that bind a schema-valid narrative to one exact evidence file."""
    errors = []
    if narrative["evidence_sha256"] != evidence_digest:
        errors.append("narrative: evidence_sha256 does not match the evidence file (%s)" % evidence_digest)
    for key in ("base_sha", "final_sha", "synthetic"):
        if narrative[key] != evidence[key]:
            errors.append("narrative: %s differs from the evidence" % key)

    records = {record["id"]: record for record in evidence["evidence"]}
    file_ids = [file["id"] for file in evidence["files"]]
    sections = _index_by_id(narrative["sections"], "sections", errors)
    for section in sections.values():
        for evidence_id in section["evidence_ids"] + section["deeper_evidence_ids"]:
            if evidence_id not in records:
                errors.append("%s: refers to unknown evidence %s" % (section["id"], evidence_id))

    covered = Counter(entry["file_id"] for entry in narrative["file_coverage"])
    for file_id in file_ids:
        if covered[file_id] != 1:
            errors.append("file_coverage: %s must be covered exactly once, found %d" % (file_id, covered[file_id]))
    for entry in narrative["file_coverage"]:
        if entry["file_id"] not in file_ids:
            errors.append("file_coverage: unknown file %s" % entry["file_id"])
        for section_id in entry["section_ids"]:
            if section_id not in sections:
                errors.append("file_coverage: %s refers to unknown section %s" % (entry["file_id"], section_id))
        if entry["category"] == "explained" and not entry["section_ids"]:
            errors.append("file_coverage: %s is marked explained but names no section" % entry["file_id"])

    for note in narrative["test_notes"]:
        record = records.get(note["evidence_id"])
        if record is None or record["kind"] != "check":
            errors.append("test_notes: %s is not a check in the evidence" % note["evidence_id"])
    return errors


def review_is_clean(evidence):
    return evidence["review"]["verdict"] == ACCEPTED_VERDICT


def release_errors(evidence, allow_synthetic, diagnostic):
    """Conditions under which a bundle must not become a walkthrough."""
    errors = []
    if not review_is_clean(evidence) and not diagnostic:
        errors.append(
            "review verdict is %s; a final walkthrough needs %s (--diagnostic renders a labeled, non-final page)"
            % (evidence["review"]["verdict"], ACCEPTED_VERDICT)
        )
    if evidence["synthetic"] and not allow_synthetic:
        errors.append("evidence is synthetic; pass --allow-synthetic only for demos and tests")
    return errors


def load_evidence(data):
    evidence = parse_json(data, "evidence")
    errors = schema_errors(evidence, load_schema(EVIDENCE_SCHEMA), "evidence")
    if not errors:
        errors = evidence_errors(evidence)
    if errors:
        raise WalkthroughError(errors)
    return evidence


def load_bundle(evidence_data, narrative_data, allow_synthetic=False, diagnostic=False):
    """Validate an evidence file and its narrative together. Returns both."""
    evidence = load_evidence(evidence_data)
    narrative = parse_json(narrative_data, "narrative")
    errors = schema_errors(narrative, load_schema(NARRATIVE_SCHEMA), "narrative")
    if not errors:
        errors = narrative_errors(narrative, evidence, sha256_hex(evidence_data))
    errors.extend(release_errors(evidence, allow_synthetic, diagnostic))
    if errors:
        raise WalkthroughError(errors)
    return evidence, narrative


# ---------------------------------------------------------------------------
# Redaction
#
# Pattern scanning is incomplete by nature. The output always says so.
# ---------------------------------------------------------------------------

REDACTION_MARK = "[REDACTED]"
SENSITIVE_PATH_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".netrc", ".pypirc",
    "credentials", "credentials.*", "secrets.*", "*.tfvars", "*.tfstate",
)
# (label, pattern, group to mask)
_SECRET_PATTERNS = (
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"), 0),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), 0),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), 0),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), 0),
    ("url-credential", re.compile(r"://[^/\s:@]+:([^@\s/]{3,})@"), 1),
    (
        "quoted-secret",
        re.compile(
            r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
            r"\w*[\"']?\s*[:=]\s*([\"'])([^\"'\s]{8,})\1"
        ),
        2,
    ),
    (
        "env-secret",
        re.compile(r"\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*=[\"']?([^\s\"']{8,})"),
        1,
    ),
)
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
_BARE_BASE64 = re.compile(r"\s*[A-Za-z0-9+/]{40,}={0,2}\s*")


def is_sensitive_path(path):
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, glob) for glob in SENSITIVE_PATH_GLOBS)


def _mask_group(match, group):
    whole, offset = match.group(0), match.start(0)
    start, end = match.span(group)
    return whole[: start - offset] + REDACTION_MARK + whole[end - offset :]


def _redact_line(body, in_key_block, found):
    """Return (redacted body, still inside a private key block)."""
    if in_key_block or _KEY_BEGIN.search(body):
        found["private-key"] += 1
        return REDACTION_MARK, not _KEY_END.search(body)
    if _BARE_BASE64.fullmatch(body):
        found["possible-key-material"] += 1
        return REDACTION_MARK, False
    for label, pattern, group in _SECRET_PATTERNS:
        body, count = pattern.subn(lambda match, group=group: _mask_group(match, group), body)
        found[label] += count
    return body, False


def redact_text(text, is_hunk=False):
    """Mask likely secrets without changing the number of lines.

    Returns (sanitized text, Counter of redactions by label). In a hunk the
    diff marker and the numeric part of the header are left untouched.
    """
    found = Counter()
    in_key_block = False
    lines = text.split("\n")
    for index, line in enumerate(lines):
        prefix, body = "", line
        if is_hunk:
            header = HUNK_HEADER.fullmatch(line)
            if header:
                prefix, body = line[: header.start("context")], header.group("context")
            elif line.startswith("\\"):
                continue
            else:
                prefix, body = line[:1], line[1:]
        ending = "\r" if body.endswith("\r") else ""
        body, in_key_block = _redact_line(body[: len(body) - len(ending)], in_key_block, found)
        lines[index] = prefix + body + ending
    return "\n".join(lines), +found


# ---------------------------------------------------------------------------
# Git access
# ---------------------------------------------------------------------------


# Repository or user configuration must not run programs or reshape the output.
_GIT_OVERRIDES = (
    "core.fsmonitor=false",
    "core.quotepath=false",
    "diff.suppressBlankEmpty=false",
    "diff.algorithm=myers",
)


class Git:
    """Read-only Git access through argument arrays, never a shell string."""

    def __init__(self, repo):
        self.repo = str(repo)
        self.env = dict(
            os.environ,
            GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0",
            GIT_PAGER="cat",
            LC_ALL="C",
        )

    def _run(self, args):
        command = ["git"]
        for setting in _GIT_OVERRIDES:
            command += ["-c", setting]
        return subprocess.run(
            command + list(args), cwd=self.repo, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def output(self, *args):
        result = self._run(args)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise WalkthroughError("git %s failed: %s" % (args[0], detail))
        return result.stdout

    def text(self, *args):
        return self.output(*args).decode("utf-8", "replace").strip()

    def succeeds(self, *args):
        return self._run(args).returncode == 0


_DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color", "--text", "--no-renames")
_STATUS_NAMES = {
    "A": "added", "M": "modified", "D": "deleted",
    "R": "renamed", "C": "copied", "T": "type-changed",
}
_SUBMODULE_MODE, _SYMLINK_MODE = "160000", "120000"


def require_commit(git, value, label, oid_length):
    if not re.fullmatch("[0-9a-f]{%d}" % oid_length, value):
        raise WalkthroughError("%s must be a full %d-character commit ID" % (label, oid_length))
    if not git.succeeds("cat-file", "-e", value + "^{commit}") or git.text("cat-file", "-t", value) != "commit":
        raise WalkthroughError("%s is not a commit in this repository: %s" % (label, value))


def _display_path(raw):
    """Paths are bytes in Git. Keep an unambiguous text form for the manifest."""
    return raw.decode("utf-8", "backslashreplace")


def _absent_to_none(value):
    return None if set(value) == {"0"} else value


def changed_files(git, base, final):
    """The complete base-to-final file inventory, from the two trees.

    Returns (file records, raw path bytes by file id). The raw path is what Git
    needs as a pathspec; the record holds a text form that is safe in JSON.
    """
    raw = git.output("diff-tree", "-r", "-z", "-M", "--raw", "--no-abbrev", base, final)
    tokens = raw.split(b"\0")
    files, raw_paths, index = [], {}, 0
    while index < len(tokens) and tokens[index]:
        old_mode, new_mode, old_blob, new_blob, status = tokens[index][1:].decode("ascii").split(" ")
        if status[0] not in _STATUS_NAMES:
            raise WalkthroughError("unsupported change status %s; resolve the range first" % status)
        paths = 2 if status[0] in "RC" else 1
        old_raw, new_raw = tokens[index + 1], tokens[index + paths]
        index += 1 + paths
        name = _STATUS_NAMES[status[0]]
        has_old, has_new = _SIDES_BY_STATUS[name]
        file_id = "F%02d" % (len(files) + 1)
        raw_paths[file_id] = new_raw if has_new else old_raw
        files.append({
            "id": file_id,
            "status": name,
            "old_path": _display_path(old_raw) if has_old else None,
            "new_path": _display_path(new_raw) if has_new else None,
            "old_blob_id": _absent_to_none(old_blob),
            "new_blob_id": _absent_to_none(new_blob),
            "old_mode": _absent_to_none(old_mode),
            "new_mode": _absent_to_none(new_mode),
            "evidence_ids": [],
        })
    return files, raw_paths


def worktree_state(git):
    """Uncommitted changes are never part of the walkthrough; list them."""
    tokens = git.output("status", "--porcelain=v1", "-z").split(b"\0")
    changes, index = [], 0
    while index < len(tokens) and tokens[index]:
        entry = tokens[index]
        code, path = entry[:2].decode("ascii"), _display_path(entry[3:])
        index += 2 if "R" in code or "C" in code else 1
        changes.append("%s %s" % (code.replace(" ", "."), path))
    return {"state": "excluded-changes" if changes else "clean", "excluded_changes": changes}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class EvidenceBuilder:
    """Collects evidence records, assigns IDs, and applies redaction."""

    def __init__(self, base, final):
        self.base, self.final = base, final
        self.records = []
        self.redaction_notes = []
        self.text_bytes = 0

    def _add(self, record, file=None):
        record["id"] = "E%02d" % (len(self.records) + 1)
        self.records.append(record)
        if file is not None:
            file["evidence_ids"].append(record["id"])
        return record["id"]

    def _add_text(self, record, text, where, file=None, is_hunk=False):
        """Store a record whose text is shown to readers: redact, hash, note."""
        sanitized, found = redact_text(text, is_hunk)
        record["text"], record["text_sha256"] = sanitized, text_sha256(sanitized)
        self.text_bytes += len(sanitized.encode("utf-8"))
        record_id = self._add(record, file)
        for label, count in sorted(found.items()):
            self.redaction_notes.append("%s (%s): %d %s span(s) redacted" % (record_id, where, count, label))

    def add_hunk(self, file, number, text):
        old_range, new_range, _ = parse_hunk(text)
        record = {
            "kind": "diff", "file_id": file["id"], "base_sha": self.base, "final_sha": self.final,
            "hunk_id": "H%02d" % number, "old_range": old_range, "new_range": new_range,
        }
        self._add_text(record, text, file["id"], file, is_hunk=True)

    def add_source(self, file, side, start, end, text):
        blob_side = "old" if side == "base" else "new"
        record = {
            "kind": "source", "file_id": file["id"], "side": side,
            "revision": self.base if side == "base" else self.final,
            "blob_id": file[blob_side + "_blob_id"], "lines": {"start": start, "end": end},
        }
        self._add_text(record, text, file["id"], file)

    def add_omission(self, file, reason, explanation):
        self._add({
            "kind": "omission", "file_id": file["id"], "reason": reason, "explanation": explanation,
            "essential_evidence_missing": reason in _ESSENTIAL_OMISSIONS,
        }, file)

    def add_check(self, check):
        tested = check["tested_revision"]
        if tested is None:
            applicability = "No tested revision was recorded."
        elif tested == self.final:
            applicability = "The recorded tested revision equals final_sha."
        else:
            applicability = "The check ran at %s, which is not final_sha." % tested
        output, limits = _bounded_output(check["output"])
        record = {
            "kind": "check", "file_ids": [], "method": check["method"], "status": check["status"],
            "tested_revision": tested, "applies_to_final": tested == self.final,
            "applicability_evidence": applicability + " Supplied to the extractor, not run by it.",
            "exit_code": check["exit_code"], "captured_at": check["captured_at"],
            "coverage_limits": list(check["coverage_limits"]) + limits,
        }
        self._add_text(record, output, "check output")


# Omissions that leave a reader unable to see content they would expect to see.
_ESSENTIAL_OMISSIONS = {"oversized", "unsupported-encoding", "secret-redaction"}


def _bounded_output(output):
    """Keep the end of long check output and say plainly that the rest was cut."""
    data = output.encode("utf-8")
    if len(data) <= MAX_CHECK_OUTPUT_BYTES:
        return output, []
    tail = data[-MAX_CHECK_OUTPUT_BYTES:].decode("utf-8", "ignore")
    tail = tail[tail.find("\n") + 1 :]
    cut = len(data) - len(tail.encode("utf-8"))
    marker = "[extractor: the first %d bytes of this output were omitted]\n" % cut
    return marker + tail, ["Output truncated by the extractor: first %d bytes omitted." % cut]


def _blob_text(git, blob):
    """Return (text, None) or (None, (omission reason, explanation))."""
    if blob is None:
        return "", None
    if int(git.text("cat-file", "-s", blob)) > MAX_BLOB_BYTES:
        return None, ("oversized", "Blob is larger than %d bytes." % MAX_BLOB_BYTES)
    data = git.output("cat-file", "blob", blob)
    if b"\0" in data:
        return None, ("binary", "Binary content is not shown.")
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, ("unsupported-encoding", "Content is not valid UTF-8 and is not shown.")


def _metadata_omission(file):
    modes = (file["old_mode"], file["new_mode"])
    if _SUBMODULE_MODE in modes:
        return "submodule", "Submodule pointer change; the submodule's content is not read."
    if _SYMLINK_MODE in modes:
        return "symlink", "Symbolic link; the link is not followed and its target is not shown."
    if any(path is not None and is_sensitive_path(path) for path in (file["old_path"], file["new_path"])):
        return "secret-redaction", "Path matches a sensitive-file pattern; content omitted by default."
    return None


def _file_hunks(git, file, raw_path, base, final, context):
    """Real Git hunks for one text file, as a list of hunk texts."""
    options = list(_DIFF_OPTIONS) + ["-U%d" % context]
    if file["old_blob_id"] and file["new_blob_id"]:
        if file["old_blob_id"] == file["new_blob_id"]:
            return []
        patch = git.output("diff", *options, file["old_blob_id"], file["new_blob_id"])
    else:
        # A whole-file add or delete has no second blob to compare against.
        patch = git.output("diff", *options, base, final, "--", b":(top,literal)" + raw_path)
    hunks = []
    for line in split_lines(patch.decode("utf-8")):
        if line.startswith("@@ "):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    return ["\n".join(lines) + "\n" for lines in hunks]


def _extract_file(git, file, raw_path, builder, limits, context):
    """Add the evidence for one changed file. Returns its text per side, if shown."""
    omission = _metadata_omission(file)
    texts = {}
    for side in ("old", "new"):
        if omission is None:
            texts[side], omission = _blob_text(git, file[side + "_blob_id"])
    if omission is not None:
        builder.add_omission(file, *omission)
        return {}
    hunks = _file_hunks(git, file, raw_path, builder.base, builder.final, context)
    size = sum(len(hunk.encode("utf-8")) for hunk in hunks)
    if not hunks:
        builder.add_omission(file, "other", "No textual hunks: empty content, or a mode or rename-only change.")
    elif size > limits["file"] or builder.text_bytes + size > limits["total"]:
        which = "per-file" if size > limits["file"] else "total"
        builder.add_omission(file, "oversized", "Diff is %d bytes, over the %s evidence limit." % (size, which))
    else:
        for number, hunk in enumerate(hunks, 1):
            builder.add_hunk(file, number, hunk)
    return texts


def _parse_source_request(request):
    try:
        path, side, span = request.rsplit(":", 2)
        start, end = (int(part) for part in span.split("-"))
    except ValueError:
        side = None
    if side not in ("base", "final"):
        raise WalkthroughError("--source must look like PATH:base|final:START-END, got %s" % request)
    return path, side, start, end


def _add_requested_sources(requests, files, texts_by_file, builder, limits):
    """--source PATH:SIDE:START-END adds an exact range from a changed text file."""
    for request in requests:
        path, side, start, end = _parse_source_request(request)
        key = "old" if side == "base" else "new"
        file = next((item for item in files if item[key + "_path"] == path), None)
        if file is None:
            raise WalkthroughError("--source %s: no changed file has that path on that side" % request)
        text = texts_by_file[file["id"]].get(key)
        if text is None:
            raise WalkthroughError("--source %s: that file's content is omitted from evidence" % request)
        lines = lines_with_endings(text)
        if not 1 <= start <= end <= len(lines):
            raise WalkthroughError("--source %s: range is outside the file's %d line(s)" % (request, len(lines)))
        chosen = "".join(lines[start - 1 : end])
        if builder.text_bytes + len(chosen.encode("utf-8")) > limits["total"]:
            raise WalkthroughError("--source %s: would exceed the total evidence limit" % request)
        builder.add_source(file, side, start, end, chosen)


def build_evidence(repo, base, final, inputs, sources=(), context=DEFAULT_CONTEXT_LINES, limits=None):
    """Build the evidence manifest for base..final from Git objects."""
    limits = limits or {"file": MAX_FILE_TEXT_BYTES, "total": MAX_TOTAL_TEXT_BYTES}
    git = Git(repo)
    if not git.succeeds("rev-parse", "--git-dir"):
        raise WalkthroughError("not a Git repository: %s" % repo)
    object_format = git.text("rev-parse", "--show-object-format")
    oid_length = OID_LENGTHS[object_format]
    require_commit(git, base, "--base", oid_length)
    require_commit(git, final, "--final", oid_length)
    if not git.succeeds("merge-base", "--is-ancestor", base, final):
        raise WalkthroughError("base is not an ancestor of final; resolve the intended range first")
    review = inputs["review"]
    if (review["reviewed_base_sha"], review["reviewed_sha"]) != (base, final):
        raise WalkthroughError("the review record covers a different base or final revision than this range")

    check_problems = []
    for number, check in enumerate(inputs["checks"], 1):
        check_problems.extend(check_result_errors(check, "inputs: check %d (%s)" % (number, check["method"])))
    if check_problems:
        raise WalkthroughError(check_problems)

    files, raw_paths = changed_files(git, base, final)
    builder = EvidenceBuilder(base, final)
    texts_by_file = {
        file["id"]: _extract_file(git, file, raw_paths[file["id"]], builder, limits, context) for file in files
    }
    _add_requested_sources(sources, files, texts_by_file, builder, limits)
    for check in inputs["checks"]:
        builder.add_check(check)

    coverage = list(inputs["coverage_limits"])
    coverage.append("Secret redaction is pattern-based and can miss secrets; review before sharing.")
    evidence = {
        "schema_version": "1.0",
        "synthetic": False,
        "repository_label": inputs.get("repository_label") or Path(git.text("rev-parse", "--show-toplevel")).name,
        "object_format": object_format,
        "base_sha": base,
        "final_sha": final,
        "base_selection_reason": inputs["base_selection_reason"],
        "review": review,
        "worktree": worktree_state(git),
        "files": files,
        "evidence": builder.records,
        "redaction_notes": builder.redaction_notes,
        "coverage_limits": coverage,
    }
    # The extractor must never emit a manifest its own validator would reject.
    problems = schema_errors(evidence, load_schema(EVIDENCE_SCHEMA), "evidence") or evidence_errors(evidence)
    if problems:
        raise WalkthroughError(["internal error: extracted evidence is invalid"] + problems)
    return evidence


def serialize(document):
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Rendering
#
# One fixed template. Every untrusted string goes through esc() exactly once.
# No script, no external resource, no form.
# ---------------------------------------------------------------------------

_BIDI_CONTROLS = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F}


def visible(text, keep="\n\t"):
    """Show control and direction-override characters as inert escapes."""
    out = []
    for char in text:
        code = ord(char)
        hidden = code < 0x20 or 0x7F <= code <= 0x9F or code in _BIDI_CONTROLS or 0xD800 <= code <= 0xDFFF
        if char not in keep and hidden:
            out.append("\\x%02x" % code if code < 0x100 else "\\u%04x" % code)
        else:
            out.append(char)
    return "".join(out)


def esc(text, keep="\n\t"):
    return html.escape(visible(str(text), keep), quote=True)


CSS = """
:root{--bg:#fbfbf9;--fg:#1c1c1a;--muted:#5d5d57;--line:#d8d8d0;--panel:#f1f1ec;--add:#e3f3e3;--del:#f9e2e2;--warn:#fff3cd;--warn-line:#b58900;--link:#1a4f8b}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#e9e9e4;--muted:#a3a39b;--line:#3a3a35;--panel:#20201d;--add:#16301a;--del:#3a1c1c;--warn:#3a3000;--warn-line:#d9a400;--link:#8bb9f0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.page{max-width:60rem;margin:0 auto;padding:1.5rem 1rem 4rem}
h1{font-size:1.7rem;line-height:1.25;margin:0 0 .5rem}
h2{font-size:1.3rem;margin:2.5rem 0 .75rem;padding-top:.75rem;border-top:1px solid var(--line)}
h3{font-size:1.1rem;margin:1.75rem 0 .5rem}
h4{font-size:.8rem;margin:1rem 0 .15rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
p,li,dd{overflow-wrap:anywhere}
.prose{white-space:pre-wrap;margin:.25rem 0 .75rem}
a{color:var(--link)}
a:focus-visible,summary:focus-visible{outline:3px solid var(--link);outline-offset:2px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85rem}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem;margin:1rem 0}
.meta dt{color:var(--muted)}
.meta dd{margin:0}
.notice{background:var(--warn);border-left:4px solid var(--warn-line);padding:.6rem .9rem;margin:1rem 0}
details{border:1px solid var(--line);border-radius:6px;margin:.6rem 0;background:var(--panel)}
summary{cursor:pointer;padding:.5rem .75rem;font-weight:600}
details>.body{padding:0 .75rem .75rem}
.scroll{overflow-x:auto;background:var(--bg);border-top:1px solid var(--line)}
table.code{border-collapse:collapse;width:100%}
table.code td{padding:0 .5rem;vertical-align:top;white-space:pre}
table.code td.ln{text-align:right;color:var(--muted);user-select:none;border-right:1px solid var(--line)}
tr.add td.src{background:var(--add)}
tr.del td.src{background:var(--del)}
tr.hdr td{color:var(--muted)}
table.list{border-collapse:collapse;width:100%}
table.list th,table.list td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
.status{font-weight:700;text-transform:uppercase;font-size:.8rem;letter-spacing:.04em}
nav ol{padding-left:1.25rem}
footer{margin-top:3rem;color:var(--muted);font-size:.85rem}
"""


def content_security_policy():
    digest = base64.b64encode(hashlib.sha256(CSS.encode("utf-8")).digest()).decode("ascii")
    return "default-src 'none'; style-src 'sha256-%s'; base-uri 'none'; form-action 'none'" % digest


def _prose(text):
    return '<p class="prose">%s</p>' % esc(text, keep="\n") if text else ""


def _items(values, empty="None recorded."):
    if not values:
        return "<p>%s</p>" % esc(empty)
    return "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(value, keep="") for value in values)


def _file_label(file):
    old, new = file["old_path"], file["new_path"]
    if old and new and old != new:
        return "%s (was %s)" % (new, old)
    return new or old


def _code_row(css_class, old_number, new_number, content):
    numbers = "".join('<td class="ln">%s</td>' % ("" if number is None else number) for number in (old_number, new_number))
    return '<tr class="%s">%s<td class="src">%s</td></tr>' % (css_class, numbers, esc(content.rstrip("\r"), keep="\t"))


def _code_table(rows):
    return '<div class="scroll"><table class="code mono">%s</table></div>' % "".join(rows)


def _diff_rows(record):
    _, _, rows = parse_hunk(record["text"])
    classes = {"+": "add", "-": "del", " ": "ctx", "\\": "hdr"}
    header = split_lines(record["text"])[0]
    out = [_code_row("hdr", None, None, header)]
    for marker, old_number, new_number, content in rows:
        out.append(_code_row(classes[marker], old_number, new_number, marker + content))
    return out


def _source_rows(record):
    start = record["lines"]["start"]
    side_is_base = record["side"] == "base"
    rows = []
    for offset, line in enumerate(split_lines(record["text"])):
        number = start + offset
        rows.append(_code_row("ctx", number if side_is_base else None, None if side_is_base else number, line))
    return rows


def _check_summary(record):
    applies = "applies to the final revision" if record["applies_to_final"] else "does NOT apply to the final revision"
    return "%s: %s (%s)" % (record["status"], record["method"], applies)


def _evidence_block(record, files):
    """One expandable block. The summary line is derived from the manifest."""
    kind = record["kind"]
    if kind == "check":
        summary, body = "Check " + _check_summary(record), _code_table(
            [_code_row("ctx", None, None, line) for line in split_lines(record["text"])]
        )
    else:
        label = _file_label(files[record["file_id"]])
        if kind == "diff":
            summary = "Diff of %s, hunk %s" % (label, record["hunk_id"])
            body = _code_table(_diff_rows(record))
        elif kind == "source":
            lines = record["lines"]
            summary = "%s at the %s revision, lines %d-%d" % (label, record["side"], lines["start"], lines["end"])
            body = _code_table(_source_rows(record))
        else:
            summary = "Not shown: %s (%s)" % (label, record["reason"])
            body = '<div class="body">%s</div>' % _prose(record["explanation"])
    return "<details><summary>%s <span class=\"mono\">%s</span></summary>%s</details>" % (
        esc(summary, keep=""), esc(record["id"]), body,
    )


def _section_html(section, records, files):
    parts = ['<article id="%s"><h3>%s</h3>' % (esc(section["id"]), esc(section["title"], keep=""))]
    for heading, key in (("Before", "before"), ("After", "after"), ("Why", "why")):
        if section[key]:
            parts.append("<h4>%s</h4>%s" % (heading, _prose(section[key])))
    parts.extend(_evidence_block(records[evidence_id], files) for evidence_id in section["evidence_ids"])
    if section["deeper_explanation"] or section["deeper_evidence_ids"]:
        inner = _prose(section["deeper_explanation"]) + "".join(
            _evidence_block(records[evidence_id], files) for evidence_id in section["deeper_evidence_ids"]
        )
        parts.append('<details><summary>Deeper explanation</summary><div class="body">%s</div></details>' % inner)
    parts.append("</article>")
    return "".join(parts)


def _verification_html(evidence, narrative, files):
    checks = [record for record in evidence["evidence"] if record["kind"] == "check"]
    notes = {}
    for note in narrative["test_notes"]:
        notes.setdefault(note["evidence_id"], []).append(note["explanation"])
    if not checks:
        return "<p>No check results were recorded for this change.</p>"
    parts = []
    for record in checks:
        facts = [
            ("Status", record["status"]),
            ("Applies to final revision", "yes" if record["applies_to_final"] else "no"),
            ("Why", record["applicability_evidence"]),
            ("Tested revision", record["tested_revision"] or "not recorded"),
            ("Exit code", "not recorded" if record["exit_code"] is None else record["exit_code"]),
            ("Captured at", record["captured_at"] or "not recorded"),
        ]
        parts.append('<h3 class="mono">%s</h3><dl class="meta">%s</dl>' % (
            esc(record["method"], keep=""),
            "".join("<dt>%s</dt><dd>%s</dd>" % (name, esc(value, keep="")) for name, value in facts),
        ))
        parts.extend(_prose(text) for text in notes.get(record["id"], []))
        if record["coverage_limits"]:
            parts.append("<h4>Limits of this check</h4>" + _items(record["coverage_limits"]))
        parts.append(_evidence_block(record, files))
    return "".join(parts)


def _limits_html(evidence, narrative):
    """Everything here comes from the manifest, so a narrative cannot hide it."""
    review, worktree = evidence["review"], evidence["worktree"]
    omissions = [record for record in evidence["evidence"] if record["kind"] == "omission"]
    files = {file["id"]: file for file in evidence["files"]}
    extra_findings = [item for item in narrative["unresolved_findings"] if item not in review["unresolved_findings"]]
    parts = [
        "<h3>Review</h3>", _prose(review["report_summary"]),
        "<h4>Unresolved review findings</h4>", _items(review["unresolved_findings"], "None recorded by the review."),
        "<h4>Review coverage limits</h4>", _items(review["coverage_limits"]),
        "<h3>Uncommitted changes excluded from this walkthrough</h3>",
        _items(worktree["excluded_changes"], "The working tree was clean when evidence was captured."),
        "<h3>Content not shown</h3>",
        _items(["%s: %s. %s" % (_file_label(files[item["file_id"]]), item["reason"], item["explanation"]) for item in omissions],
               "No file content was omitted."),
        "<h3>Redactions</h3>", _items(evidence["redaction_notes"], "No redactions were applied."),
        "<h3>Coverage limits</h3>", _items(evidence["coverage_limits"]),
    ]
    if extra_findings:
        parts += ["<h3>Further findings noted by the author</h3>", _items(extra_findings)]
    if narrative["tradeoffs"]:
        parts += ["<h3>Tradeoffs</h3>", _items(narrative["tradeoffs"])]
    if narrative["limitations"]:
        parts += ["<h3>Limitations noted by the author</h3>", _items(narrative["limitations"])]
    return "".join(parts)


def _coverage_html(evidence, narrative):
    coverage = {entry["file_id"]: entry for entry in narrative["file_coverage"]}
    rows = []
    for file in evidence["files"]:
        entry = coverage[file["id"]]
        links = ", ".join('<a href="#%s">%s</a>' % (esc(section_id), esc(section_id)) for section_id in entry["section_ids"])
        rows.append("<tr><td class=\"mono\">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(_file_label(file), keep=""), esc(file["status"]), esc(entry["category"]), links, esc(entry["reason"], keep=""),
        ))
    head = "<tr><th>File</th><th>Change</th><th>Coverage</th><th>Sections</th><th>Reason</th></tr>"
    return '<div class="scroll"><table class="list">%s%s</table></div>' % (head, "".join(rows))


def _notices(evidence):
    notices = []
    if evidence["synthetic"]:
        notices.append("SYNTHETIC EXAMPLE. This is not a real, reviewed change.")
    if not review_is_clean(evidence):
        notices.append(
            "NOT A CLEAN REVIEW. The review verdict is %s. This is a diagnostic walkthrough, not a final reviewed one."
            % evidence["review"]["verdict"]
        )
    missing = sum(1 for record in evidence["evidence"] if record["kind"] == "omission" and record["essential_evidence_missing"])
    if missing:
        notices.append("Incomplete: content for %d file(s) could not be shown. See Limits." % missing)
    stale = [record for record in evidence["evidence"] if record["kind"] == "check" and not record["applies_to_final"]]
    if stale:
        notices.append("%d recorded check(s) do not apply to the final revision. See Verification." % len(stale))
    if evidence["worktree"]["excluded_changes"]:
        notices.append("Uncommitted changes existed when evidence was captured and are not part of this walkthrough.")
    return "".join('<p class="notice">%s</p>' % esc(text) for text in notices)


def render_html(evidence, narrative):
    """Render a validated bundle. Call load_bundle first."""
    files = {file["id"]: file for file in evidence["files"]}
    records = {record["id"]: record for record in evidence["evidence"]}
    review = evidence["review"]
    labels = ("[SYNTHETIC] " if evidence["synthetic"] else "") + ("" if review_is_clean(evidence) else "[DIAGNOSTIC] ")
    title = labels + narrative["title"]
    open_findings = ""
    if not review_is_clean(evidence):
        open_findings = "<h2>Open review findings</h2>" + _items(
            review["unresolved_findings"], "The review listed none; see its summary under Limits."
        )
    header_facts = [
        ("Repository", evidence["repository_label"]),
        ("Base", evidence["base_sha"]),
        ("Final", evidence["final_sha"]),
        ("Base chosen because", evidence["base_selection_reason"]),
        ("Review verdict", review["verdict"]),
        ("Unresolved findings", len(review["unresolved_findings"])),
    ]
    nav = "".join('<li><a href="#%s">%s</a></li>' % (esc(s["id"]), esc(s["title"], keep="")) for s in narrative["sections"])
    body = [
        "<header><h1>%s</h1>" % esc(title, keep=""),
        _notices(evidence),
        '<dl class="meta">%s</dl>' % "".join(
            "<dt>%s</dt><dd class=\"mono\">%s</dd>" % (name, esc(value, keep="")) for name, value in header_facts
        ),
        "<p>This file is a snapshot of one reviewed range. It does not update when the repository changes.</p>",
        open_findings + "</header>",
        '<section id="overview"><h2>Overview</h2>%s</section>' % _prose(narrative["summary"]),
        '<nav aria-label="Sections"><ol>%s</ol></nav>' % nav,
        '<section id="changes"><h2>What changed</h2>%s</section>'
        % "".join(_section_html(section, records, files) for section in narrative["sections"]),
        '<section id="verification"><h2>Verification</h2>%s</section>' % _verification_html(evidence, narrative, files),
        '<section id="limits"><h2>Limits</h2>%s</section>' % _limits_html(evidence, narrative),
        '<section id="files"><h2>File coverage</h2>%s</section>' % _coverage_html(evidence, narrative),
        '<footer><p>Evidence SHA-256: <span class="mono">%s</span></p></footer>' % esc(narrative["evidence_sha256"]),
    ]
    head = (
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="%s">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        "<title>%s</title><style>%s</style>"
    ) % (esc(content_security_policy()), esc(title, keep=""), CSS)
    return '<!doctype html>\n<html lang="en"><head>%s</head><body><div class="page">%s</div></body></html>\n' % (
        head, "".join(body),
    )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _load_inputs(path):
    inputs = parse_json(read_file(path, "inputs"), "inputs")
    errors = schema_errors(inputs, load_schema(INPUTS_SCHEMA), "inputs")
    if errors:
        raise WalkthroughError(errors)
    return inputs


def cmd_extract(args):
    limits = {"file": args.max_file_bytes, "total": args.max_total_bytes}
    evidence = build_evidence(args.repo, args.base, args.final, _load_inputs(args.inputs), args.source, args.context, limits)
    data = serialize(evidence)
    Path(args.out).write_bytes(data)
    kinds = Counter(record["kind"] for record in evidence["evidence"])
    print("evidence_sha256: %s" % sha256_hex(data))
    print("files: %d, hunks: %d, omissions: %d, checks: %d, redactions: %d" % (
        len(evidence["files"]), kinds["diff"], kinds["omission"], kinds["check"], len(evidence["redaction_notes"]),
    ))
    print("worktree: %s" % evidence["worktree"]["state"])


def cmd_validate(args):
    evidence_data = read_file(args.evidence, "evidence")
    if args.narrative:
        load_bundle(evidence_data, read_file(args.narrative, "narrative"), args.allow_synthetic, args.diagnostic)
    else:
        load_evidence(evidence_data)
    print("valid; evidence_sha256: %s" % sha256_hex(evidence_data))


def cmd_render(args):
    evidence, narrative = load_bundle(
        read_file(args.evidence, "evidence"), read_file(args.narrative, "narrative"),
        args.allow_synthetic, args.diagnostic,
    )
    data = render_html(evidence, narrative).encode("utf-8")
    Path(args.out).write_bytes(data)
    print("wrote %s" % args.out)
    print("html_sha256: %s" % sha256_hex(data))


def _add_release_flags(command):
    command.add_argument("--allow-synthetic", action="store_true", help="accept the hand-written sample; demos and tests only")
    command.add_argument("--diagnostic", action="store_true",
                         help="render even when the review is not clean; the page is labeled as non-final")


def build_parser():
    parser = argparse.ArgumentParser(prog="walkthrough.py", description="Git range to offline HTML walkthrough.")
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract", help="write evidence.json for a reviewed base..final range")
    extract.add_argument("--repo", default=".", help="repository or worktree to read (default: current directory)")
    extract.add_argument("--base", required=True, help="full base commit ID")
    extract.add_argument("--final", required=True, help="full final commit ID")
    extract.add_argument("--inputs", required=True, help="JSON with the review record and check results")
    extract.add_argument("--out", required=True, help="where to write evidence.json")
    extract.add_argument("--source", action="append", default=[], metavar="PATH:SIDE:START-END",
                         help="also include an exact source range; SIDE is base or final")
    extract.add_argument("--context", type=int, default=DEFAULT_CONTEXT_LINES, help="context lines per hunk")
    extract.add_argument("--max-file-bytes", type=int, default=MAX_FILE_TEXT_BYTES)
    extract.add_argument("--max-total-bytes", type=int, default=MAX_TOTAL_TEXT_BYTES)
    extract.set_defaults(run=cmd_extract)

    validate = commands.add_parser("validate", help="check evidence, and a narrative if given")
    validate.add_argument("--evidence", required=True)
    validate.add_argument("--narrative")
    _add_release_flags(validate)
    validate.set_defaults(run=cmd_validate)

    render = commands.add_parser("render", help="validate, then write one offline HTML file")
    render.add_argument("--evidence", required=True)
    render.add_argument("--narrative", required=True)
    render.add_argument("--out", required=True)
    _add_release_flags(render)
    render.set_defaults(run=cmd_render)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except WalkthroughError as error:
        for message in error.messages:
            print("error: %s" % message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
