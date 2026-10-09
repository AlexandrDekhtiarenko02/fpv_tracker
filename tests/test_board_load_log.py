"""Загрузка платы пишется в тот же CSV, раз в секунду.

Проверяет арифметику процентов и что чтение /proc не падает.
На машине без прошивки Pi throttle_flags остаётся пустым — это норма.
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

for col in ("cpu_util_pct", "cpu0_pct", "cpu1_pct", "cpu2_pct", "cpu3_pct",
            "throttle_flags", "mem_avail_kb", "load_1m"):
    assert col in t._FLIGHT_LOG_COLUMNS, "нет колонки %s" % col

assert t._cpu_pct((0, 100), (25, 200)) == 75.0
assert t._cpu_pct((50, 100), (150, 200)) == 0.0
assert t._cpu_pct(None, (0, 1)) is None
assert t._cpu_idle_total([1, 2, 3, 10, 5]) == (15, 21)

t._sample_board_load()
time.sleep(0.05)
t._sample_board_load()
assert t._cpu_util_pct is not None and 0.0 <= t._cpu_util_pct <= 100.0, (
    t._cpu_util_pct)
assert t._mem_avail_kb is None or t._mem_avail_kb > 0
assert t._load_1m is None or t._load_1m >= 0.0
print("OK: колонки на месте, проценты считаются, /proc читается "
      "(util=%s mem=%s load=%s throttle=%s)"
      % (t._cpu_util_pct, t._mem_avail_kb, t._load_1m, t._throttle_flags))
