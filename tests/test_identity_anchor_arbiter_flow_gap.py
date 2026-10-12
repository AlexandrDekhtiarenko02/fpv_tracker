"""ANCHOR ARBITER: flow_gap (расхождение flow с matcher) при обычной
ручной тряске НЕ переводит в persistent IDENTITY_UNCERTAIN, пока anchor
свежо подтверждает ту же позицию (реальные стендовые логи 8e2ac74,
крупная цель, 2/17 ложных срывов, оба через flow_gap streak).

ЖИВОЙ ПРИМЕР ИЗ ЛОГОВ. zahvat26: matcher score=0.828 second=0.413
(явно однозначный, ambiguity=0), anchor_score=0.882 anchor_offset=0.039px
(практически идеальное anchor-подтверждение той же позиции), но
flow_gap: 11.29, 13.07, 13.67, 12.43, 6.84, 7.00 — 6 кадров подряд
> MATCH_GAP_SOFT=4 из-за тряски камеры. Старая архитектура: streak≥6 →
persistent UNCERTAIN. Новая: arbiter говорит "это по-прежнему та же
цель", persistent UNCERTAIN не срабатывает.

flow_predict застаблен на "уверенно не согласен с matcher'ом"
(симулирует тряску). matcher (fake) — уверенный, без ambiguity.
Реальный anchor-check на реальной сцене (объект на месте) подтверждает.
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
    "тест сам по себе негоден без IDENTITY_ANCHOR_ARBITER_ENABLED")


def make_scene(seed=0):
    """Крупная цель на своём месте — real anchor-check каждый periodic
    замер MUST её найти и подтвердить."""
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 40   # крупнее, чем в других файлах — соответствует zahvat26
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 4, 40, -1)
    cv2.rectangle(obj, (s // 4, s // 2), (3 * s // 4, 3 * s // 4), 60, -1)
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


_shake_offsets = [11.0, 13.0, 13.5, 12.5, 7.0, 7.0, 8.0, 9.0, 6.0, 5.0]
_shake_idx = [0]
_shake_last_off = [0.0]


def fake_flow_shake(prev_g, cur_g, pts, cx, cy):
    """Симулирует тряску камеры: flow сообщает position, сдвинутый от
    lock на shake_off. matcher (см. ниже) вернёт исходную позицию lock,
    так flow_gap = shake_off — точный воспроизведённый zahvat26.

    Один цикл fake_flow + fake_match на кадр, порядок вызовов
    гарантирован (flow первый, match второй). shake_idx хранит номер
    кадра, shake_last_off — офсет, использованный в ЭТОМ кадре, чтобы
    match мог вернуться на shake_last_off px назад от pred (=восстано-
    вить lock)."""
    off = _shake_offsets[_shake_idx[0] % len(_shake_offsets)]
    _shake_last_off[0] = off
    _shake_idx[0] += 1
    return True, cx + off, cy


def fake_match_confident(gray, pred_cx, pred_cy, flow_motion=0.0,
                          tgt_dx=0.0, tgt_dy=0.0):
    """Реалистичный zahvat26: score=0.828 second=0.413 (lead≈0.5, явно
    выше MATCH_LEAD_FULL — matcher уверенно однозначен, никакой
    ambiguity). Возвращает позицию, отстоящую от pred на -shake_last_off
    — восстанавливает исходный lock, так flow_gap = shake_last_off."""
    t._match_dbg["second"] = 0.413
    return True, pred_cx - _shake_last_off[0], pred_cy, 0.828


N_UNC = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

print("=== 1. Крупная цель + shake (flow_gap>MATCH_GAP_SOFT каждый кадр) "
      "+ matcher confident + anchor confirms → NOT persistent UNCERTAIN "
      "(реальный сценарий zahvat23/26 логов 8e2ac74) ===")
capture()
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_shake_idx[0] = 0
t.flow_predict = fake_flow_shake
t.template_match_locked = fake_match_confident
_events = []
t.flight_log.event = _events.append
_states = []
_streak_seen = []
_arb_seen = []
_gap_seen = []
_amb_seen = []
for i in range(N_UNC * 3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _states.append(t.track_state)
    _streak_seen.append(t._match_dbg.get("identity_uncertain_streak"))
    _arb_seen.append(t._match_dbg.get("identity_anchor_recently_confirmed"))
    _gap_seen.append(t._match_dbg.get("identity_flow_gap"))
    _amb_seen.append(t._match_dbg.get("identity_ambiguous"))
print("    identity_flow_gap:", _gap_seen)
print("    identity_ambiguous:", _amb_seen)
print("    streak:", _streak_seen)
print("    anchor_recently_confirmed:", _arb_seen)
print("    track_state (уникальные):", sorted(set(_states)))
assert all(g == 1 for g in _gap_seen), (
    "identity_flow_gap не выставлен на всех кадрах shake'а: %s" % _gap_seen)
assert all(a == 0 for a in _amb_seen), (
    "identity_ambiguous выставлен, хотя fake matcher confident: %s" % _amb_seen)
assert max(_streak_seen) >= N_UNC, (
    "streak ни разу не дошёл до CONFIRM_FRAMES=%d — тест сам по себе "
    "негоден (без arbiter'а мы бы сработали): %s" % (N_UNC, _streak_seen))
assert all(s == t.TRACK_STATE_TRACKED for s in _states), (
    "track_state покинул TRACKED — arbiter не заблокировал streak-триггер "
    "на shake-flow_gap: %s" % _states)
assert any(_arb_seen), (
    "тест сам по себе негоден: arbiter не подтверждал")
assert t._identity_anchor_gray is _anchor_ref
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
_iu_events = [e for e in _events if e.startswith("IDENTITY_UNCERTAIN")]
assert not _iu_events, (
    "событие IDENTITY_UNCERTAIN появилось: %s" % _iu_events)
print("    OK: flow_gap>MATCH_GAP_SOFT %d кадров подряд, matcher чёткий, "
      "anchor подтверждал — persistent UNCERTAIN не сработал"
      % (len(_gap_seen)))

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: flow_gap (ручная тряска, реальный сценарий крупной цели из "
      "логов 8e2ac74 zahvat23/26) БОЛЬШЕ НЕ роняет лок в persistent "
      "IDENTITY_UNCERTAIN, если matcher уверенно однозначен и arbiter "
      "подтверждает ту же позицию. Диагностика identity_flow_gap по-"
      "прежнему честна.")
