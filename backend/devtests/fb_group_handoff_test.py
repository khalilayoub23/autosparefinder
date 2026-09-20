"""
fb_group_handoff_test.py — Phase 5M group discovery handoff unit tests.

Tests the URL validation, deduplication, and normalisation logic that the
/api/v1/system/ingest-group-list endpoint applies before calling
_upsert_discovered_groups().

Does NOT make network calls or DB connections.
"""

import re
import sys
import unittest

sys.path.insert(0, "/app")

# ── Mirror the exact validation logic from routes/system.py ──────────────────

_FB_GROUP_URL_RE = re.compile(
    r"^https://(www\.)?facebook\.com/groups/[A-Za-z0-9._%-]+/?$"
)
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|[​-‏‪-‮﻿⁠-⁤]"
)


def _sanitize_for_llm(text: str, maxlen: int) -> str:
    return _CONTROL_RE.sub("", text).strip()[:maxlen]


def _validate_and_normalise(raw_groups: list) -> tuple[list, int, int]:
    """Mirrors the validation block in ingest_group_list().

    Returns (valid_groups, rejected_invalid_count, duplicates_count).
    """
    valid: list[dict] = []
    rejected_invalid = 0
    seen_urls: set[str] = set()
    duplicates = 0

    for g in raw_groups:
        if not isinstance(g, dict):
            rejected_invalid += 1
            continue
        raw_url = str(g.get("url") or "").strip().rstrip("/")
        name = _sanitize_for_llm(str(g.get("name") or ""), 255)
        if not name:
            name = "Unknown Group"
        if not _FB_GROUP_URL_RE.match(raw_url):
            rejected_invalid += 1
            continue
        norm_url = raw_url + "/"
        if norm_url in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(norm_url)
        valid.append({"name": name, "url": norm_url})

    return valid, rejected_invalid, duplicates


# ── URL Validation ────────────────────────────────────────────────────────────

class TestURLValidation(unittest.TestCase):

    def test_numeric_group_id_accepted(self):
        v, rej, dup = _validate_and_normalise([
            {"name": "Test", "url": "https://www.facebook.com/groups/123456789"},
        ])
        self.assertEqual(len(v), 1)
        self.assertEqual(rej, 0)

    def test_named_slug_accepted(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/my.group.slug-test"},
        ])
        self.assertEqual(len(v), 1)

    def test_without_www_accepted(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://facebook.com/groups/123"},
        ])
        self.assertEqual(len(v), 1)

    def test_trailing_slash_accepted(self):
        v, rej, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/123/"},
        ])
        self.assertEqual(len(v), 1)
        self.assertEqual(rej, 0)

    def test_invalid_non_group_url_rejected(self):
        _, rej, _ = _validate_and_normalise([
            {"name": "X", "url": "https://www.facebook.com/profile/123"},
        ])
        self.assertEqual(rej, 1)

    def test_invalid_non_facebook_domain_rejected(self):
        _, rej, _ = _validate_and_normalise([
            {"name": "X", "url": "https://evil.com/groups/123"},
        ])
        self.assertEqual(rej, 1)

    def test_mbasic_facebook_rejected(self):
        # mbasic.facebook.com is not in the regex — only facebook.com
        _, rej, _ = _validate_and_normalise([
            {"name": "X", "url": "https://mbasic.facebook.com/groups/123"},
        ])
        self.assertEqual(rej, 1)

    def test_http_scheme_rejected(self):
        _, rej, _ = _validate_and_normalise([
            {"name": "X", "url": "http://www.facebook.com/groups/123"},
        ])
        self.assertEqual(rej, 1)

    def test_empty_url_rejected(self):
        _, rej, _ = _validate_and_normalise([{"name": "X", "url": ""}])
        self.assertEqual(rej, 1)

    def test_none_url_rejected(self):
        _, rej, _ = _validate_and_normalise([{"name": "X", "url": None}])
        self.assertEqual(rej, 1)

    def test_non_dict_rejected(self):
        _, rej, _ = _validate_and_normalise(["not_a_dict"])
        self.assertEqual(rej, 1)

    def test_integer_entry_rejected(self):
        _, rej, _ = _validate_and_normalise([42])
        self.assertEqual(rej, 1)


# ── URL Normalisation ─────────────────────────────────────────────────────────

class TestNormalisation(unittest.TestCase):

    def test_url_always_ends_with_slash(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/no-slash"},
        ])
        self.assertTrue(v[0]["url"].endswith("/"))

    def test_trailing_slash_not_doubled(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/123/"},
        ])
        self.assertEqual(v[0]["url"], "https://www.facebook.com/groups/123/")
        self.assertFalse(v[0]["url"].endswith("//"))

    def test_batch_all_normalised(self):
        groups = [
            {"name": f"Group {i}", "url": f"https://www.facebook.com/groups/{i}"}
            for i in range(5)
        ]
        v, rej, dup = _validate_and_normalise(groups)
        self.assertEqual(len(v), 5)
        self.assertEqual(rej, 0)
        self.assertEqual(dup, 0)
        for g in v:
            self.assertTrue(g["url"].endswith("/"))


# ── Deduplication ─────────────────────────────────────────────────────────────

