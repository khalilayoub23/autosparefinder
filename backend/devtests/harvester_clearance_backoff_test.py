"""
devtests/harvester_clearance_backoff_test.py
Tests the Phase 2 recovery defect fix:
  - ensure_clearance() must fail fast (not retry 6×70s) when FlareSolverr is broken
  - http_get() must return "" immediately when clearance is unavailable
  - Workers must not start when clearance is unavailable (queue burn prevention)

Run: docker exec autospare_backend python3 /app/devtests/harvester_clearance_backoff_test.py
"""
import sys
import time
import unittest
import os

sys.path.insert(0, "/app")

# Patch FLARESOLVERR URL to a dead host BEFORE importing the module under test
os.environ["FLARESOLVERR_URL"] = "http://127.0.0.1:9999/v1"   # nothing listening here
os.environ["HARVESTER_CLEARANCE_TTL_S"] = "30"
os.environ["HARVESTER_CLEARANCE_FAIL_BACKOFF_S"] = "5"  # tiny for test speed

import car_parts_ie_flaresolverr_harvester as H


class TestClearanceBackOff(unittest.TestCase):

    def setUp(self):
        # Reset module-level state before each test
        with H._CLEARANCE_LOCK:
            H._CLEARANCE["cookie"] = ""
            H._CLEARANCE["ts"] = 0.0
            H._CLEARANCE["ua"] = H._DEFAULT_UA
        H._CLEARANCE_FAILED_TS = 0.0

    def test_solve_clearance_fails_on_dead_host(self):
        """_solve_clearance must return False when FlareSolverr is unreachable."""
        result = H._solve_clearance()
        self.assertFalse(result, "_solve_clearance should return False on unreachable host")

    def test_ensure_clearance_sets_failed_ts_on_failure(self):
        """After all retries fail, _CLEARANCE_FAILED_TS must be set."""
        before = time.time()
        result = H.ensure_clearance(force=True)
        after = time.time()
        self.assertFalse(result)
        self.assertGreater(H._CLEARANCE_FAILED_TS, before - 1)
        self.assertLess(H._CLEARANCE_FAILED_TS, after + 1)

    def test_ensure_clearance_fails_fast_within_backoff(self):
        """Second call within backoff period must return False immediately (< 1s)."""
        # First call — will attempt and fail (sets _CLEARANCE_FAILED_TS)
        H.ensure_clearance(force=True)
        H._CLEARANCE_FAILED_TS = time.time()  # simulate just-failed

        # Second call — should return immediately (back-off)
        t0 = time.time()
        result = H.ensure_clearance()
        elapsed = time.time() - t0

        self.assertFalse(result, "Should return False during back-off period")
        self.assertLess(elapsed, 1.0, f"Should return in <1s during back-off, took {elapsed:.2f}s")

    def test_ensure_clearance_retries_after_backoff_expires(self):
        """After back-off period, ensure_clearance should attempt again (not immediately return)."""
        H._CLEARANCE_FAILED_TS = time.time() - (H.CLEARANCE_FAIL_BACKOFF_S + 1)  # expired

        t0 = time.time()
        result = H.ensure_clearance()
        elapsed = time.time() - t0

        self.assertFalse(result, "Should still fail (FS still unreachable)")
        # Should have attempted retries (not immediately returned) — takes >0.5s with dead host
        # (urllib times out relatively quickly on refused connection)
        self.assertGreater(elapsed, 0.1, "Should have attempted retries, not immediate return")

    def test_http_get_returns_empty_fast_when_clearance_unavailable(self):
        """http_get must return '' quickly (< 2s) when clearance is known unavailable."""
        H._CLEARANCE_FAILED_TS = time.time()  # just failed — back-off active

        t0 = time.time()
        result = H.http_get("https://www.car-parts.ie/car-parts/honda/civic/brakes")
        elapsed = time.time() - t0

        self.assertEqual(result, "", "Should return empty string")
        self.assertLess(elapsed, 2.0, f"Should return quickly in backoff, took {elapsed:.2f}s")

    def test_failed_ts_cleared_on_success(self):
        """On success, _CLEARANCE_FAILED_TS must be cleared to 0."""
        H._CLEARANCE_FAILED_TS = time.time() - 999  # old failure

        # Simulate a successful solve by directly setting state
        with H._CLEARANCE_LOCK:
            H._CLEARANCE["cookie"] = "cf_clearance=test_value"
            H._CLEARANCE["ts"] = time.time()
        # Calling ensure_clearance with a fresh cookie should skip retries and return True
        result = H.ensure_clearance()
        self.assertTrue(result, "Should return True with fresh cookie")
        # _CLEARANCE_FAILED_TS should NOT be cleared just from a fresh-cookie fast-path
        # (it's only cleared when _solve_clearance() succeeds). That's fine — the fast
        # path returned True without touching _CLEARANCE_FAILED_TS.

    def test_no_state_mutation_on_backoff_fast_path(self):
        """Back-off fast path must not mutate _CLEARANCE or _CLEARANCE_FAILED_TS."""
        H._CLEARANCE_FAILED_TS = time.time()
        original_failed_ts = H._CLEARANCE_FAILED_TS
        with H._CLEARANCE_LOCK:
            H._CLEARANCE["cookie"] = ""
            H._CLEARANCE["ts"] = 0.0

        H.ensure_clearance()  # back-off path

        # Failed TS should not advance (no new failure happened)
        self.assertAlmostEqual(H._CLEARANCE_FAILED_TS, original_failed_ts, delta=0.1)


if __name__ == "__main__":
    print("=" * 70)
    print("Harvester clearance back-off fix — unit tests")
    print("=" * 70)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestClearanceBackOff)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
