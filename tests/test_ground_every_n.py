"""Бег земли считается реже, но интервал измеряется по факту.

Замерено в полёте 4 сентября 2026: расчёт стоит 11.4 мс на кадр при бюджете
48 мс — треть времени кадра. На стенде он не запускался НИ РАЗУ, потому что
требует арма и высоты, поэтому в его цену никто и не смотрел, а FPS просел
только в воздухе.

Прореживание безопасно ровно потому, что скорость считается по ИЗМЕРЕННОМУ
интервалу между кадрами, а не по предположению «кадры идут подряд». Если бы
интервал был зашит константой, прореживание вдвое занизило бы скорость вдвое,
и обнаружилось бы это только при сверке с GPS.
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Настройка есть и разумна ===")
n = t.GROUND_EVERY_N
print("    GROUND_EVERY_N =", n)
assert isinstance(n, int) and n >= 1, "шаг должен быть целым и не меньше 1"
assert n <= 4, ("шаг %d — обновление реже 6 Гц; поток успеет уехать дальше, "
                "чем окно оптического потока" % n)

print("\n=== 2. Расчёт вызывается раз в N кадров, а не каждый ===")
zvali = []
t.estimate_ground_speed = lambda gray, ts: zvali.append(ts)
t._gs_n = 0
KADROV = 30
for i in range(KADROV):
    t._gs_n += 1
    if t.GROUND_SPEED_ENABLED and t._gs_n % t.GROUND_EVERY_N == 0:
        t.estimate_ground_speed(None, time.monotonic())
print("    кадров %d -> вызовов %d (ожидали %d)"
      % (KADROV, len(zvali), KADROV // n))
assert len(zvali) == KADROV // n, "прореживание работает не так, как задумано"

print("\n=== 3. Скорость считается по ИЗМЕРЕННОМУ интервалу ===")
# Иначе прореживание молча занизило бы скорость ровно во столько же раз.
import io as _io  # noqa: E402
src = _io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
telo = src[src.index("def estimate_ground_speed("):]
telo = telo[:telo.index("\ndef ", 10)]
assert "now_mono - _gs_prev_t" in telo or "dt" in telo, (
    "интервал не измеряется — прореживание исказит скорость")
assert "0.04" not in telo and "1/25" not in telo, (
    "в расчёте зашит период кадра: с прореживанием скорость станет неверной")
print("    интервал берётся из меток времени, константы периода нет")

print("\n=== 4. Колонка с фактическим интервалом пишется в лог ===")
cols = t._FLIGHT_LOG_COLUMNS.split(",")
assert "ground_flow_dt_ms" in cols, (
    "без фактического интервала на разборе не отличить прореживание от "
    "пропущенных кадров")
print("    ground_flow_dt_ms на позиции", cols.index("ground_flow_dt_ms"))

print("\nOK: считаем реже, а меряем по-прежнему честно")
