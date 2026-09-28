"""Подтверждённая identity не заменяется соседним patch молча (отчёт
25.09, разбор поверх 64949ec, п.C/F/K-3 — прямая цитата пользователя:
"выбранная identity не должна автоматически заменяться соседним patch.
При потере уверенности — IDENTITY_UNCERTAIN, а не silent retarget").

СЦЕНАРИЙ, максимально приближенный к реальному "background trap" из отчёта
(flow_quality≈1.0 при серьёзном уходе фона): подтверждаем patch A обычным
путём, затем на месте A остаётся пустой фон, а РЯДОМ (в пределах окна
поиска) появляется patch B — буквально копия ЖИВОГО template_gray на
момент захвата, то есть заведомо самый выгодный для matchTemplate кандидат,
какой вообще можно сконструировать (score у него будет максимальным, какой
в принципе достижим). Если бы identity могла тихо "переехать" на более
выгодный сосед — это ЕДИНСТВЕННЫЙ сценарий, где она обязана была бы это
сделать. Она не делает.

flow_predict подменён детерминированной заглушкой (flow честно продолжает
считать, что цель на прежнем месте, куда его точки были посажены при
последнем подтверждённом кадре, — ровно то, что описывает отчёт: "поток
следит за реальным движением цели и на фон не смотрит"). template_match_
locked НЕ подменяется — весь эффект получен РЕАЛЬНЫМ matchTemplate поверх
реального (пусть и сконструированного) кадра.
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


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.flow_predict = _real_flow_predict
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def fake_flow_still_on_a(prev_g, cur_g, pts, cx, cy):
    """Поток честно продолжает считать, что цель на прежнем (переданном)
    месте — тех же координатах, где сидели его точки на последнем
    подтверждённом кадре. Реальный LK на сцене, где A заменена плоским
    фоном, скорее всего просто потерял бы эти точки (status=0) — заглушка
    убирает эту недетерминированность CV, не меняя сути: поток не смотрит
    на фон и не 'переезжает' на B сам."""
    return True, cx, cy


assert t.IDENTITY_UNCERTAIN_ENABLED, "тест сам по себе негоден без ENABLED"
N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

capture()
A_cx, A_cy = t.lock_cx, t.lock_cy
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_tmpl_at_capture = t.template_gray.copy()
th, tw = _tmpl_at_capture.shape[:2]
OFFSET = 10.0   # внутри окна поиска (SEARCH_MARGIN_MIN), заведомо > MATCH_GAP_SOFT


def trap_scene():
    """Плоский фон; на месте A — ничего; рядом, на расстоянии OFFSET —
    точная копия эталона, снятого при захвате (самый выгодный для
    matchTemplate кандидат, какой вообще можно сконструировать)."""
    g = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    bx = int(round(A_cx + OFFSET - tw / 2))
    by = int(round(A_cy - th / 2))
    g[by:by + th, bx:bx + tw] = _tmpl_at_capture
    return g


trap = trap_scene()

print("=== 1. Серия кадров с более выгодным соседом B: НЕ silent-TRACKED "
      "на B, а деградация в IDENTITY_UNCERTAIN ===")
t.flow_predict = fake_flow_still_on_a
_events = []
t.flight_log.event = _events.append
_states_seen = []
_gap_seen = []
for i in range(N):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(trap)
    _states_seen.append(t.track_state)
    _gap_seen.append(t._match_dbg.get("flow_gap"))
    assert t._identity_anchor_gray is _anchor_ref, (
        "_identity_anchor_gray стал другим объектом на кадре %d — тихо "
        "переехал на соседа" % i)
    assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
        "байты _identity_anchor_gray изменились на кадре %d" % i)
    assert t._match_dbg.get("identity_anchor_changed") == 0, (
        "identity_anchor_changed=1 на кадре %d — соседний patch тихо "
        "подтверждён как новая identity" % i)
print("    flow_gap по кадрам: %s" % ["%.2f" % g for g in _gap_seen])
print("    track_state по кадрам: %s" % _states_seen)
assert _states_seen[:-1] == [t.TRACK_STATE_TRACKED] * (N - 1), (
    "сработало раньше N=%d кадров: %s" % (N, _states_seen))
assert _states_seen[-1] == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "после %d кадров устойчивого расхождения с более выгодным соседом "
    "ожидали IDENTITY_UNCERTAIN, получили %r — значит патч B был тихо "
    "принят как продолжение TRACKED" % (N, _states_seen[-1]))
with t.state_lock:
    _controllable = t.target_controllable
assert not _controllable, "controllable остался True после срабатывания"
print("    N=%d кадров устойчивого расхождения с patch B -> "
      "IDENTITY_UNCERTAIN, controllable=False — НЕ silent-TRACKED на B" % N)

print("\n=== 2. Позиция НЕ доехала до B — частичный дрейф остановлен "
      "коммит-гейтом (п.E), а не 'мы уже там' ===")
dist_to_a = abs(t.lock_cx - A_cx)
dist_to_b = abs(t.lock_cx - (A_cx + OFFSET))
print("    lock_cx=%.2f: расстояние до A=%.2f, до B=%.2f"
      % (t.lock_cx, dist_to_a, dist_to_b))
assert dist_to_a < OFFSET * 0.6, (
    "lock_cx уехал больше чем на половину пути к B (%.2f из %.2f) — дрейф "
    "не был остановлен вовремя" % (dist_to_a, OFFSET))
assert dist_to_b > OFFSET * 0.4, (
    "lock_cx оказался ближе к B, чем к A — практически 'доехали' до "
    "соседа, хоть формально и не TRACKED")

print("\n=== 3. Ни одного события 'IDENTITY ANCHOR: подтверждена' после "
      "первичного захвата — единственное такое событие принадлежит ему "
      "===")
_anchor_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
assert not _anchor_events, (
    "событие 'IDENTITY ANCHOR: подтверждена' произошло во время эпизода с "
    "соседом B: %s" % _anchor_events)
print("    0 событий 'IDENTITY ANCHOR' за весь эпизод с B")

print("\n=== 4. Персистентность: не отходит само на дальнейших кадрах "
      "того же trap-сценария ===")
for _ in range(10):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(trap)
    assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
        "IDENTITY_UNCERTAIN отпустило само на кадре с тем же соседом B")
    assert t._identity_anchor_gray is _anchor_ref
print("    10 дополнительных кадров с B — IDENTITY_UNCERTAIN держится, "
      "anchor не тронут")

t.flow_predict = _real_flow_predict
t.reset_tracking(to_acq=False)

print("\nOK: устойчиво более выгодный сосед B рядом с подтверждённым A НЕ "
      "заменяет identity молча — система деградирует в IDENTITY_UNCERTAIN "
      "(controllable=False) РОВНО через дебаунс IDENTITY_UNCERTAIN_CONFIRM_"
      "FRAMES, позиция не доезжает до B, confirmed identity anchor не "
      "трогается ни разу, событие подтверждения identity не появляется.")
