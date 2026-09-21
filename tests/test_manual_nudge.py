"""Ручная коррекция рамки правым стиком (ТЗ next-commit spec §11/§12,
исправления по code review 15ce5bf).

ИСТОЧНИК СИГНАЛА — AUX2/AUX3 (rc_channels[5]/[6]), живые копии правого
стика, заведённые на передатчике в обход маски MSP-оверрайда. Roll/Pitch
(ch[0]/ch[1]) НЕ используются: во время TRACKED+override MSP_RC отдаёт по
ним уже подменённое значение, а не живой стик — это и было причиной
исходного бага «рамка не двигается от стика» (see: пояснение у
MANUAL_NUDGE_ROLL_AUX_IDX в tracker.py).

Проверяется:
  A/B/C/D — исходная механика: мёртвая зона, движение по формуле стика
      (а не от flow/match), re-anchor при отпускании, снятие заморозки.
  E — HOLD не оживает в TRACKED от одного лишь стика (review п.1).
  F — состояние коррекции не переживает потерю/новый лок (review п.2).
  G — шаг nudge не зависит от фактического FPS (review п.5).
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

AUX2_IDX = t.MANUAL_NUDGE_ROLL_AUX_IDX
AUX3_IDX = t.MANUAL_NUDGE_PITCH_AUX_IDX


class _Chasy:
    """Управляемые часы — nudge теперь считает шаг по РЕАЛЬНОМУ времени
    между кадрами (review п.5), и без контроля над часами тест был бы
    зависим от скорости машины, на которой его запускают."""

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


def make_blank():
    """Совсем без текстуры — flow/match НЕ МОГУТ переподтвердить цель, а
    только на этом и можно честно проверить, что HOLD держится сам, без
    помощи (и без вмешательства) от nudge."""
    return np.full((t.LORES_H, t.LORES_W), 120, np.uint8)


scene = make_scene()
blank = make_blank()


def set_stick(roll_us, pitch_us=0.0):
    """AUX2/AUX3 — НЕ ch[0]/ch[1]. rc_channels (не receiver_channels):
    это сырой, всегда живой массив (см. пояснение у констант в
    tracker.py) — receiver_channels целиком замирает при оверрайде,
    включая AUX-элементы, если брать их оттуда по ошибке ещё раз."""
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick(0)
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


capture()
cx0 = t.lock_cx
epoch0 = t.geometry_epoch
print("захват: lock_cx=%.2f geometry_epoch=%d" % (cx0, epoch0))

FRAME_DT = 1.0 / t.CAM_FPS

print("\n=== A. Стик в мёртвой зоне — nudge неактивен ===")
set_stick(0)
before = t.lock_cx
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0, (
    "nudge посчитан активным при стике в центре")
print("    manual_nudge=%s, lock_cx %.2f -> %.2f, aux2_raw=%s aux3_raw=%s "
      "nudge_rc_fresh=%s"
      % (t._match_dbg.get("manual_nudge"), before, t.lock_cx,
         t._match_dbg.get("aux2_raw"), t._match_dbg.get("aux3_raw"),
         t._match_dbg.get("nudge_rc_fresh")))
assert t._match_dbg.get("nudge_rc_fresh") == 1, (
    "AUX-копии не считаются свежими сразу после записи rc_link_ts — "
    "диагностика свежести сломана")

print("\n=== B. Стик отклонён вправо (AUX2) — рамка едет по формуле, а не "
      "от flow/match ===")
ROLL_US = 300.0  # заметно за пределами MANUAL_NUDGE_DEADBAND_US=60
set_stick(ROLL_US)
half = 500.0 - t.MANUAL_NUDGE_DEADBAND_US
norm = (ROLL_US - t.MANUAL_NUDGE_DEADBAND_US) / half
expected_step = t.MANUAL_NUDGE_ROLL_SIGN * norm * t.MANUAL_NUDGE_MAX_PX_S * FRAME_DT
print("    ожидаемый шаг за кадр (dt=%.4f): %.4f px" % (FRAME_DT, expected_step))

positions = [t.lock_cx]
N = 5
for _ in range(N):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    assert t._match_dbg.get("manual_nudge") == 1, "nudge не распознан активным"
    assert t.overlay_text == "NUDGE", (
        "оверлей не показывает NUDGE во время коррекции: %r" % t.overlay_text)
    positions.append(t.lock_cx)

steps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
print("    шаги lock_cx:", ["%.4f" % s for s in steps])
# Первый шаг активации — по номинальному dt (_nudge_prev_t ещё не было),
# остальные — по РЕАЛЬНОМУ интервалу между кадрами (здесь он равен
# номинальному, часы тикают ровно FRAME_DT).
for s in steps:
    assert abs(s - expected_step) < 1e-6, (
        "шаг рамки (%.4f) не совпадает с формулой стика (%.4f) — либо "
        "nudge считает неверно, либо flow/match всё-таки вмешались"
        % (s, expected_step))
assert t.geometry_epoch == epoch0, (
    "geometry_epoch изменился ВО ВРЕМЯ коррекции — re-anchor должен "
    "случиться только на ОТПУСКАНИИ стика")

print("\n=== C. Отпускание стика — re-anchor: новая эпоха, заморозка "
      "адаптации, сброс истории ===")
cx_before_release = t.lock_cx
set_stick(0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
print("    geometry_epoch: %d -> %d" % (epoch0, t.geometry_epoch))
assert t.geometry_epoch == epoch0 + 1, (
    "отпускание стика не подняло geometry_epoch — re-anchor не сработал")
assert t._adapt_frozen_posle_reanchor, (
    "адаптация шаблона не заморожена сразу после re-anchor — позиция ещё "
    "не подтверждена свежим измерением")
assert abs(t.lock_cx - cx_before_release) < 1.0, (
    "re-anchor сам по себе не должен телепортировать рамку — она уже там, "
    "где её оставила ручная коррекция")

print("\n=== D. Один кадр со свежим потоком — заморозка снимается ===")
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert not t._adapt_frozen_posle_reanchor, (
    "заморозка адаптации не снялась после кадра со свежим потоком — "
    "шаблон никогда не сможет доучиться после re-anchor")


print("\n=== E. Стик отклонён, а трекер в HOLD — nudge НЕ должен оживлять "
      "TRACKED (review п.1) ===")
# ЧЕСТНЫЙ HOLD: кормим пустую сцену без единой текстурной точки, поэтому
# flow/match легитимно проваливаются САМИ — HOLD держится по-настоящему, а
# не только потому, что я поставил флаг руками. Если бы сцена осталась
# прежней (с объектом), flow/match тривиально переподтвердили бы цель на
# этом же кадре, и тест проверял бы не то — совпадение с легитимным
# восстановлением, а не вмешательство именно nudge.
with t.state_lock:
    t.track_state = t.TRACK_STATE_HOLD
    t.target_controllable = False
t.lost_frames = 1  # внутри HOLD_FRAMES=10 — состояние останется HOLD, не LOST
# УБИВАЕМ flow ДЕТЕРМИНИРОВАННО: без точек flow_predict возвращает False
# собственным ранним выходом, независимо от того, как cv2 поведёт себя на
# вырожденном (без текстуры) кадре. Иначе LK способен дать status=1 с
# нулевым смещением даже на однородном изображении, и HOLD "восстановился"
# бы легитимно — тест проверял бы совпадение, а не вмешательство nudge.
t.prev_pts = None
cx_before_hold_test = t.lock_cx
set_stick(ROLL_US, pitch_us=ROLL_US)
epoch_after_transition = None
for i in range(3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(blank)
    print("    кадр %d: track_state=%s target_controllable=%s manual_nudge=%s "
          "geometry_epoch=%d"
          % (i, t.track_state, t.target_controllable,
             t._match_dbg.get("manual_nudge"), t.geometry_epoch))
    assert t.track_state in (t.TRACK_STATE_HOLD, t.TRACK_STATE_LOST), (
        "track_state=%r — ожидался HOLD или (после исчерпания HOLD_FRAMES) "
        "LOST, но никак не TRACKED" % t.track_state)
    assert t.track_state != t.TRACK_STATE_TRACKED, (
        "nudge поднял HOLD/LOST в TRACKED без единого подтверждения "
        "flow/match — именно регрессия, найденная в review")
    with t.state_lock:
        _controllable_now = t.target_controllable
    assert not _controllable_now, (
        "target_controllable стал True из одного лишь отклонения стика")
    assert t._match_dbg.get("manual_nudge") == 0, (
        "manual_nudge=1 вне TRACKED — гейт по track_state не сработал")
    if i == 0:
        # Кадр 0 — это ПЕРЕХОД TRACKED -> HOLD, тот самый геометрический
        # разрыв (fix #3), и он ОБЯЗАН поднять эпоху сам по себе, стик тут
        # ни при чём: тот же переход поднял бы эпоху и при стике на нуле.
        epoch_after_transition = t.geometry_epoch
    else:
        # А ДАЛЬШЕ, пока HOLD длится и стик всё ещё отклонён, эпоха обязана
        # СТОЯТЬ — вот это и есть проверка "нужно от review": стик сам по
        # себе, без подтверждённого TRACKED, ничего больше не запускает.
        assert t.geometry_epoch == epoch_after_transition, (
            "geometry_epoch продолжила расти внутри HOLD (%d -> %d) при "
            "отклонённом стике — coррекция как будто проникает мимо гейта"
            % (epoch_after_transition, t.geometry_epoch))
assert t.lock_cx == cx_before_hold_test, (
    "lock_cx сдвинулся от стика в HOLD — nudge не должен был применить "
    "смещение вне TRACKED")
set_stick(0)  # вернуть в нейтраль для следующих сценариев


print("\n=== F. Состояние коррекции не переживает потерю/новый лок "
      "(review п.2) ===")
# Возвращаемся в нормальный TRACKED и начинаем nudge, НЕ отпуская стик.
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
set_stick(ROLL_US)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 1, (
    "тест сам по себе негоден: nudge не активировался перед потерей лока")
assert t._nudge_was_active, "тест сам по себе негоден: флаг не выставлен"

# Полный сброс лока ПРЯМО ВО ВРЕМЯ коррекции — имитация AUX off/потери.
t.reset_tracking(to_acq=False)
assert not t._nudge_was_active, (
    "_nudge_was_active пережил reset_tracking — следующий захват увидит "
    "стик как «только что отпущенный» и выполнит ложный re-anchor")
assert t._nudge_prev_t is None, (
    "_nudge_prev_t пережил reset_tracking — часы nudge не обнулены")
assert not t._adapt_frozen_posle_reanchor, (
    "_adapt_frozen_posle_reanchor пережил reset_tracking — заморозка "
    "адаптации от прошлого захода перетекла в новый")

# Новый захват — стик всё ещё отклонён (пилот не отпускал). Он не должен
# восприниматься как "только что отпущенный" (что вызвало бы фантомный
# REANCHOR/эпоху уже на новой цели).
epoch_before_new_capture = t.geometry_epoch
capture()
assert t.geometry_epoch == epoch_before_new_capture, (
    "новый захват при всё ещё отклонённом стике поднял geometry_epoch — "
    "фантомный re-anchor от состояния прошлого захода")
print("    reset_tracking очищает _nudge_was_active/_nudge_prev_t/"
      "_adapt_frozen_posle_reanchor; новый захват не наследует состояние "
      "прошлого")


print("\n=== G. Шаг nudge не зависит от фактического FPS (review п.5) ===")
# Один и тот же РЕАЛЬНЫЙ отрезок времени (1 секунда) отклонённого стика —
# при 24 к/с (нормальный FPS) и при 12 к/с (просевший, крупная цель) —
# должен дать ПОХОЖЕЕ суммарное смещение, а не вдвое разное.
epoch_before_g = t.geometry_epoch


def run_nudge_for(real_seconds, fps):
    dt = 1.0 / fps
    n = int(round(real_seconds / dt))
    cx_start = t.lock_cx
    for _ in range(n):
        # Стик переустанавливается КАЖДЫЙ кадр — как в реальности, где
        # fc_io_loop опрашивает MSP_RC непрерывно, и rc_link_ts всегда
        # свежий. Один вызов set_stick() перед циклом состарился бы за
        # MANUAL_NUDGE_RC_FRESH_S=0.35с, и на низком FPS большая часть
        # кадров молча теряла бы nudge как «несвежий RC» — это тестовый
        # артефакт, а не поведение трекера на живом приёмнике.
        set_stick(ROLL_US)
        _clk.tick(dt)
        t.process_locked_tracker(scene)
    moved = t.lock_cx - cx_start
    set_stick(0)
    _clk.tick(dt)
    t.process_locked_tracker(scene)  # отпустить, re-anchor, закрыть эпизод
    return moved


with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
moved_24 = run_nudge_for(1.0, 24.0)
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
moved_12 = run_nudge_for(1.0, 12.0)

print("    смещение за 1 реальную секунду: 24 fps -> %.2f px, 12 fps -> "
      "%.2f px" % (moved_24, moved_12))
assert moved_24 > 0 and moved_12 > 0, (
    "тест сам по себе негоден: смещения должны быть положительны")
rel_diff = abs(moved_24 - moved_12) / max(moved_24, moved_12)
assert rel_diff < 0.15, (
    "смещение за одинаковое реальное время различается на %.0f%% между "
    "24 и 12 fps (%.2f px против %.2f px) — шаг nudge зависит от "
    "фактического FPS, а не должен" % (rel_diff * 100, moved_24, moved_12))


print("\nOK: ручная коррекция двигает рамку по формуле AUX2/AUX3, не "
      "оживляет HOLD, не переживает потерю лока, не зависит от FPS, "
      "отпускание вызывает re-anchor с новой эпохой и заморозкой адаптации")
