"""ANCHOR ARBITER: matcher ambiguous (score≈second) НЕ переводит в
persistent IDENTITY_UNCERTAIN, пока anchor свежо подтверждает ту же
позицию (реальные стендовые логи 8e2ac74, tiny-цель, 13/17 ложных
срывов).

ЖИВОЙ ПРИМЕР ИЗ ЛОГОВ. zahvat07: score=0.939, second=0.938 (lead≈0.001,
явная ambiguity — несколько равных correlation peaks), но
anchor_score=0.989, anchor_offset=0.143px — immutable anchor чётко
подтверждает ту же позицию. Старая архитектура (streak≥6 → persistent
UNCERTAIN) роняла лок. Новая: arbiter говорит "это по-прежнему A",
persistent UNCERTAIN не срабатывает.

Здесь fake matcher форсирует ambiguity (score/second заведомо близкие),
flow_predict возвращает pred (position stable). Реальный периодический
anchor-check на реальной сцене (A по-прежнему на своём месте) уверенно
подтверждает — arbiter должен погасить streak-trigger.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS

assert t.IDENTITY_ANCHOR_ARBITER_ENABLED, (
    "тест сам по себе негоден без IDENTITY_ANCHOR_ARBITER_ENABLED — "
    "это и есть проверяемое поведение")


def make_scene(seed=0):
    """Обычная сцена: цель на своём месте (для реального периодического
    anchor-check'а, который MUST найти anchor и подтвердить)."""
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.flow_predict = _real_flow_predict
    t.template_match_locked = _real_template_match_locked
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def fake_flow_stable(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_ambiguous(gray, pred_cx, pred_cy, flow_motion=0.0,
                          tgt_dx=0.0, tgt_dy=0.0):
    """Реалистичное дублирование zahvat07: score=0.939 second=0.938
    (lead≈0.001, ниже MATCH_LEAD_FULL — identity_ambiguous=1), dist_fm=0
    (нет flow_gap), matcher по-прежнему возвращает pred position."""
    t._match_dbg["second"] = 0.938
    return True, pred_cx, pred_cy, 0.939


N_UNC = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

print("=== 1. tiny target + ambiguity (score≈second) + anchor confirms → "
      "NOT persistent UNCERTAIN (реальный сценарий zahvat07/10/12 логов "
      "8e2ac74) ===")
capture()
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
t.flow_predict = fake_flow_stable
t.template_match_locked = fake_match_ambiguous
_events = []
t.flight_log.event = _events.append
_streak_seen = []
_arb_seen = []
_states = []
# Гоним заметно дольше N_UNC, чтобы streak гарантированно прошёл
# CONFIRM_FRAMES — если бы arbiter не работал, ушло бы в UNCERTAIN.
for i in range(N_UNC * 3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _streak_seen.append(t._match_dbg.get("identity_uncertain_streak"))
    _arb_seen.append(t._match_dbg.get("identity_anchor_recently_confirmed"))
    _states.append(t.track_state)
print("    streak: %s" % _streak_seen)
print("    anchor_recently_confirmed: %s" % _arb_seen)
print("    track_state (уникальные): %s" % sorted(set(_states)))
assert all(s == t.TRACK_STATE_TRACKED for s in _states), (
    "track_state покинул TRACKED хотя бы раз — arbiter не заблокировал "
    "streak-триггер: %s" % _states)
assert max(_streak_seen) >= N_UNC, (
    "streak ни разу не дошёл до CONFIRM_FRAMES=%d, тест сам по себе "
    "негоден (мы должны были бы триггернуть без arbiter'а): %s"
    % (N_UNC, _streak_seen))
assert any(_arb_seen), (
    "тест сам по себе негоден: arbiter ни разу не подтверждал — тогда "
    "TRACKED объясняется чем-то другим")
assert t._identity_anchor_gray is _anchor_ref
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
_iu_events = [e for e in _events if e.startswith("IDENTITY_UNCERTAIN")]
assert not _iu_events, (
    "событие IDENTITY_UNCERTAIN появилось: %s" % _iu_events)
print("    OK: streak дошёл до %d, arbiter подтверждал, persistent "
      "UNCERTAIN не сработал ни разу за %d кадров"
      % (max(_streak_seen), N_UNC * 3))

print("\n=== 2. Diagnostics: identity_ambiguous=1 продолжает честно "
      "отражать состояние matcher'а — arbiter гасит только state-machine "
      "transition, не сырую диагностику ===")
assert t._match_dbg.get("identity_ambiguous") == 1, (
    "identity_ambiguous не выставлен, хотя lead между score и second "
    "заведомо ниже MATCH_LEAD_FULL")
print("    identity_ambiguous=1 по-прежнему выставлен")

print("\n=== 3. Kill-switch IDENTITY_ANCHOR_ARBITER_ENABLED=False — та же "
      "сцена, но arbiter отключён: старое поведение (streak → UNCERTAIN) "
      "восстанавливается ===")
t.reset_tracking(to_acq=False)
capture()
t.flow_predict = fake_flow_stable
t.template_match_locked = fake_match_ambiguous
t.IDENTITY_ANCHOR_ARBITER_ENABLED = False
for _ in range(N_UNC * 2):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    if t.track_state != t.TRACK_STATE_TRACKED:
        break
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "ENABLED=False: старое поведение должно восстанавливаться, но "
    "track_state=%r" % t.track_state)
print("    ENABLED=False -> IDENTITY_UNCERTAIN (старое поведение "
      "воспроизведено, arbiter корректно управляется kill-switch'ом)")
t.IDENTITY_ANCHOR_ARBITER_ENABLED = True

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: matcher ambiguity (score≈second, реальный сценарий tiny-"
      "цели из логов 8e2ac74 zahvat07/10/12) — БОЛЬШЕ НЕ роняет лок в "
      "persistent IDENTITY_UNCERTAIN, если arbiter (свежий anchor-check) "
      "подтверждает ту же позицию. Диагностика identity_ambiguous по-"
      "прежнему честна, kill-switch восстанавливает старое поведение.")
