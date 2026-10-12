"""Brightness diagnostics and LK recovery through the real tracker pipeline."""
from pathlib import Path
import sys
import unittest
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import offline

t = offline.load_tracker()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.events = []
        self.old_time = t.time
        owner = self

        class Clock:
            def monotonic(self):
                return owner.now

            def __getattr__(self, name):
                return getattr(owner.old_time, name)

        t.time = Clock()
        self.addCleanup(setattr, t, "time", self.old_time)
        event = mock.patch.object(t.flight_log, "event", self.events.append)
        event.start()
        self.addCleanup(event.stop)
        t.reset_tracking(to_acq=False)
        t._cam_shadow_dbg = {}
        t.color_active = False
        t.chroma_u = t.chroma_v = None
        t.aux4_state = True
        t.prev_aux_on = True
        t.acq_wait_left = 0
        t.track_state = t.TRACK_STATE_ACQ
        rng = np.random.default_rng(0)
        self.obj = (rng.random((24, 24)) * 100 + 100).astype(np.uint8)
        cv2.circle(self.obj, (8, 8), 4, 40, -1)
        self.frame = self.scene()
        t.process_locked_tracker(self.frame)
        self.assertEqual(t.track_state, t.TRACK_STATE_TRACKED)
        self.anchor = t._identity_anchor_gray
        self.anchor_bytes = self.anchor.copy()
        self.events.clear()

    def scene(self, shift=0, background=120):
        frame = np.full((t.LORES_H, t.LORES_W), background, np.uint8)
        cx, cy = t.LORES_W // 2 + shift, t.LORES_H // 2
        frame[cy - 12:cy + 12, cx - 12:cx + 12] = self.obj
        return frame

    def step(self, dt=1.0 / t.CAM_FPS, frame=None):
        self.now += dt
        t.process_locked_tracker(self.frame if frame is None else frame)

    def assert_anchor_unchanged(self):
        self.assertIs(t._identity_anchor_gray, self.anchor)
        np.testing.assert_array_equal(self.anchor, self.anchor_bytes)

    def test_brightness_only_jump_continues_matching_for_entire_sample(self):
        for metadata in [(None, None), (2000.0, 2000.0)]:
            with self.subTest(metadata=metadata):
                t._cam_shadow_dbg = t._camera_jump_check(
                    *metadata, 166.0, 130.0, 252.0, 1.0)
                t._cam_shadow_dbg["sample_t"] = self.now
                self.assertTrue(t._cam_shadow_dbg["jump"])
                self.assertTrue(t._cam_shadow_dbg["top_saturated"])
                for _ in range(25):
                    self.step(frame=self.scene(background=156))
                    self.assertEqual(t.track_state, t.TRACK_STATE_TRACKED)
                    self.assertEqual(t._stand_log_snapshot(self.now, 0)["match_updated"], 1)
                self.assert_anchor_unchanged()
        self.assertFalse(any(x.startswith("VISUAL_UNSTABLE") for x in self.events))

    def test_measured_exposure_jump_still_suspends_and_recovers(self):
        original_reference = t.prev_gray
        t._cam_shadow_dbg = t._camera_jump_check(4000, 2000, 130, 130, 120, 1)
        t._cam_shadow_dbg["sample_t"] = self.now
        for _ in range(24):
            self.step()
            self.assertEqual(t.track_state, t.TRACK_STATE_VISUAL_UNSTABLE)
            self.assertIs(t.prev_gray, original_reference)
        t._cam_shadow_dbg = {}
        self.step(frame=self.scene(shift=3))
        self.assertEqual(t.track_state, t.TRACK_STATE_TRACKED)
        self.assertEqual(t._flow_pair_dbg["reinitialized"], 1)
        self.assertEqual(t._flow_pair_dbg["reason"], "processing_gap")
        self.assertFalse(t.last_flow_ok)  # No fictitious same-frame LK result.
        self.assertIsNotNone(t.prev_pts)
        self.assert_anchor_unchanged()
        self.step(frame=self.scene(shift=3))
        self.assertEqual(t._flow_pair_dbg["reinitialized"], 0)
        self.assertTrue(t.last_flow_ok)

    def test_gap_skips_old_lk_pair_then_seeds_fresh_points(self):
        t._match_dbg["flow_gap"] = 99.0  # Previous measurement must not leak.
        with mock.patch.object(t, "flow_predict", wraps=t.flow_predict) as flow:
            self.step(dt=1.0, frame=self.scene(shift=4))
        self.assertIsNone(flow.call_args.args[0])
        self.assertIsNone(flow.call_args.args[2])
        self.assertEqual(t.track_state, t.TRACK_STATE_TRACKED)
        self.assertFalse(t.last_flow_ok)
        self.assertEqual(t._flow_pair_dbg["age_ms"], 1000.0)
        self.assertEqual(t._flow_pair_dbg["reinitialized"], 1)
        self.assertNotIn("flow_gap", t._match_dbg)
        self.assertEqual(t._match_dbg["adapt_skip_reason"], "no_flow")
        np.testing.assert_array_equal(t.prev_gray, self.scene(shift=4))
        self.assertEqual(t._flow_reference_t, self.now)
        self.assert_anchor_unchanged()
        self.step(frame=self.scene(shift=4))
        self.assertTrue(t.last_flow_ok)
        self.assertEqual(t._flow_pair_dbg["reinitialized"], 0)

    def test_gap_refreshes_points_even_outside_periodic_refresh_slot(self):
        with mock.patch.object(t, "FLOW_REFRESH_EVERY", 1000):
            self.step(dt=1)
        self.assertIsNotNone(t.prev_pts)
        self.assertGreaterEqual(len(t.prev_pts), t.FLOW_MIN_POINTS)

    def test_hold_after_gap_also_seeds_current_reference(self):
        blank = np.full_like(self.frame, 120)
        with mock.patch.object(t, "template_match_locked", return_value=(False, t.lock_cx, t.lock_cy, 0.0)), \
                mock.patch.object(t, "refresh_flow_points", wraps=t.refresh_flow_points) as refresh:
            self.step(dt=1, frame=blank)
        self.assertEqual(t.track_state, t.TRACK_STATE_HOLD)
        self.assertEqual(refresh.call_count, 1)
        np.testing.assert_array_equal(refresh.call_args.args[0], blank)
        np.testing.assert_array_equal(t.prev_gray, blank)
        self.assertEqual(t._flow_reference_t, self.now)
        self.assert_anchor_unchanged()

    def test_pair_age_boundary_clock_regression_and_unknown_time(self):
        for age, expected in [(t.FLOW_RASSH_SVEZH_S, False),
                              (t.FLOW_RASSH_SVEZH_S + .001, True),
                              (-.01, True), (None, True)]:
            with self.subTest(age=age):
                t.prev_gray = self.frame.copy()
                t.prev_pts = np.zeros((8, 1, 2), np.float32)
                t._flow_reference_t = None if age is None else 10.0
                t._stand_begin_step()
                self.assertEqual(t._prepare_flow_pair(10.0 if age is None else 10.0 + age), expected)
                self.assertEqual(t._flow_pair_dbg["reinitialized"], int(expected))

    def test_reference_reset_and_normal_cadence(self):
        self.assertEqual(t._flow_reference_t, self.now)
        for _ in range(5):
            self.step()
            self.assertTrue(t.last_flow_ok)
            self.assertEqual(t._flow_pair_dbg["reinitialized"], 0)
        t.reset_tracking(to_acq=False)
        self.assertIsNone(t._flow_reference_t)
        self.assertIsNone(t.prev_gray)
        self.assertIsNone(t.prev_pts)

    def test_csv_records_gap_once_and_clears_idle_diagnostics(self):
        rows = []
        with mock.patch.object(t.flight_log, "enabled", True), \
                mock.patch.object(t.flight_log, "row", rows.append), \
                mock.patch.object(t.lock_log, "active", False), \
                mock.patch.object(t, "_flight_prev_state", t.TRACK_STATE_TRACKED):
            self.step(dt=1)
            t._capture_flight_row(self.now)
            self.assertEqual(len(rows), 1, "CSV collector swallowed an exception")
            gap = dict(zip(t._COLS, rows[-1]))
            self.assertEqual(len(rows[-1]), len(t._COLS))
            self.assertEqual(gap["flow_reinitialized"], 1)
            self.assertEqual(gap["flow_pair_age_ms"], 1000)
            self.assertEqual(gap["flow_reinit_reason"], "processing_gap")
            self.step()
            t._capture_flight_row(self.now)
            normal = dict(zip(t._COLS, rows[-1]))
            self.assertEqual(normal["flow_reinitialized"], 0)
            self.assertEqual(normal["flow_reinit_reason"], "")
            t.fast_idle_update()
            t._capture_flight_row(self.now)
            idle = dict(zip(t._COLS, rows[-1]))
            self.assertIsNone(idle["flow_pair_age_ms"])
            self.assertEqual(idle["flow_reinitialized"], 0)

    def test_discarding_pair_does_not_reset_identity_or_templates(self):
        template = t.template_gray
        base = t.template_base
        t._identity_anchor_check_streak = 2
        t._identity_uncertain_streak = 7
        t._identity_uncertain_pending = True
        t.lost_frames = 4
        self.assertTrue(t._prepare_flow_pair(self.now + 1))
        self.assertEqual(t._identity_anchor_check_streak, 2)
        self.assertEqual(t._identity_uncertain_streak, 7)
        self.assertTrue(t._identity_uncertain_pending)
        self.assertEqual(t.lost_frames, 4)
        self.assertIs(t.template_gray, template)
        self.assertIs(t.template_base, base)
        self.assert_anchor_unchanged()

    def test_invalid_camera_intervals_and_future_samples_do_not_block(self):
        for dt in [None, -.01, 0.0, t.CAM_JUMP_MAX_VALID_DT_S + .01]:
            sample = t._camera_jump_check(4000, 2000, 166, 130, 252, dt)
            sample["sample_t"] = self.now
            self.assertFalse(sample["dt_valid"])
            self.assertEqual(t._visual_unstable_now(sample, self.now), (False, ""))
        sample = t._camera_jump_check(4000, 2000, 130, 130, 120, 1)
        sample["sample_t"] = self.now + .01
        self.assertEqual(t._visual_unstable_now(sample, self.now), (False, ""))


if __name__ == "__main__":
    unittest.main()
