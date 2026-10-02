"""Tests for the walkthrough generator.

Run from the repository root:

    python3 -B -m unittest discover -s tests -v
"""

import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

WALKTHROUGH_DIR = Path(__file__).resolve().parent.parent / "skills" / "mestre-harness" / "walkthrough"
_spec = importlib.util.spec_from_file_location("walkthrough", WALKTHROUGH_DIR / "walkthrough.py")
wt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wt)

SAMPLE_EVIDENCE = json.loads((WALKTHROUGH_DIR / "sample-evidence.json").read_text())
SAMPLE_NARRATIVE = json.loads((WALKTHROUGH_DIR / "sample-narrative.json").read_text())

# Git must not read the developer's own configuration during tests.
ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def rehash(record):
    record["text_sha256"] = wt.text_sha256(record["text"])


def bundle(change_evidence=None, change_narrative=None, rebind=True):
    """Sample bundle as bytes, after optional edits. rebind keeps the digest valid."""
    evidence, narrative = copy.deepcopy(SAMPLE_EVIDENCE), copy.deepcopy(SAMPLE_NARRATIVE)
    if change_evidence:
        change_evidence(evidence)
    evidence_data = wt.serialize(evidence)
    if rebind:
        narrative["evidence_sha256"] = wt.sha256_hex(evidence_data)
    if change_narrative:
        change_narrative(narrative)
    return evidence_data, wt.serialize(narrative)


def record(evidence, evidence_id):
    return next(item for item in evidence["evidence"] if item["id"] == evidence_id)


class SchemaSubsetTests(unittest.TestCase):
    def test_samples_satisfy_their_schemas(self):
        self.assertEqual(wt.schema_errors(SAMPLE_EVIDENCE, wt.load_schema(wt.EVIDENCE_SCHEMA)), [])
        self.assertEqual(wt.schema_errors(SAMPLE_NARRATIVE, wt.load_schema(wt.NARRATIVE_SCHEMA)), [])

    def test_unknown_keyword_fails_closed(self):
        with self.assertRaises(wt.WalkthroughError):
            wt.schema_errors("x", {"type": "string", "maxLength": 3})

    def test_unexpected_field_is_reported(self):
        evidence = copy.deepcopy(SAMPLE_EVIDENCE)
        evidence["html"] = "<b>"
        errors = wt.schema_errors(evidence, wt.load_schema(wt.EVIDENCE_SCHEMA), "evidence")
        self.assertIn("evidence: unexpected field html", errors)

    def test_pattern_does_not_accept_a_trailing_newline(self):
        schema = {"type": "string", "pattern": "^[0-9a-f]{4}$"}
        self.assertEqual(wt.schema_errors("abcd", schema), [])
        self.assertTrue(wt.schema_errors("abcd\n", schema))

    def test_one_of_reports_the_branch_named_by_kind(self):
        evidence = copy.deepcopy(SAMPLE_EVIDENCE)
        del record(evidence, "E03")["hunk_id"]
        errors = wt.schema_errors(evidence, wt.load_schema(wt.EVIDENCE_SCHEMA), "evidence")
        self.assertEqual(errors, ["evidence.evidence[2]: missing required field hunk_id"])

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(wt.WalkthroughError):
            wt.parse_json(b'{"a": 1, "a": 2}', "doc")