class TestDeduplication(unittest.TestCase):

    def test_exact_duplicate_url_detected(self):
        v, rej, dup = _validate_and_normalise([
            {"name": "A", "url": "https://www.facebook.com/groups/123"},
            {"name": "B", "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(len(v), 1)
        self.assertEqual(dup, 1)
        self.assertEqual(rej, 0)

    def test_trailing_slash_variant_is_duplicate(self):
        # /groups/123 and /groups/123/ normalise to the same URL
        v, _, dup = _validate_and_normalise([
            {"name": "A", "url": "https://www.facebook.com/groups/123"},
            {"name": "B", "url": "https://www.facebook.com/groups/123/"},
        ])
        self.assertEqual(len(v), 1)
        self.assertEqual(dup, 1)

    def test_first_occurrence_kept_on_dup(self):
        v, _, _ = _validate_and_normalise([
            {"name": "First", "url": "https://www.facebook.com/groups/abc"},
            {"name": "Second", "url": "https://www.facebook.com/groups/abc"},
        ])
        self.assertEqual(v[0]["name"], "First")

    def test_different_urls_not_deduplicated(self):
        v, rej, dup = _validate_and_normalise([
            {"name": "A", "url": "https://www.facebook.com/groups/111"},
            {"name": "B", "url": "https://www.facebook.com/groups/222"},
        ])
        self.assertEqual(len(v), 2)
        self.assertEqual(dup, 0)

    def test_many_duplicates_counted(self):
        same = {"name": "X", "url": "https://www.facebook.com/groups/dup"}
        v, _, dup = _validate_and_normalise([same] * 5)
        self.assertEqual(len(v), 1)
        self.assertEqual(dup, 4)


# ── Name Sanitisation ─────────────────────────────────────────────────────────

class TestSanitisation(unittest.TestCase):

    def test_name_truncated_to_255(self):
        v, _, _ = _validate_and_normalise([
            {"name": "x" * 300, "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(len(v[0]["name"]), 255)

    def test_empty_name_becomes_unknown_group(self):
        v, _, _ = _validate_and_normalise([
            {"name": "", "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(v[0]["name"], "Unknown Group")

    def test_none_name_becomes_unknown_group(self):
        v, _, _ = _validate_and_normalise([
            {"name": None, "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(v[0]["name"], "Unknown Group")

    def test_control_chars_stripped_from_name(self):
        v, _, _ = _validate_and_normalise([
            {"name": "test\x00group\x01name", "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(v[0]["name"], "testgroupname")

    def test_whitespace_preserved_in_name(self):
        v, _, _ = _validate_and_normalise([
            {"name": "  Car Parts IL  ", "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertEqual(v[0]["name"], "Car Parts IL")


# ── Mixed batch ───────────────────────────────────────────────────────────────

class TestMixedBatch(unittest.TestCase):

    def test_mixed_valid_invalid_duplicates(self):
        raw = [
            {"name": "Good 1", "url": "https://www.facebook.com/groups/aaa"},
            {"name": "Good 2", "url": "https://www.facebook.com/groups/bbb"},
            {"name": "Dup",    "url": "https://www.facebook.com/groups/aaa"},
            {"name": "Bad",    "url": "https://evil.com/groups/aaa"},
            "not_a_dict",
        ]
        v, rej, dup = _validate_and_normalise(raw)
        self.assertEqual(len(v), 2)   # aaa + bbb
        self.assertEqual(dup, 1)      # second aaa
        self.assertEqual(rej, 2)      # evil.com + not_a_dict

    def test_empty_list_produces_empty_valid(self):
        v, rej, dup = _validate_and_normalise([])
        self.assertEqual(v, [])
        self.assertEqual(rej, 0)
        self.assertEqual(dup, 0)

    def test_large_batch_within_limit(self):
        groups = [
            {"name": f"Group {i}", "url": f"https://www.facebook.com/groups/{i}"}
            for i in range(200)
        ]
        v, rej, dup = _validate_and_normalise(groups)
        self.assertEqual(len(v), 200)
        self.assertEqual(rej, 0)
        self.assertEqual(dup, 0)

    def test_counts_sum_to_total_submitted(self):
        raw = [
            {"name": "A", "url": "https://www.facebook.com/groups/1"},
            {"name": "B", "url": "https://www.facebook.com/groups/2"},
            {"name": "C", "url": "https://www.facebook.com/groups/1"},   # dup
            {"name": "D", "url": "https://bad.com/groups/3"},             # invalid
            "str_entry",                                                   # invalid
        ]
        v, rej, dup = _validate_and_normalise(raw)
        total = len(raw)
        self.assertEqual(len(v) + rej + dup, total)


# ── _upsert_discovered_groups argument contract ───────────────────────────────

class TestUpsertContract(unittest.TestCase):
    """Verify that valid output from _validate_and_normalise() has exactly
    the shape _upsert_discovered_groups() expects: list of {"name", "url"}."""

    def test_output_keys_match_upsert_contract(self):
        v, _, _ = _validate_and_normalise([
            {"name": "Test Group", "url": "https://www.facebook.com/groups/123"},
        ])
        self.assertIn("name", v[0])
        self.assertIn("url", v[0])
        self.assertEqual(set(v[0].keys()), {"name", "url"})

    def test_url_is_string(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/abc"},
        ])
        self.assertIsInstance(v[0]["url"], str)

    def test_name_is_string(self):
        v, _, _ = _validate_and_normalise([
            {"name": "G", "url": "https://www.facebook.com/groups/abc"},
        ])
        self.assertIsInstance(v[0]["name"], str)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestURLValidation,
        TestNormalisation,
        TestDeduplication,
        TestSanitisation,
        TestMixedBatch,
        TestUpsertContract,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print()
    print("=" * 70)
    print(f"fb_group_handoff_test.py: {passed}/{total} PASS")
    print("=" * 70)

    sys.exit(0 if failures == 0 else 1)
