"""Anchor-check обязан подтверждать ИМЕННО proposed-позицию, а не «anchor
нашёлся ГДЕ-ТО в search window» (найдено оператором при разборе реального
кода bf7957f, не отчёта). _shadow_match_against_template ищет ЛУЧШИЙ
anchor-match ВНУТРИ окна поиска вокруг запрошенной позиции — она не
проверяет anchor РОВНО в этой позиции. Первая версия IDENTITY_ANCHOR_CHECK
(и симметрично — LOST->AUTO_REACQ, AUTO_TEMPLATE_REFRESH) читала только
score, полностью игнорируя возвращаемые координаты найденного совпадения
(_iac_mx/_iac_my).

СЦЕНАРИЙ: confirmed A остаётся ВИДИМОЙ в кадре (не убрана, в отличие от
test_identity_anchor_agreement_trap.py — там весь смысл K-4 был в том, что
flow/matcher СОГЛАСНЫ друг с другом; здесь другая опасность). Рядом с A, на
расстоянии, ставящем обе точки в одно search-окно, появляется ДРУГАЯ,
непохожая на A структура B. flow+matcher (застаблены) согласованно
"уверенно" сообщают, что цель теперь на B. Anchor-check ищет вокруг B,
находит A (она реально лучше всего похожа на anchor — это и есть anchor),
даёт высокий score — но это НЕ должно засчитываться как "B подтверждён":
anchor подтвердил A рядом с B, не сам B.
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
_real_shadow_match = t._shadow_match_against_template


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

OFFSET = 15.0   # больше допуска IDENTITY_ANCHOR_MATCH_MAX_OFFSET_FRAC*size
                # (0.5 * 24 = 12), меньше типичного search margin — обе
                # точки должны попасть в одно search-окно
B_cx, B_cy = A_cx + OFFSET, A_cy


def trap_scene_both_visible(seed=1):
    """A остаётся НЕТРОНУТОЙ на своём месте. Рядом (OFFSET px) — ДРУГАЯ,
    заметно непохожая на A структура B (другой паттерн: рамка на тёмном
    фоне, не текстурный круг)."""
    g = scene.copy()
    rng = np.random.default_rng(seed + 500)
    s2 = 20
    obj2 = (rng.random((s2, s2)) * 100 + 20).astype(np.uint8)
    cv2.rectangle(obj2, (2, 2), (s2 - 3, s2 - 3), 200, 2)
    y0, x0 = int(B_cy - s2 / 2), int(B_cx - s2 / 2)
    g[y0:y0 + s2, x0:x0 + s2] = obj2
    return g


def fake_flow_to_b(prev_g, cur_g, pts, cx, cy):
    return True, B_cx, B_cy


def fake_match_to_b(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


trap = trap_scene_both_visible()

print("=== 1. Контроль: РЕАЛЬНЫЙ _shadow_match_against_template вокруг B "
      "действительно находит A (не что-то ещё) с высоким score — сценарий "
      "не вырожден, старая (score-only) логика ДЕЙСТВИТЕЛЬНО была бы "
      "обманута ===")
# flow_motion — КАК В РЕАЛЬНОМ ПЕРВОМ кадре ловушки: lock_cx всё ещё на A,
# flow (застаблен) сообщает B — adaptive search margin в process_locked_
# tracker считает flow_motion = hypot(pred-lock) = OFFSET, а не 0. Именно
# ШИРОКОЕ окно (мнимое "быстрое движение") даёт A попасть в него — ровно
# тот момент, когда риск реально высок (drift только начался).
_iac_ok0, _iac_score0, _iac_psr0, _iac_second0, _iac_mx0, _iac_my0 = (
    _real_shadow_match(trap, _anchor_ref, t._identity_anchor_w,
                       t._identity_anchor_h, t._identity_anchor_std,
                       B_cx, B_cy, OFFSET))
_offset0 = ((_iac_mx0 - B_cx) ** 2 + (_iac_my0 - B_cy) ** 2) ** 0.5
_dist_to_a0 = ((_iac_mx0 - A_cx) ** 2 + (_iac_my0 - A_cy) ** 2) ** 0.5
print("    anchor-match вокруг B: (x=%.1f,y=%.1f) score=%.2f — расстояние "
      "до B=%.1fpx, до A=%.1fpx" % (_iac_mx0, _iac_my0, _iac_score0, _offset0, _dist_to_a0))
assert _iac_ok0 and _iac_score0 >= t.MATCH_GOOD_SCORE, (
    "тест сам по себе негоден: anchor-match вокруг B не дал уверенного "
    "score — старая (score-only) логика тоже отклонила бы, сценарий не "
    "проверяет то, что нужно")
assert _dist_to_a0 < _offset0, (
    "тест сам по себе негоден: найденная позиция не ближе к A, чем к B — "
    "проверить нечего, anchor и так не 'соблазнился' A")
print("    подтверждено: score-only проверка ЗДЕСЬ сказала бы 'B "
      "подтверждён' (score=%.2f >= %.2f), хотя реально найдена A "
      "(%.1fpx от неё против %.1fpx от B)"
      % (_iac_score0, t.MATCH_GOOD_SCORE, _dist_to_a0, _offset0))

print("\n=== 2. _anchor_confirms_position: ТА ЖЕ пара (ok, score) с "
      "координатами A -> НЕ подтверждает B ===")
assert not t._anchor_confirms_position(
    _iac_ok0, _iac_score0, _iac_psr0,
    _iac_mx0, _iac_my0, B_cx, B_cy), (
    "_anchor_confirms_position подтвердила B, хотя найденные координаты "
    "принадлежат A (%.1fpx от B) — позиционная слепота не исправлена"
    % _offset0)
assert t._anchor_confirms_position(
    _iac_ok0, _iac_score0, _iac_psr0,
    _iac_mx0, _iac_my0, A_cx, A_cy), (
    "контроль: та же пара координат ОБЯЗАНА подтверждать A (запрос ровно "
    "туда, где реально нашли) — иначе функция сломана в другую сторону")
print("    _anchor_confirms_position(..., query=B) -> False; "
      "(..., query=A) -> True — функция различает 'подтверждает B' от "
      "'нашла A рядом'")

print("\n=== 3. Полный pipeline: flow+matcher согласованно на B, A видна "
      "рядом — IDENTITY_ANCHOR_CHECK НЕ подтверждает B, streak растёт, "
      "заканчивается IDENTITY_UNCERTAIN ===")
t.flow_predict = fake_flow_to_b
t.template_match_locked = fake_match_to_b
_events = []
t.flight_log.event = _events.append
_streak_seen = []
_states = []
for i in range(8):
    _clk.tick(0.5)   # см. IDENTITY_ANCHOR_CHECK_PERIOD_S
    t.process_locked_tracker(trap)
    _streak_seen.append(t._match_dbg.get("identity_anchor_check_streak"))
    _states.append(t.track_state)
    if t.track_state != t.TRACK_STATE_TRACKED:
        break
print("    track_state по кадрам:", _states)
print("    identity_anchor_check_streak по кадрам:", _streak_seen)
assert _streak_seen == sorted(_streak_seen) and _streak_seen[-1] >= 1, (
    "streak не рос монотонно к срабатыванию: %s" % _streak_seen)
assert _states[-1] == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "track_state=%r — A видна рядом с B, flow/matcher согласны на B, но "
    "anchor-check обязан был НЕ подтвердить B (там нашлась бы A) и в "
    "итоге увести в IDENTITY_UNCERTAIN" % _states[-1])
with t.state_lock:
    _controllable = t.target_controllable
assert not _controllable, "controllable остался True"
assert t._identity_anchor_gray is _anchor_ref
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
_anchor_confirm_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
assert not _anchor_confirm_events, (
    "'IDENTITY ANCHOR: подтверждена' произошло, хотя ничего подтверждаться "
    "не должно было: %s" % _anchor_confirm_events)
print("    IDENTITY_UNCERTAIN достигнут, controllable=False, anchor не "
      "тронут — 'anchor нашёлся у A' корректно НЕ засчитан как "
      "'B подтверждён'")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: anchor-check различает 'anchor подтверждает ИМЕННО proposed "
      "позицию' от 'anchor где-то нашёлся в search window' — confirmed A, "
      "остающаяся видимой рядом с местом B, куда drift увёл flow+matcher, "
      "больше не может быть ошибочно засчитана как подтверждение B.")
