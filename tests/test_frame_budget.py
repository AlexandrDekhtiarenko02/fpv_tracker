"""Примерка масштаба уступает бюджету кадра (ТЗ next-commit spec §4).

Ядро слежения (поток + матч) — приоритет, оно уже отработало к моменту
проверки. Вторичный дорогой этап (measure_scale_change / примерка масштаба)
не обязан выполняться на КАЖДОМ номинально запланированном кадре, если
callback уже потратил большую часть бюджета: иначе именно там, где кадр и
без того дорогой (крупный эталон, тепловой throttling), мы добавляли бы ещё
расход сверху, усиливая просадку, а не сглаживая её.

Пропуск по бюджету обязан отличаться от пропуска «мерили — не получилось»
(size_skip=1..7): иначе замедление борта на разборе неотличимо от отказа
самого алгоритма.
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()
t.MOTION_GUARD_ENABLED = False
t.color_active = False


def make_scene(size=40, seed=7):
    rng = np.random.default_rng(seed)
    frame = (rng.random((t.LORES_H, t.LORES_W)) * 80 + 60).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = size
    obj = np.zeros((s, s), np.uint8)
    cv2.rectangle(obj, (0, 0), (s - 1, s - 1), 210, -1)
    cv2.circle(obj, (s // 3, s // 3), max(2, s // 6), 40, -1)
    cv2.line(obj, (0, s - 1), (s - 1, 0), 20, max(1, s // 14))
    x0, y0 = cx - s // 2, cy - s // 2
    frame[y0:y0 + s, x0:x0 + s] = obj
    return frame


scene = make_scene()
period = None

t.reset_tracking(to_acq=True)
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся — тест сам негоден"
period = t._period_primerki()
print("период примерки: %d кадров" % period)

# Прогреваем до кадра ПЕРЕД плановой примеркой. Неподвижная цель, cb_t0=None
# (как offline/tools — не режется бюджетом вовсе, см. docstring функции).
while t.frame_index % period != period - 1:
    t.process_locked_tracker(scene, None)
assert t.frame_index % period == period - 1

print("\n=== Бюджет исчерпан: пропуск с кодом 8, а не 1..7 ===")
# Следующий кадр — плановый (frame_index % period == 0). cb_t0 далеко в
# прошлом: бюджет кадра исчерпан задолго до этой проверки.
stale_t0 = time.monotonic() - 5.0
t.process_locked_tracker(scene, stale_t0)
assert t.frame_index % period == 0, "расчёт периода в тесте сбился"
print("    size_skip=%s  expensive_stage_skipped=%s  skip_reason=%r"
      % (t._match_dbg.get("size_skip"), t._match_dbg.get("expensive_stage_skipped"),
         t._match_dbg.get("skip_reason")))
assert t._match_dbg.get("size_skip") == 8, (
    "плановая примерка при исчерпанном бюджете должна получить код 8, "
    "получено %r — либо бюджет не сработал, либо перепутан с обычным отказом"
    % t._match_dbg.get("size_skip"))
assert t._match_dbg.get("expensive_stage_skipped") == 1, (
    "expensive_stage_skipped должен стоять при пропуске по бюджету")
assert t._match_dbg.get("skip_reason") == "budget", (
    "skip_reason должен называть причину пропуска явно")

print("\n=== Бюджет свободен: примерка выполняется, а не застревает на 8 ===")
# Доходим до следующей плановой отметки со свежим cb_t0 — бюджет в норме.
while t.frame_index % period != period - 1:
    t.process_locked_tracker(scene, None)
fresh_t0 = time.monotonic()
t.process_locked_tracker(scene, fresh_t0)
print("    size_skip=%s  expensive_stage_skipped=%s"
      % (t._match_dbg.get("size_skip"), t._match_dbg.get("expensive_stage_skipped")))
assert t._match_dbg.get("size_skip") != 8, (
    "при свежем cb_t0 бюджет не исчерпан, а код всё равно 8 — застряло "
    "прошлое значение или бюджет считается неверно")
assert t._match_dbg.get("expensive_stage_skipped") == 0, (
    "expensive_stage_skipped должен снова стать 0, когда бюджет есть")

print("\n=== cb_t0=None (offline/tools) никогда не режет по бюджету ===")
# Возврат к прогреву без cb_t0 — проверяем, что НИ ОДИН кадр за целый период
# не получил код 8: offline-прогон не должен зависеть от реального времени.
saw_budget_skip = False
for _ in range(period + 1):
    t.process_locked_tracker(scene, None)
    if t._match_dbg.get("size_skip") == 8:
        saw_budget_skip = True
assert not saw_budget_skip, (
    "cb_t0=None не должен приводить к пропуску по бюджету — иначе offline-"
    "прогоны (test_*.py, tools/profile_pi.py) стали бы зависеть от скорости "
    "машины, на которой их запускают")

print("\nOK: примерка масштаба уступает бюджету кадра, код пропуска отличим "
      "от обычного отказа, offline-прогоны бюджетом не ограничены")
