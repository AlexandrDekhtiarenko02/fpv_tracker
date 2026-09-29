"""TEST H/I (разбор оператора, п.7): затяжной single-signal bypass — "flow
согласен с matcher" ДОКАЗЫВАЕТ, что два алгоритма сейчас смотрят в одно
место, но не то, что это ВСЁ ЕЩЁ confirmed identity A. Обратная сторона той
же проблемы: система способна жить СКОЛЬ УГОДНО ДОЛГО, полагаясь только на
ОДИН из двух сигналов (flow_ok ИЛИ match_ok, никогда оба разом) — ни разу
не имея возможности независимо сверить их друг с другом. Короткий разрыв
(предсказание на кадр-два, пока другой сигнал временно недоступен) —
нормальная работа. Долгий — сам по себе уже сигнал, независимо от того,
ошибается каждый сигнал по отдельности или нет.

ЭТО НЕ НОВЫЙ CV score (прямое требование отчёта) — IDENTITY_DUAL_SIGNAL_GAP
переиспользует ТОТ ЖЕ флаг _identity_dual_signal_frame, которым уже
управляет IDENTITY_UNCERTAIN_CONFIRM_FRAMES (см. test_identity_uncertain.py
§9) — другая, отдельная state-machine семантика поверх него: не "N ПОДРЯД
ПРОТИВОРЕЧИВЫХ dual-signal кадров", а "сколько TRACKED-кадров подряд идёт
БЕЗ ЕДИНОЙ dual-signal возможности вообще".

IDENTITY_ANCHOR_CHECK_ENABLED изолирован здесь намеренно — этот файл
проверяет ИМЕННО gap-механизм (п.7), периодическая сверка с anchor — уже
отдельно, в test_identity_anchor_agreement_trap.py (п.6/K-4).
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


def make_scene(seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()

assert t.IDENTITY_DUAL_SIGNAL_GAP_ENABLED, (
    "тест сам по себе негоден без IDENTITY_DUAL_SIGNAL_GAP_ENABLED")
_orig_anchor_check_enabled = t.IDENTITY_ANCHOR_CHECK_ENABLED
t.IDENTITY_ANCHOR_CHECK_ENABLED = False
N = t.IDENTITY_DUAL_SIGNAL_GAP_MAX_FRAMES


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


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


def fake_flow_only(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_fails(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    return False, pred_cx, pred_cy, 0.0


def fake_flow_fails(prev_g, cur_g, pts, cx, cy):
    return False, cx, cy


def fake_match_only(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


print("=== H. Только flow жив (match_ok=False) N=%d кадров подряд — "
      "TRACKED не может держаться бесконечно ===" % N)
capture()
_anchor_ref_h = t._identity_anchor_gray
_anchor_bytes_h = t._identity_anchor_gray.copy()
t.flow_predict = fake_flow_only
t.template_match_locked = fake_match_fails
_events_h = []
t.flight_log.event = _events_h.append
_gap_seen_h = []
_states_h = []
for i in range(N + 3):
    tick()
    _gap_seen_h.append(t._match_dbg.get("identity_dual_signal_gap_frames"))
    _states_h.append(t.track_state)
    if t.track_state != t.TRACK_STATE_TRACKED:
        break
print("    identity_dual_signal_gap_frames (первые/последние 5):",
      _gap_seen_h[:5], "...", _gap_seen_h[-5:])
print("    track_state финально: %s, после %d кадров" % (_states_h[-1], len(_states_h)))
assert _gap_seen_h == list(range(1, len(_gap_seen_h) + 1)), (
    "gap-счётчик не растёт РОВНО на 1 каждый single-signal кадр: %s"
    % _gap_seen_h)
assert all(s == t.TRACK_STATE_TRACKED for s in _states_h[:N - 1]), (
    "track_state покинул TRACKED раньше N=%d кадров: %s" % (N, _states_h))
assert _states_h[-1] == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "после %d кадров подряд только с flow (matcher ни разу) track_state=%r, "
    "ожидали IDENTITY_UNCERTAIN — иначе automation способен оставаться "
    "controllable TRACKED БЕСКОНЕЧНО на одном лишь потоке" % (N, _states_h[-1]))
with t.state_lock:
    _controllable_h = t.target_controllable
assert not _controllable_h, "controllable остался True после срабатывания"
_gap_events_h = [e for e in _events_h if "dual_signal_gap" in e]
assert len(_gap_events_h) == 1, (
    "ожидали ровно 1 событие с dual_signal_gap, получили %d: %s"
    % (len(_gap_events_h), _gap_events_h))
assert ("dual_signal_gap=%d" % N) in _gap_events_h[0], (
    "событие не называет правильное число кадров: %s" % _gap_events_h[0])
assert t._identity_anchor_gray is _anchor_ref_h
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes_h)
print("    событие: %s" % _gap_events_h[0])
print("    OK: только-flow дольше N=%d кадров -> IDENTITY_UNCERTAIN, "
      "controllable=False, anchor не тронут" % N)

print("\n=== I. Только matcher жив (flow_ok=False) N=%d кадров подряд — "
      "аналогично ===" % N)
t.reset_tracking(to_acq=False)
capture()
_anchor_ref_i = t._identity_anchor_gray
_anchor_bytes_i = t._identity_anchor_gray.copy()
t.flow_predict = fake_flow_fails
t.template_match_locked = fake_match_only
_events_i = []
t.flight_log.event = _events_i.append
_gap_seen_i = []
_states_i = []
for i in range(N + 3):
    tick()
    _gap_seen_i.append(t._match_dbg.get("identity_dual_signal_gap_frames"))
    _states_i.append(t.track_state)
    if t.track_state != t.TRACK_STATE_TRACKED:
        break
print("    track_state финально: %s, после %d кадров" % (_states_i[-1], len(_states_i)))
assert all(s == t.TRACK_STATE_TRACKED for s in _states_i[:N - 1]), (
    "track_state покинул TRACKED раньше N=%d кадров: %s" % (N, _states_i))
assert _states_i[-1] == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "после %d кадров подряд только с matcher (flow ни разу) track_state=%r, "
    "ожидали IDENTITY_UNCERTAIN" % (N, _states_i[-1]))
with t.state_lock:
    _controllable_i = t.target_controllable
assert not _controllable_i, "controllable остался True после срабатывания"
assert t._identity_anchor_gray is _anchor_ref_i
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes_i)
print("    OK: только-matcher дольше N=%d кадров -> IDENTITY_UNCERTAIN, "
      "controllable=False, anchor не тронут" % N)

print("\n=== J. Контроль: КОРОТКИЙ разрыв (меньше N) не триггерит — "
      "чередование single/dual-signal сбрасывает счётчик, не копит его "
      "между разрывами ===")
t.reset_tracking(to_acq=False)
capture()
t.flow_predict = fake_flow_only
t.template_match_locked = fake_match_fails
for _ in range(N - 5):
    tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
assert t._match_dbg.get("identity_dual_signal_gap_frames") == N - 5
# Один честный dual-signal кадр — сбрасывает счётчик.
t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
tick()
assert t._match_dbg.get("identity_dual_signal_gap_frames") == 0, (
    "один dual-signal кадр не сбросил счётчик разрыва в 0, получено %r"
    % t._match_dbg.get("identity_dual_signal_gap_frames"))
assert t.track_state == t.TRACK_STATE_TRACKED
# Дальше ещё N-1 single-signal кадров подряд — НЕ должно накопиться сразу
# до триггера (счётчик считает от сброса, не от начала теста).
t.flow_predict = fake_flow_only
t.template_match_locked = fake_match_fails
for _ in range(N - 1):
    tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "счётчик разрыва не сбросился по-настоящему — сработал раньше "
    "положенного N=%d после честного dual-signal кадра" % N)
print("    N-5 single-signal -> 1 честный dual-signal (сброс в 0) -> N-1 "
      "single-signal -> всё ещё TRACKED — счётчик действительно считает от "
      "последнего dual-signal кадра, не накапливается между разрывами")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.IDENTITY_ANCHOR_CHECK_ENABLED = _orig_anchor_check_enabled
t.reset_tracking(to_acq=False)

print("\nOK: длительное удержание TRACKED только на одном из двух "
      "независимых сигналов (flow ИЛИ matcher, никогда оба сразу) больше "
      "не может продолжаться бесконечно — после IDENTITY_DUAL_SIGNAL_GAP_"
      "MAX_FRAMES кадров подряд без единой dual-signal возможности "
      "система уходит в IDENTITY_UNCERTAIN, а не остаётся controllable "
      "TRACKED на недоказанной identity.")