class BundleValidationTests(unittest.TestCase):
    def assert_rejected(self, fragment, change_evidence=None, change_narrative=None, rebind=True):
        with self.assertRaises(wt.WalkthroughError) as caught:
            wt.load_bundle(*bundle(change_evidence, change_narrative, rebind), allow_synthetic=True)
        self.assertTrue(
            any(fragment in message for message in caught.exception.messages),
            "expected %r in %r" % (fragment, caught.exception.messages),
        )

    def test_sample_is_valid_only_as_an_explicit_demo(self):
        wt.load_bundle(*bundle(), allow_synthetic=True)
        with self.assertRaises(wt.WalkthroughError) as caught:
            wt.load_bundle(*bundle(), allow_synthetic=False)
        self.assertIn("synthetic", caught.exception.messages[0])

    def test_stale_evidence_digest(self):
        self.assert_rejected("evidence_sha256 does not match",
                             lambda e: e["coverage_limits"].append("edited later"), rebind=False)

    def test_tampered_text(self):
        def tamper(evidence):
            record(evidence, "E02")["text"] += "import os\n"
        self.assert_rejected("E02: text does not match its recorded hash", tamper)

    def test_mismatched_revisions(self):
        self.assert_rejected("narrative: final_sha differs", change_narrative=lambda n: n.update(final_sha="c" * 40))
        self.assert_rejected("review: reviewed revision differs",
                             lambda e: e["review"].update(reviewed_sha="c" * 40))
        self.assert_rejected("review: reviewed base differs",
                             lambda e: e["review"].update(reviewed_base_sha="c" * 40))

    def test_object_format_must_fit_the_ids(self):
        self.assert_rejected("does not fit object format", lambda e: e.update(object_format="sha256"))

    def test_review_that_requests_changes_blocks_the_walkthrough(self):
        for verdict in ("changes-requested", "incomplete-evidence"):
            self.assert_rejected("review verdict is " + verdict, lambda e, v=verdict: e["review"].update(verdict=v))

    def test_file_coverage_must_be_complete_and_single(self):
        self.assert_rejected("F01 must be covered exactly once, found 0",
                             change_narrative=lambda n: n["file_coverage"].clear())
        self.assert_rejected("F01 must be covered exactly once, found 2",
                             change_narrative=lambda n: n["file_coverage"].append(dict(n["file_coverage"][0])))
        self.assert_rejected("marked explained but names no section",
                             change_narrative=lambda n: n["file_coverage"][0].update(section_ids=[]))

    def test_references_must_exist(self):
        self.assert_rejected("S01: refers to unknown evidence E99",
                             change_narrative=lambda n: n["sections"][0]["evidence_ids"].append("E99"))
        self.assert_rejected("refers to unknown section S09",
                             change_narrative=lambda n: n["file_coverage"][0]["section_ids"].append("S09"))
        self.assert_rejected("E01 is not a check",
                             change_narrative=lambda n: n["test_notes"][0].update(evidence_id="E01"))

    def test_check_status_cannot_be_upgraded(self):
        self.assert_rejected("applies_to_final needs tested_revision equal to final_sha",
                             lambda e: record(e, "E04").update(applies_to_final=True))
        self.assert_rejected("a not-run check cannot have a tested revision or exit code",
                             lambda e: record(e, "E04").update(exit_code=0))
        self.assert_rejected("a passed check cannot have a non-zero exit code",
                             lambda e: record(e, "E04").update(status="passed", exit_code=1))

    def test_hunk_must_match_its_ranges(self):
        self.assert_rejected("E03: ranges differ from the hunk header",
                             lambda e: record(e, "E03")["old_range"].update(count=5))

        def drop_line(evidence):
            item = record(evidence, "E03")
            item["text"] = item["text"].replace(" def greet(name):\n", "")
            rehash(item)
        self.assert_rejected("E03: hunk body does not match", drop_line)

    def test_source_identity_and_range(self):
        self.assert_rejected("E01: text does not have the 5 line(s)", lambda e: record(e, "E01")["lines"].update(end=5))
        self.assert_rejected("E01: blob is not the base blob", lambda e: record(e, "E01").update(blob_id="d" * 40))
        self.assert_rejected("E02: revision is not the final commit", lambda e: record(e, "E02").update(revision="a" * 40))

    def test_file_shape_follows_status(self):
        self.assert_rejected("F01: status added cannot have old path", lambda e: e["files"][0].update(status="added"))
        self.assert_rejected("F01: status modified needs new path", lambda e: e["files"][0].update(new_path=None))

    def test_ids_and_links_must_be_consistent(self):
        self.assert_rejected("evidence: duplicate id E01", lambda e: record(e, "E02").update(id="E01"))
        self.assert_rejected("F01: lists unknown evidence E77", lambda e: e["files"][0]["evidence_ids"].append("E77"))
        self.assert_rejected("E01: is not listed by its file F01", lambda e: e["files"][0]["evidence_ids"].remove("E01"))

    def test_worktree_state_must_agree_with_its_list(self):
        self.assert_rejected("worktree: state and excluded_changes disagree",
                             lambda e: e["worktree"]["excluded_changes"].append("?? scratch.txt"))


