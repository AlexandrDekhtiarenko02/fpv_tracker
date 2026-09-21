"""После ручной коррекции рамка не должна возвращаться к месту
первоначального захвата (найдено на стенде на 3ff7a5e).

ПРИЧИНА БАГА. reanchor_tracker_at_current_box() при отпускании стика
переснимал только flow-состояние (prev_pts/prev_gray) и derivative-
историю (geometry_epoch), но оставлял template_gray/template_base как
были — снятыми при ПЕРВОНАЧАЛЬНОМ захвате. Пока новая (исправленная
оператором) позиция остаётся в пределах search margin от старой,
matchTemplate находит на СТАРОМ месте пиксель-в-пиксель совпадение (сцена
не менялась) и своим весом (w_m) медленно тянет box обратно — рамка
"устаёт" от коррекции и сползает туда, откуда взлетела.

Сценарий: сцена с ДВУМЯ различимыми объектами A (место первоначального
захвата) и B (куда оператор поправляет рамку), оба одновременно видны и
оба в пределах досягаемости друг друга через search margin — иначе старый
анкор просто выпал бы из окна поиска, и баг не воспроизвёлся бы вовсе.
Захват на A -> nudge до B -> отпустить -> несколько кадров БЕЗ изменений
в сцене (A всё ещё там же, откуда его тянуло раньше) -> box обязан
остаться у B, а не сползти обратно к A.
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
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic

CY = t.LORES_H // 2
# A — РОВНО под штатным прицелом ACQ (CENTER_X_LORES), чтобы захват был
# детерминирован через обычный путь «под прицелом есть за что цепляться»,
# без трюков с подменой CENTER_X/CENTER_Y (эти константы используются по
# всему файлу для расчёта прицельной точки, трогать их в тесте — сломать
# заодно что-то ещё в этом же процессе).
AX = t.CENTER_X_LORES
BX = AX + 32   # 32 px — в пределах search margin, но с зазором от A
OBJ = 16


def make_scene():
    """Два РАЗЛИЧИМЫХ объекта одновременно в кадре: A — то, что было под
    прицелом при захвате, B — куда оператор поправляет рамку. Оба остаются
    на месте до конца теста (статичный стенд, ничего в сцене не движется)
    — единственная переменная тут действия трекера, не сцена."""
    frame = np.full((t.LORES_H, t.LORES_W), 110, np.uint8)

    def stamp(cx, seed_pattern, marker):
        r = np.random.default_rng(seed_pattern)
        s = OBJ
        patch = (r.random((s, s)) * 90 + 110).astype(np.uint8)
        if marker == "circle":
            cv2.circle(patch, (s // 2, s // 2), s // 3, 30, -1)
        else:
            cv2.line(patch, (0, s - 1), (s - 1, 0), 30, max(1, s // 8))
        x0, y0 = cx - s // 2, CY - s // 2
        frame[y0:y0 + s, x0:x0 + s] = patch

    stamp(AX, 11, "circle")   # A — под прицелом при захвате
    stamp(BX, 22, "line")     # B — куда правит оператор, ДРУГОЙ узор
    return frame


scene = make_scene()


def set_stick(roll_us, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


FRAME_DT = 1.0 / t.CAM_FPS

# Захват РОВНО на A.
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
set_stick(0)
t.target_box_main = None
_clk.tick(0.001)
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"
assert abs(t.lock_cx - AX) < 5.0, (
    "захват сел не на A (lock_cx=%.1f, ожидали ~%.1f) — тест сам по себе "
    "негоден" % (t.lock_cx, AX))
template_at_capture = t.template_gray.copy()
print("захват на A: lock_cx=%.2f" % t.lock_cx)

# Стабилизируем пару кадров без коррекции — реалистичный контекст перед
# тем, как оператор решит поправить рамку.
for _ in range(3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)

# NUDGE от A к B: полное отклонение стика, пока рамка не дойдёт до B.
print("\n=== Nudge от A (%.0f) к B (%.0f) ===" % (AX, BX))
ROLL_US = 300.0
set_stick(ROLL_US)
guard = 0
while t.lock_cx < BX and guard < 200:
    set_stick(ROLL_US)
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    guard += 1
assert guard < 200, "nudge не дошёл до B за разумное число кадров"
assert t._match_dbg.get("manual_nudge") == 1, "nudge не был активен по пути"
print("    дошли до lock_cx=%.2f за %d кадров" % (t.lock_cx, guard))

# Отпускаем ровно у B.
set_stick(0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
cx_at_release = t.lock_cx
print("    отпустили у lock_cx=%.2f (B=%.0f)" % (cx_at_release, BX))
assert abs(cx_at_release - BX) < 10.0, (
    "тест сам по себе негоден: отпустили не рядом с B")

# ГЛАВНАЯ ПРОВЕРКА. Эталон обязан быть пересобран под B, а не остаться
# снимком A — иначе следующая проверка (снапбэк) будет неотличима от
# случайности.
assert not np.array_equal(t.template_gray, template_at_capture), (
    "template_gray после re-anchor побитово совпадает с эталоном, снятым "
    "при захвате на A — эталон не пересобран под новую позицию")
assert t.template_gray.shape == template_at_capture.shape, (
    "форма эталона неожиданно изменилась при пересборке — lock_w/h не "
    "должны были трогаться nudge'ем")

# НЕСКОЛЬКО КАДРОВ БЕЗ ИЗМЕНЕНИЙ В СЦЕНЕ. A всё ещё на месте и всё ещё
# отличим — именно это раньше утягивало box обратно.
print("\n=== После release: box не должен сползать обратно к A ===")
positions = [cx_at_release]
for i in range(15):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    positions.append(t.lock_cx)
    print("    кадр %2d: lock_cx=%.2f (B=%.0f, A=%.0f)"
          % (i, t.lock_cx, BX, AX))

max_drift_from_B = max(abs(p - BX) for p in positions)
final_pos = positions[-1]
print("    максимальное отклонение от B за 15 кадров: %.2f px"
      % max_drift_from_B)
print("    финальная позиция: %.2f (расстояние до A: %.2f, до B: %.2f)"
      % (final_pos, abs(final_pos - AX), abs(final_pos - BX)))

assert max_drift_from_B < 8.0, (
    "box отклонился от принятой оператором позиции B больше чем на "
    "%.1f px (макс. допуск 8.0) за 15 кадров без изменений в сцене — "
    "похоже на снапбэк к старому эталону" % max_drift_from_B)
assert abs(final_pos - AX) > abs(final_pos - BX), (
    "финальная позиция (%.2f) ближе к МЕСТУ ПЕРВОНАЧАЛЬНОГО ЗАХВАТА A "
    "(%.2f), чем к принятой оператором B (%.2f) — box вернулся к старому "
    "анкору" % (final_pos, AX, BX))

print("\nOK: после ручной коррекции и отпускания box остаётся у принятой "
      "оператором позиции, а не сползает обратно к месту первоначального "
      "захвата")
