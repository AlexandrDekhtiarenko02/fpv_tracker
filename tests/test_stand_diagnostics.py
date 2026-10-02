"""Logging freshness, clock provenance and completion; no FC output."""
import collections
import contextlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import offline

t = offline.load_tracker()


class StandDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.old_time = t.time
        self.old_cam = t._cam_shadow_dbg
        self.old_diag = t._stand_diag.copy()
        self.old_wall = t._stand_cb_wall_t0
        self.old_mono = t._stand_cb_mono_t0
        self.clock = 100.0
        t.time = types.SimpleNamespace(monotonic=lambda: self.clock,
                                       perf_counter=lambda: self.clock + 200.0)
        t._cam_shadow_dbg = {}
        t._stand_reset_metrics()
        t._stand_cb_wall_t0 = None
        t._stand_cb_mono_t0 = None

    def tearDown(self):
        t.time = self.old_time
        t._cam_shadow_dbg = self.old_cam
        t._stand_diag.clear()
        t._stand_diag.update(self.old_diag)
        t._stand_cb_wall_t0 = self.old_wall
        t._stand_cb_mono_t0 = self.old_mono

    def measured_sample(self):
        t._stand_begin_step()
        t._stand_match_diag_sample()
        t._stand_score_sample("match")

    def test_fresh_then_retained_score_and_psr(self):
        self.measured_sample()
        fresh = t._stand_log_snapshot(self.clock, 0)
        self.assertEqual((fresh["match_updated"], fresh["diag_updated"]), (1, 1))
        self.assertEqual(fresh["score_age_ms"], 0)
        self.clock += 0.8
        t._stand_begin_step()  # Early-return step, no matcher invocation.
        stale = t._stand_log_snapshot(self.clock, 0)
        self.assertEqual((stale["match_updated"], stale["diag_updated"]), (0, 0))
        self.assertAlmostEqual(stale["score_age_ms"], 800)
        self.assertAlmostEqual(stale["diag_age_ms"], 800)

    def test_acquisition_default_is_not_a_measured_match(self):
        t._stand_begin_step()
        t._stand_score_sample("acquisition_default")
        sample = t._stand_log_snapshot(self.clock, 0)
        self.assertEqual(sample["match_updated"], 0)
        self.assertEqual(sample["score_source"], "acquisition_default")
        self.assertIsNone(sample["diag_age_ms"])

    def test_unavailable_matcher_return_is_not_a_measurement(self):
        t._stand_begin_step()
        t._stand_score_sample("match")
        sample = t._stand_log_snapshot(self.clock, 0)
        self.assertEqual(sample["match_updated"], 0)
        self.assertEqual(sample["score_source"], "match_unavailable")

    def test_reset_removes_previous_acquisition_metadata(self):
        self.measured_sample()
        t._stand_reset_metrics()
        sample = t._stand_log_snapshot(self.clock, 0)
        self.assertIsNone(sample["score_age_ms"])
        self.assertIsNone(sample["diag_age_ms"])
        self.assertEqual(sample["match_updated"], 0)

    def test_camera_sample_freshness_and_missing_data(self):
        self.assertIsNone(t._stand_log_snapshot(self.clock, 0)["sample_fresh"])
        t._cam_shadow_dbg = {"sample_t": self.clock}
        self.assertEqual(t._stand_log_snapshot(self.clock, 0)["sample_fresh"], 1)
        self.clock += t.CAM_JUMP_MAX_VALID_DT_S + 0.001
        self.assertEqual(t._stand_log_snapshot(self.clock, 0)["sample_fresh"], 0)

    def test_wall_clock_requires_current_callback(self):
        t._stand_cb_wall_t0 = 299.97
        t._stand_cb_mono_t0 = 99.97
        sample = t._stand_log_snapshot(self.clock, 99.97)
        self.assertAlmostEqual(sample["wall_ms"], 30)
        self.assertEqual(sample["timing_source"], "perf_counter")
        other = t._stand_log_snapshot(self.clock, 99.98)
        self.assertIsNone(other["wall_ms"])
        self.assertEqual(other["timing_source"], "unavailable")

    def test_no_perf_counter_has_explicit_unavailable_clock(self):
        t.time = types.SimpleNamespace(monotonic=lambda: self.clock)
        t._stand_cb_wall_t0 = 99.97
        t._stand_cb_mono_t0 = 99.97
        sample = t._stand_log_snapshot(self.clock, 99.97)
        self.assertIsNone(sample["wall_ms"])
        self.assertEqual(sample["timing_source"], "unavailable")

    def test_event_details_include_evidence_and_thresholds(self):
        t._cam_shadow_dbg = {"sample_t": 99.5, "sample_seq": 7,
                             "dt_valid": True, "gray_delta": 23.0}
        details = t._stand_visual_event_details(self.clock)
        for token in ("sample_seq=7", "sample_age_ms=500.0", "gray_delta=23.0",
                      "dt_valid=True", "exp_ratio=None", "gray_threshold=",
                      "top_threshold=", "max_age_ms="):
            self.assertIn(token, details)

    def test_csv_row_preserves_score_but_marks_it_stale(self):
        captured = []
        logger = types.SimpleNamespace(enabled=True, _t0=99.0,
                                       row=captured.append, event=lambda text: None)
        with contextlib.ExitStack() as patches:
            for name, value in (
                    ("flight_log", logger),
                    ("lock_log", types.SimpleNamespace(active=False)),
                    ("_flight_prev_t", None), ("_flight_prev_state", "IDLE"),
                    ("_flight_prev_launch", None), ("_flight_prev_override", False),
                    ("track_state", "IDLE"), ("last_match_score", 0.99)):
                patches.enter_context(mock.patch.object(t, name, value))
            self.measured_sample()
            t._capture_flight_row(99.9)
            self.assertEqual(len(captured), 1, "row collection swallowed an exception")
            fresh = dict(zip(t._COLS, captured[-1]))
            self.assertEqual(len(captured[-1]), len(t._COLS))
            self.assertEqual(fresh["match_updated"], 1)
            self.assertEqual(fresh["match_score"], 0.99)
            self.clock += 0.8
            t._stand_begin_step()
            t._capture_flight_row(100.7)
            self.assertEqual(len(captured), 2)
            stale = dict(zip(t._COLS, captured[-1]))
            self.assertEqual(stale["match_score"], 0.99)
            self.assertEqual(stale["match_updated"], 0)
            self.assertEqual(stale["match_diag_updated"], 0)
            self.assertAlmostEqual(stale["match_score_age_ms"], 800)


