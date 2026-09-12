import random
import unittest

from anti_detection import (
    BackoffState,
    BrowserProfile,
    DEFAULT_PROFILES,
    jittered_interval,
    pick_profile,
)


class TestJitteredInterval(unittest.TestCase):
    def test_within_expected_bounds(self):
        rng = random.Random(42)
        value = jittered_interval(600, 0.25, rng=rng)
        self.assertGreaterEqual(value, 450)
        self.assertLessEqual(value, 750)

    def test_zero_jitter_returns_base(self):
        rng = random.Random(1)
        value = jittered_interval(600, 0.0, rng=rng)
        self.assertEqual(value, 600)

    def test_rejects_non_positive_base(self):
        with self.assertRaises(ValueError):
            jittered_interval(0)

    def test_rejects_invalid_jitter_ratio(self):
        with self.assertRaises(ValueError):
            jittered_interval(600, 1.0)
        with self.assertRaises(ValueError):
            jittered_interval(600, -0.1)

    def test_deterministic_with_seeded_rng(self):
        rng1 = random.Random(7)
        rng2 = random.Random(7)
        self.assertEqual(
            jittered_interval(600, 0.25, rng=rng1),
            jittered_interval(600, 0.25, rng=rng2),
        )


class TestPickProfile(unittest.TestCase):
    def test_returns_one_of_default_profiles(self):
        rng = random.Random(3)
        profile = pick_profile(rng=rng)
        self.assertIn(profile, DEFAULT_PROFILES)

    def test_headers_include_user_agent(self):
        profile = BrowserProfile(impersonate="chrome124", user_agent="UA-TEST")
        headers = profile.headers()
        self.assertEqual(headers["User-Agent"], "UA-TEST")
        self.assertNotIn("sec-ch-ua", headers)

    def test_headers_include_sec_ch_ua_when_present(self):
        profile = BrowserProfile(
            impersonate="chrome124",
            user_agent="UA-TEST",
            sec_ch_ua='"Chromium";v="124"',
            sec_ch_ua_platform='"Windows"',
        )
        headers = profile.headers()
        self.assertEqual(headers["sec-ch-ua"], '"Chromium";v="124"')
        self.assertEqual(headers["sec-ch-ua-platform"], '"Windows"')

    def test_rejects_empty_profile_list(self):
        with self.assertRaises(ValueError):
            pick_profile(profiles=())

    def test_all_default_profiles_have_nonempty_user_agent(self):
        for profile in DEFAULT_PROFILES:
            self.assertTrue(profile.user_agent)
            self.assertTrue(profile.impersonate)


class TestBackoffState(unittest.TestCase):
    def test_no_delay_before_any_failure(self):
        state = BackoffState()
        self.assertEqual(state.current_delay(), 0.0)

    def test_delay_grows_exponentially(self):
        state = BackoffState(base_seconds=60, cap_seconds=1800, jitter_seconds=0)
        d1 = state.record_failure()
        d2 = state.record_failure()
        d3 = state.record_failure()
        self.assertAlmostEqual(d1, 60, delta=0.01)
        self.assertAlmostEqual(d2, 120, delta=0.01)
        self.assertAlmostEqual(d3, 240, delta=0.01)

    def test_delay_is_capped(self):
        state = BackoffState(base_seconds=60, cap_seconds=300, jitter_seconds=0)
        delay = 0
        for _ in range(10):
            delay = state.record_failure()
        self.assertLessEqual(delay, 300)

    def test_success_resets_streak(self):
        state = BackoffState(base_seconds=60, jitter_seconds=0)
        state.record_failure()
        state.record_failure()
        self.assertEqual(state.consecutive_failures, 2)
        state.record_success()
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.current_delay(), 0.0)

    def test_jitter_adds_nonnegative_extra_delay(self):
        rng = random.Random(5)
        state = BackoffState(base_seconds=60, jitter_seconds=30)
        state.record_failure(rng=rng)
        delay = state.current_delay(rng=rng)
        self.assertGreaterEqual(delay, 60)
        self.assertLessEqual(delay, 90 + 1e-9)

    def test_independent_instances_do_not_share_state(self):
        state_a = BackoffState()
        state_b = BackoffState()
        state_a.record_failure()
        self.assertEqual(state_a.consecutive_failures, 1)
        self.assertEqual(state_b.consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
