"""K-4 (ОБЯЗАТЕЛЬНЫЙ, разбор оператора поверх dc49a29, п.6/TEST G): flow И
matcher СОГЛАСНЫ МЕЖДУ СОБОЙ на patch B, но B не соответствует immutable
identity anchor A -> IDENTITY_UNCERTAIN, а не silent TRACKED.

ПОЧЕМУ ЭТО НЕ ДУБЛИКАТ K-3 (test_identity_anchor_no_silent_switch.py).
K-3: flow=A (честно остаётся на месте), matcher=B — они РАСХОДЯТСЯ, и это
расхождение (dist_fm) уже само по себе сигнал, которым process_locked_
tracker умел пользоваться ДО этой правки (п.D/E, отчёт 25.09). K-4: flow=B
И matcher=B одновременно — dist_fm мал, ambiguity (lead) не срабатывает,
_identity_uncertain_streak остаётся 0 ВЕСЬ прогон. Раньше это был чистый
hard-lock bypass: ни один существующий сигнал его не видел, при этом
_identity_anchor_gray честно продолжал бы хранить A. Ловит это ТОЛЬКО
новый, независимый механизм — периодическая сверка предложенной позиции с
_identity_anchor_gray (IDENTITY_ANCHOR_CHECK_ENABLED), который не
полагается на согласие/несогласие flow и matcher друг с другом.

flow_predict И template_match_locked оба застаблены на "уверенно нашли
что-то на B" — реальная механика согласия между ними тут не важна (она
и есть источник опасности: два независимых алгоритма могут ОБА
одновременно ошибиться на одну и ту же постороннюю структуру, что и
показали реальные логи 25.09). Сцена — реальная, БЕЗ фейка: рядом с A
(единственный реальный объект в кадре) на месте B — плоский фон, ничего
похожего на A там на самом деле нет. Анкор-проверка использует РЕАЛЬНЫЙ
_shadow_match_against_template — только flow/match, ведущие позицию К B,
детерминированы.
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

assert t.IDENTITY_ANCHOR_CHECK_ENABLED, (
    "тест сам по себе негоден без IDENTITY_ANCHOR_CHECK_ENABLED")


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


capture()
A_cx, A_cy = t.lock_cx, t.lock_cy
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()

B_cx, B_cy = A_cx + 60.0, A_cy
assert B_cx < t.LORES_W - 20, "тест сам по себе негоден: B вне кадра"


def fake_flow_to_b(prev_g, cur_g, pts, cx, cy):
    """Поток 'уверенно' считает, что цель уже на B — ОШИБСЯ сам, не
    честно продолжает следить за A (это и есть отличие от K-3)."""
    return True, B_cx, B_cy


def fake_match_to_b(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    """Матч ПОЛНОСТЬЮ согласен с потоком (та же позиция, высокий score,
    низкий second -> lead большой, ambiguity guard не видит НИЧЕГО)."""
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


def trap_scene():
    """РЕАЛЬНЫЙ кадр: A на своём месте (как и был при захвате), на месте B
    — ничего, кроме плоского фона. Fakes выше уводят ПОЗИЦИЮ к B; сама
    сцена никакого 'B' не содержит вовсе — ловит именно то, что оба
    алгоритма ошиблись, а не что там реально что-то похожее появилось."""
    return scene.copy()


trap = trap_scene()

print("=== 1. flow=B и matcher=B СОГЛАСНЫ друг с другом — существующие "
      "сигналы (streak/ambiguous/flow_gap) остаются 0 весь прогон ===")
t.flow_predict = fake_flow_to_b
t.template_match_locked = fake_match_to_b
_events = []
t.flight_log.event = _events.append
_states = []
_iu_streak_seen = []
_ambiguous_seen = []
_gap_seen = []
N_FRAMES = 8
_lock_before_this_frame = (t.lock_cx, t.lock_cy)
_lock_before_trigger_frame = None
for i in range(N_FRAMES):
    _lock_before_this_frame = (t.lock_cx, t.lock_cy)
    _clk.tick(0.5)   # см. IDENTITY_ANCHOR_CHECK_PERIOD_S=0.5 — гоним
                     # часы так, чтобы периодическая сверка успевала
                     # сработать на каждом кадре, не ждать десятки тиков
    t.process_locked_tracker(trap)
    _states.append(t.track_state)
    _iu_streak_seen.append(t._match_dbg.get("identity_uncertain_streak"))
    _ambiguous_seen.append(t._match_dbg.get("identity_ambiguous"))
    _gap_seen.append(t._match_dbg.get("identity_flow_gap"))
    if t.track_state != t.TRACK_STATE_TRACKED:
        _lock_before_trigger_frame = _lock_before_this_frame
        break
print("    track_state по кадрам:", _states)
print("    identity_uncertain_streak по кадрам:", _iu_streak_seen)
assert all(s == 0 for s in _iu_streak_seen), (
    "identity_uncertain_streak вырос хотя бы раз (%s) — тест сам по себе "
    "негоден: flow/matcher обязаны быть ПОЛНОСТЬЮ согласны друг с другом, "
    "старый механизм не должен был увидеть здесь вообще ничего" % _iu_streak_seen)
assert not any(_ambiguous_seen), (
    "identity_ambiguous сработал хотя бы раз — fakes сконструированы "
    "неверно, lead должен быть большим (second=0.05 << score=0.90)")
assert not any(_gap_seen), (
    "identity_flow_gap сработал хотя бы раз — match и flow заданы в ТОЧНО "
    "одну и ту же позицию, dist_fm обязан быть 0")
print("    подтверждено: streak/ambiguous/flow_gap — ВСЕ 0 на каждом "
      "кадре, старые сигналы этот сценарий не видят")

print("\n=== 2. Тем не менее — IDENTITY_UNCERTAIN, а не silent TRACKED ===")
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "track_state=%r после серии кадров с согласным, но не подтверждённым "
    "anchor'ом B — ожидали IDENTITY_UNCERTAIN. Если это TRACKED — hard-"
    "lock bypass снова открыт: flow и matcher согласны друг с другом, а "
    "automation продолжает жить на B" % t.track_state)
with t.state_lock:
    _controllable = t.target_controllable
assert not _controllable, "controllable остался True при срабатывании"
_anchor_events = [e for e in _events if e.startswith("IDENTITY_UNCERTAIN")]
assert len(_anchor_events) == 1, (
    "ожидали ровно 1 событие IDENTITY_UNCERTAIN, получили %d: %s"
    % (len(_anchor_events), _anchor_events))
assert "anchor" in _anchor_events[0], (
    "событие не указывает anchor как причину срабатывания: %s" % _anchor_events[0])
print("    событие: %s" % _anchor_events[0])
print("    track_state=IDENTITY_UNCERTAIN, controllable=False — hard-lock "
      "не пробит")

print("\n=== 3. Confirmed identity anchor не тронут ни разу за весь "
      "эпизод ===")
assert t._identity_anchor_gray is _anchor_ref, (
    "_identity_anchor_gray стал другим объектом")
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
    "байты _identity_anchor_gray изменились")
_ia_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
assert not _ia_events, (
    "событие 'IDENTITY ANCHOR: подтверждена' произошло во время эпизода "
    "с B: %s" % _ia_events)
print("    _identity_anchor_gray — тот же объект, те же байты; ни одного "
      "события 'IDENTITY ANCHOR' за весь эпизод")

print("\n=== 4. Коммит-гейт (п.E): ИМЕННО триггерящий кадр не сдвинул "
      "lock_cx/cy — позиция могла уже приехать к B РАНЬШЕ (flow и matcher "
      "тут полностью согласны, дамп-фактор dist_fm=0 не тормозит блендинг "
      "— это не то же самое, что K-3), но САМ момент срабатывания не "
      "имеет права что-либо закоммитить ===")
assert _lock_before_trigger_frame is not None, (
    "тест сам по себе негоден: триггер не случился в пределах %d кадров"
    % N_FRAMES)
print("    lock_cx/cy до триггерящего кадра: %r, после: (%.1f, %.1f)"
      % (_lock_before_trigger_frame, t.lock_cx, t.lock_cy))
assert (t.lock_cx, t.lock_cy) == _lock_before_trigger_frame, (
    "lock_cx/cy изменились НА САМОМ триггерящем кадре (%r -> %r) — "
    "коммит-гейт (п.E) не остановил его" % (_lock_before_trigger_frame,
                                             (t.lock_cx, t.lock_cy)))

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: flow и matcher, СОГЛАСНЫЕ друг с другом на patch B (dist_fm=0, "
      "ambiguity нет, старый streak все кадры остаётся 0) — это НЕ "
      "доказательство identity. Периодическая сверка с confirmed identity "
      "anchor ловит именно этот, ранее не закрытый hard-lock bypass: "
      "IDENTITY_UNCERTAIN/controllable=False, anchor не тронут ни разу, "
      "TRACKED на постороннем B не наступает.")