class CompletionReportTests(unittest.TestCase):
    def report(self, why, duration):
        with tempfile.TemporaryDirectory() as directory:
            logger = t.LockLogger(directory)
            logger._path = directory
            logger.at_lock = {}
            logger._state_counts = collections.Counter(TRACKED=2, VISUAL_UNSTABLE=3)
            row = [None] * len(t._COLS)
            row[t._C_STATE] = "VISUAL_UNSTABLE"
            logger._hvost.append(tuple(row))
            logger._zapisat_itog(why, duration)
            return Path(directory, "итог.txt").read_text(encoding="utf-8")

    def test_manual_stop_does_not_claim_a_tracking_loss(self):
        report = self.report("AUX4 выключен", 2.0)
        self.assertIn("completion_kind=manual_stop", report)
        self.assertIn("ЗАХВАТ ОСТАНОВЛЕН ОПЕРАТОРОМ", report)
        self.assertNotIn("СЛЕЖЕНИЕ СОРВАЛОСЬ", report)
        self.assertIn("TRACKED=2, VISUAL_UNSTABLE=3", report)

    def test_incomplete_and_other_session_end_are_distinct(self):
        self.assertIn("completion_kind=incomplete", self.report("unknown", None))
        self.assertIn("completion_kind=session_end", self.report("shutdown", 2.0))


if __name__ == "__main__":
    unittest.main()