class RenderTests(unittest.TestCase):
    def render(self, change_evidence=None, change_narrative=None):
        evidence, narrative = wt.load_bundle(*bundle(change_evidence, change_narrative), allow_synthetic=True)
        return wt.render_html(evidence, narrative)

    def test_page_is_self_contained(self):
        page = self.render()
        self.assertIn("Synthetic example: trim spaces around a greeting name", page)
        self.assertIn("SYNTHETIC EXAMPLE", page)
        for forbidden in ("<script", "<link", "<img", "<iframe", "<form", "<object", "http://", "https://",
                          " style=", " onclick", "javascript:", "@import", "url("):
            self.assertNotIn(forbidden, page)
        self.assertEqual(set(re.findall(r'href="([^"]*)"', page)), {"#S01"})

    def test_style_matches_the_content_security_policy(self):
        page = self.render()
        style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]*)"', page).group(1)
        self.assertIn("default-src &#x27;none&#x27;", policy)
        self.assertIn("style-src &#x27;sha256-%s&#x27;" % digest, policy)

    def test_status_and_limits_come_from_the_evidence(self):
        def silence(narrative):
            narrative["test_notes"].clear()
            narrative["unresolved_findings"].clear()
            narrative["limitations"].clear()
        page = self.render(change_narrative=silence)
        self.assertIn("not-run", page)
        self.assertIn("does NOT apply to the final revision", page)
        self.assertIn("No real tests or caller compatibility were assessed.", page)
        self.assertIn("All source, commit IDs, blob IDs, and review details are hand-authored examples.", page)

    def test_untrusted_text_is_displayed_inertly(self):
        payload = '</pre><script>alert(1)</script><img src=x onerror=alert(2)>&"\' \x1b[31mred\u202e'

        def poison_evidence(evidence):
            evidence["files"][0].update(old_path='"><svg onload=1>.py', new_path='"><svg onload=1>.py')
            evidence["repository_label"] = payload
            evidence["review"]["unresolved_findings"] = [payload]
            source = record(evidence, "E02")
            source["text"] = "def greet(name):\n    return '%s'\n" % payload
            rehash(source)
            check = record(evidence, "E04")
            check["method"], check["text"] = payload, payload + "\n"
            rehash(check)

        def poison_narrative(narrative):
            narrative["title"] = "</title>" + payload
            narrative["summary"] = payload
            narrative["sections"][0].update(title=payload, before=payload, deeper_explanation=payload)
            narrative["file_coverage"][0]["reason"] = payload
            narrative["tradeoffs"] = [payload]

        page = self.render(poison_evidence, poison_narrative)
        for forbidden in ("<script", "<img", "<svg", "\x1b", "\u202e", "</pre>"):
            self.assertNotIn(forbidden, page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("\\x1b[31mred\\u202e", page)
        self.assertEqual(page.count("<title>"), 1)

    def test_unclean_review_renders_only_as_a_labeled_diagnostic(self):
        def request_changes(evidence):
            evidence["review"].update(verdict="changes-requested", unresolved_findings=["R-01 off-by-one in greet"])
        data = bundle(request_changes)
        with self.assertRaises(wt.WalkthroughError):
            wt.load_bundle(*data, allow_synthetic=True)
        page = wt.render_html(*wt.load_bundle(*data, allow_synthetic=True, diagnostic=True))
        self.assertIn("<title>[SYNTHETIC] [DIAGNOSTIC] ", page)
        self.assertIn("NOT A CLEAN REVIEW. The review verdict is changes-requested.", page)
        self.assertLess(page.index("Open review findings"), page.index('id="overview"'))
        self.assertIn("R-01 off-by-one in greet", page)
        self.assertNotIn("DIAGNOSTIC", self.render())

    def test_incomplete_evidence_is_announced(self):
        def add_omission(evidence):
            evidence["files"][0]["evidence_ids"].append("E05")
            evidence["evidence"].append({
                "id": "E05", "kind": "omission", "file_id": "F01", "reason": "oversized",
                "explanation": "Too large.", "essential_evidence_missing": True,
            })
        page = self.render(add_omission)
        self.assertIn("Incomplete: content for 1 file(s) could not be shown.", page)


class RedactionTests(unittest.TestCase):
    def assert_masked(self, line, secret):
        sanitized, found = wt.redact_text(line + "\n")
        self.assertNotIn(secret, sanitized)
        self.assertIn(wt.REDACTION_MARK, sanitized)
        self.assertTrue(found)

    def test_known_secret_shapes(self):
        self.assert_masked("aws = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE")
        self.assert_masked("t = ghp_" + "a1B2" * 9, "ghp_" + "a1B2" * 9)
        self.assert_masked('password = "correct-horse-battery"', "correct-horse-battery")
        self.assert_masked("\"apiKey\": 'k9k9k9k9k9k9'", "k9k9k9k9k9k9")
        self.assert_masked("DB_PASSWORD=hunter2hunter2", "hunter2hunter2")
        self.assert_masked("url = https://user:s3cr3tpw@example.invalid/db", "s3cr3tpw")
        self.assert_masked("key = sk-" + "x" * 30, "sk-" + "x" * 30)

    def test_ordinary_code_is_left_alone(self):
        text = "def greet(name):\n    token = read_token()\n    return name.strip()\n"
        self.assertEqual(wt.redact_text(text), (text, {}))

    def test_private_key_block_is_masked_line_by_line(self):
        text = "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc/def+ghi\n-----END RSA PRIVATE KEY-----\nb\n"
        sanitized, found = wt.redact_text(text)
        self.assertEqual(sanitized, "a\n" + (wt.REDACTION_MARK + "\n") * 4 + "b\n")
        self.assertEqual(found["private-key"], 4)

    def test_hunk_structure_survives(self):
        hunk = ('@@ -1,2 +1,2 @@ password = "contextsecret99"\n context\n'
                '-token = "oldoldoldold"\n+token = "newnewnewnew"\r\n\\ No newline at end of file\n')
        sanitized, _ = wt.redact_text(hunk, is_hunk=True)
        for secret in ("contextsecret99", "oldoldoldold", "newnewnewnew"):
            self.assertNotIn(secret, sanitized)
        self.assertEqual(wt.parse_hunk(sanitized)[:2], wt.parse_hunk(hunk)[:2])
        self.assertTrue(sanitized.split("\n")[3].endswith("\r"))

    def test_sensitive_paths(self):
        for path in (".env", "config/.env.production", "deploy/id_rsa", "certs/server.pem", "a/terraform.tfvars"):
            self.assertTrue(wt.is_sensitive_path(path), path)
        for path in ("src/environment.py", "docs/keys.md", "env.example.md"):
            self.assertFalse(wt.is_sensitive_path(path), path)


class Repo:
    """A throwaway Git repository."""

    def __init__(self, root, object_format=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        init = ["init", "-q", "-b", "main"]
        if object_format:
            init.append("--object-format=" + object_format)
        self.git(*init)

    def git(self, *args):
        result = subprocess.run(["git"] + list(args), cwd=str(self.root), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=True)
        return result.stdout.decode().strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


def inputs_for(base, final, checks=(), verdict=wt.ACCEPTED_VERDICT):
    return {
        "base_selection_reason": "HEAD when the task started.",
        "review": {
            "reviewed_base_sha": base, "reviewed_sha": final, "verdict": verdict,
            "report_summary": "Reviewed the full range.", "unresolved_findings": [], "coverage_limits": [],
        },
        "checks": list(checks),
        "coverage_limits": [],
    }


def check_result(status="passed", revision=None, exit_code=0, output="ok\n"):
    return {"method": "python -m pytest", "status": status, "tested_revision": revision, "exit_code": exit_code,
            "captured_at": "2026-10-02T10:00:00Z", "output": output, "coverage_limits": []}


def narrative_for(evidence, evidence_data):
    """A minimal honest narrative: one section per file, citing all its evidence."""
    sections, coverage = [], []
    for number, file in enumerate(evidence["files"], 1):
        section_id = "S%02d" % number
        sections.append({
            "id": section_id, "title": "Change %d" % number, "before": "", "after": "", "why": "Test narrative.",
            "evidence_ids": file["evidence_ids"], "deeper_explanation": "", "deeper_evidence_ids": [],
        })
        coverage.append({"file_id": file["id"], "category": "explained", "section_ids": [section_id], "reason": "Shown."})
    return {
        "schema_version": "1.0", "synthetic": False, "evidence_sha256": wt.sha256_hex(evidence_data),
        "base_sha": evidence["base_sha"], "final_sha": evidence["final_sha"], "title": "Test change",
        "summary": "A change used by the tests.", "sections": sections, "file_coverage": coverage,
        "test_notes": [], "tradeoffs": [], "limitations": [], "unresolved_findings": [],
    }


GITHUB_TOKEN = "ghp_" + "Zz9" * 12
ODD_NAME = '-rf "quoted" <b>\n.txt'


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, ISOLATED_GIT_ENV)
        cls.env.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = repo = Repo(Path(cls.tmp.name) / "repo")
        repo.write("app.py", "def greet(name):\n    return 'Hello, ' + name\n")
        repo.write("old_name.txt", "".join("line %d\n" % n for n in range(1, 21)))
        repo.write("gone.txt", "soon deleted\n")
        repo.write("script.sh", "#!/bin/sh\necho hi\n")
        repo.write("crlf.txt", "one\r\ntwo\r\n")
        repo.write("logo.bin", b"\x89PNG\x00\x01\x02")
        repo.write("legacy.txt", b"caf\xe9\n")
        cls.base = repo.commit("base")

        repo.write("app.py", "def greet(name):\n    return 'Hello, ' + name.strip()\n")
        repo.write("notes.md", "first\nlast without newline")
        repo.git("mv", "old_name.txt", "new_name.txt")
        repo.write("new_name.txt", "".join("line %d\n" % n for n in range(1, 20)) + "line twenty\n")
        (repo.root / "gone.txt").unlink()
        script = repo.root / "script.sh"
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        repo.write("crlf.txt", "one\r\nTWO\r\n")
        repo.write("logo.bin", b"\x89PNG\x00\x09\x09")
        repo.write("legacy.txt", b"caf\xe9 au lait\n")
        os.symlink("app.py", str(repo.root / "link"))
        repo.write(".env", "API_SECRET=do-not-show-this-value\n")
        repo.write("settings.py", 'GITHUB = "%s"\npassword = "correct-horse-battery"\nDEBUG = True\n' % GITHUB_TOKEN)
        repo.write(ODD_NAME, "odd\n")
        repo.write("big.txt", "x" * 70 + "\n" + ("filler line\n" * 20000))
        cls.final = repo.commit("final")

        cls.inputs = inputs_for(cls.base, cls.final, [
            check_result(revision=cls.final),
            check_result(revision=cls.base),
            check_result(status="not-run", revision=None, exit_code=None, output="token=" + GITHUB_TOKEN + "\n"),
        ])
        cls.evidence = wt.build_evidence(repo.root, cls.base, cls.final, cls.inputs)
        cls.data = wt.serialize(cls.evidence)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.env.stop()

    def file(self, path):
        return next(item for item in self.evidence["files"] if path in (item["new_path"], item["old_path"]))

    def records(self, path, kind=None):
        ids = self.file(path)["evidence_ids"]
        return [item for item in self.evidence["evidence"] if item["id"] in ids and kind in (None, item["kind"])]

    def omission(self, path):
        (only,) = self.records(path)
        self.assertEqual(only["kind"], "omission")
        return only

    def test_manifest_is_valid_and_complete(self):
        wt.load_evidence(self.data)
        self.assertEqual(len(self.evidence["files"]), 13)
        self.assertFalse(self.evidence["synthetic"])
        self.assertEqual(self.evidence["repository_label"], "repo")
        self.assertNotIn(str(self.repo.root), self.data.decode())

    def test_statuses_paths_and_modes(self):
        expected = {
            "app.py": "modified", "notes.md": "added", "gone.txt": "deleted", "new_name.txt": "renamed",
            "script.sh": "modified", "link": "added", ODD_NAME: "added",
        }
        for path, status in expected.items():
            self.assertEqual(self.file(path)["status"], status, path)
        renamed = self.file("new_name.txt")
        self.assertEqual((renamed["old_path"], renamed["new_path"]), ("old_name.txt", "new_name.txt"))
        deleted = self.file("gone.txt")
        self.assertEqual((deleted["new_path"], deleted["new_blob_id"], deleted["new_mode"]), (None, None, None))
        script = self.file("script.sh")
        self.assertEqual((script["old_mode"], script["new_mode"]), ("100644", "100755"))

    def test_hunks_are_the_real_diff(self):
        (hunk,) = self.records("app.py", "diff")
        self.assertEqual(hunk["text"], "@@ -1,2 +1,2 @@\n def greet(name):\n"
                                       "-    return 'Hello, ' + name\n+    return 'Hello, ' + name.strip()\n")
        (added,) = self.records("notes.md", "diff")
        self.assertEqual(added["text"], "@@ -0,0 +1,2 @@\n+first\n+last without newline\n\\ No newline at end of file\n")
        (deleted,) = self.records("gone.txt", "diff")
        self.assertEqual(deleted["text"], "@@ -1 +0,0 @@\n-soon deleted\n")
        (renamed,) = self.records("new_name.txt", "diff")
        self.assertIn("-line 20\n+line twenty\n", renamed["text"])
        (crlf,) = self.records("crlf.txt", "diff")
        self.assertIn("-two\r\n+TWO\r\n", crlf["text"])

    def test_content_that_cannot_be_shown_is_an_honest_omission(self):
        expected = {
            "logo.bin": ("binary", False), "legacy.txt": ("unsupported-encoding", True), "link": ("symlink", False),
            ".env": ("secret-redaction", True), "script.sh": ("other", False), "big.txt": ("oversized", True),
        }
        for path, (reason, essential) in expected.items():
            omission = self.omission(path)
            self.assertEqual((omission["reason"], omission["essential_evidence_missing"]), (reason, essential), path)

    def test_secrets_never_reach_the_manifest(self):
        text = self.data.decode()
        for secret in (GITHUB_TOKEN, "correct-horse-battery", "do-not-show-this-value"):
            self.assertNotIn(secret, text)
        (hunk,) = self.records("settings.py", "diff")
        self.assertEqual(hunk["text"].count("\n"), 4)
        self.assertIn("+DEBUG = True\n", hunk["text"])
        self.assertTrue(any("github-token" in note for note in self.evidence["redaction_notes"]))
        self.assertTrue(any("check output" in note for note in self.evidence["redaction_notes"]))

    def test_check_applicability_is_derived_not_trusted(self):
        checks = [item for item in self.evidence["evidence"] if item["kind"] == "check"]
        self.assertEqual([item["applies_to_final"] for item in checks], [True, False, False])
        self.assertEqual([item["status"] for item in checks], ["passed", "passed", "not-run"])
        self.assertIn("not final_sha", checks[1]["applicability_evidence"])

    def test_inconsistent_check_input_is_refused(self):
        bad = inputs_for(self.base, self.final, [check_result(status="passed", revision=self.final, exit_code=2)])
        with self.assertRaises(wt.WalkthroughError) as caught:
            wt.build_evidence(self.repo.root, self.base, self.final, bad)
        self.assertIn("inputs: check 1 (python -m pytest): a passed check cannot have", caught.exception.messages[0])

    def test_odd_file_names_stay_data(self):
        (hunk,) = self.records(ODD_NAME, "diff")
        self.assertEqual(hunk["text"], "@@ -0,0 +1 @@\n+odd\n")

    def test_range_must_be_exact_and_reviewed(self):
        cases = [
            ((self.base[:12], self.final), "--base must be a full"),
            ((self.final, self.base), "base is not an ancestor of final"),
            ((self.repo.git("rev-parse", self.final + "^{tree}"), self.final), "--base is not a commit"),
        ]
        for (base, final), fragment in cases:
            with self.assertRaises(wt.WalkthroughError) as caught:
                wt.build_evidence(self.repo.root, base, final, inputs_for(base, final))
            self.assertIn(fragment, caught.exception.messages[0])
        with self.assertRaises(wt.WalkthroughError) as caught:
            wt.build_evidence(self.repo.root, self.base, self.final, inputs_for(self.base, self.base))
        self.assertIn("review record covers a different", caught.exception.messages[0])

    def test_requested_source_range_is_exact(self):
        evidence = wt.build_evidence(self.repo.root, self.base, self.final, self.inputs,
                                     sources=["notes.md:final:2-2", "app.py:base:1-2"])
        sources = [item for item in evidence["evidence"] if item["kind"] == "source"]
        self.assertEqual(sources[0]["text"], "last without newline")
        self.assertEqual(sources[1]["text"], "def greet(name):\n    return 'Hello, ' + name\n")
        self.assertEqual(sources[1]["revision"], self.base)
        for request in ("notes.md:final:2-9", "missing.py:final:1-1", "logo.bin:final:1-1", "app.py:middle:1-1"):
            with self.assertRaises(wt.WalkthroughError):
                wt.build_evidence(self.repo.root, self.base, self.final, self.inputs, sources=[request])

    def test_end_to_end_render(self):
        narrative = wt.serialize(narrative_for(self.evidence, self.data))
        page = wt.render_html(*wt.load_bundle(self.data, narrative, allow_synthetic=False))
        self.assertIn("-rf &quot;quoted&quot; &lt;b&gt;\\x0a.txt", page)
        self.assertIn("new_name.txt (was old_name.txt)", page)
        self.assertIn("Incomplete: content for 3 file(s) could not be shown.", page)
        self.assertIn("2 recorded check(s) do not apply to the final revision.", page)
        self.assertNotIn(GITHUB_TOKEN, page)
        self.assertNotIn("<b>", page)

    def test_command_line_round_trip(self):
        with tempfile.TemporaryDirectory() as out:
            paths = {name: str(Path(out) / name) for name in ("inputs.json", "evidence.json", "narrative.json", "page.html")}
            Path(paths["inputs.json"]).write_bytes(wt.serialize(self.inputs))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = wt.main(["extract", "--repo", str(self.repo.root), "--base", self.base, "--final", self.final,
                                "--inputs", paths["inputs.json"], "--out", paths["evidence.json"]])
            self.assertEqual(code, 0)
            data = Path(paths["evidence.json"]).read_bytes()
            self.assertIn("evidence_sha256: " + wt.sha256_hex(data), stdout.getvalue())
            Path(paths["narrative.json"]).write_bytes(wt.serialize(narrative_for(json.loads(data), data)))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wt.main(["render", "--evidence", paths["evidence.json"], "--narrative",
                                          paths["narrative.json"], "--out", paths["page.html"]]), 0)
            self.assertTrue(Path(paths["page.html"]).read_text().startswith("<!doctype html>"))

            Path(paths["evidence.json"]).write_bytes(data.replace(b"name.strip()", b"name.title()"))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = wt.main(["render", "--evidence", paths["evidence.json"], "--narrative", paths["narrative.json"],
                                "--out", paths["page.html"]])
            self.assertEqual(code, 1)
            self.assertIn("error: ", stderr.getvalue())


class ExtractionIsolationTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, ISOLATED_GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def two_commits(self, repo):
        repo.write("app.py", "value = 1\n")
        base = repo.commit("base")
        repo.write("app.py", "value = 2\n")
        return base, repo.commit("final")

    def test_repository_configuration_cannot_run_programs(self):
        repo = Repo(self.tmp / "repo")
        marker = self.tmp / "executed"
        hook = repo.write("../hook.sh", "#!/bin/sh\ntouch '%s'\nexit 0\n" % marker)
        hook.chmod(0o755)
        repo.write(".gitattributes", "*.py diff=evil\n")
        repo.write("app.py", "value = 1\n")
        base = repo.commit("base")
        repo.write("app.py", "value = 2\n")
        repo.write("added.py", "fresh = True\n")
        final = repo.commit("final")
        for key in ("diff.external", "diff.evil.textconv", "diff.evil.command", "core.fsmonitor"):
            repo.git("config", key, str(hook))

        # Control: plain Git does run the configured programs in this repository.
        subprocess.run(["git", "diff", base, final], cwd=str(repo.root), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        self.assertTrue(marker.exists())
        marker.unlink()

        evidence = wt.build_evidence(repo.root, base, final, inputs_for(base, final))
        self.assertFalse(marker.exists())
        hunks = sorted(item["text"] for item in evidence["evidence"] if item["kind"] == "diff")
        self.assertEqual(hunks, ["@@ -0,0 +1 @@\n+fresh = True\n", "@@ -1 +1 @@\n-value = 1\n+value = 2\n"])

    def test_uncommitted_work_is_excluded_and_disclosed(self):
        repo = Repo(self.tmp / "repo")
        base, final = self.two_commits(repo)
        repo.write("app.py", "value = 999\n")
        repo.write("scratch.txt", "untracked\n")
        evidence = wt.build_evidence(repo.root, base, final, inputs_for(base, final))
        self.assertEqual(evidence["worktree"], {"state": "excluded-changes", "excluded_changes": [".M app.py", "?? scratch.txt"]})
        self.assertNotIn("999", wt.serialize(evidence).decode())

    def test_merge_commits_inside_the_range(self):
        repo = Repo(self.tmp / "repo")
        base, _ = self.two_commits(repo)
        repo.git("switch", "-q", "-c", "side", base)
        repo.write("side.txt", "from the side branch\n")
        repo.commit("side")
        repo.git("switch", "-q", "main")
        repo.git("merge", "-q", "--no-ff", "-m", "merge", "side")
        final = repo.git("rev-parse", "HEAD")
        evidence = wt.build_evidence(repo.root, base, final, inputs_for(base, final))
        self.assertEqual(sorted(file["new_path"] for file in evidence["files"]), ["app.py", "side.txt"])

    def test_sha256_repositories(self):
        repo = Repo(self.tmp / "repo", object_format="sha256")
        base, final = self.two_commits(repo)
        self.assertEqual(len(final), 64)
        evidence = wt.build_evidence(repo.root, base, final, inputs_for(base, final))
        self.assertEqual(evidence["object_format"], "sha256")
        wt.load_evidence(wt.serialize(evidence))

    def test_empty_range_and_non_repository(self):
        repo = Repo(self.tmp / "repo")
        base, _ = self.two_commits(repo)
        evidence = wt.build_evidence(repo.root, base, base, inputs_for(base, base))
        self.assertEqual((evidence["files"], evidence["evidence"]), ([], []))
        with self.assertRaises(wt.WalkthroughError):
            wt.build_evidence(self.tmp, base, base, inputs_for(base, base))

    def test_long_check_output_is_cut_openly(self):
        repo = Repo(self.tmp / "repo")
        base, final = self.two_commits(repo)
        output = "".join("line %06d\n" % n for n in range(5000))
        evidence = wt.build_evidence(repo.root, base, final, inputs_for(base, final, [check_result(revision=final, output=output)]))
        check = next(item for item in evidence["evidence"] if item["kind"] == "check")
        self.assertTrue(check["text"].startswith("[extractor: the first "))
        self.assertTrue(check["text"].endswith("line 004999\n"))
        self.assertLessEqual(len(check["text"]), wt.MAX_CHECK_OUTPUT_BYTES + 100)
        self.assertIn("Output truncated by the extractor", check["coverage_limits"][0])


if __name__ == "__main__":
    unittest.main()
