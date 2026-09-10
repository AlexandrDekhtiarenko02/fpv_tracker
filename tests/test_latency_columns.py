"""Возраст пакетов гироскопа и RC обязан быть в логе.

Зачем. В ACRO стик задаёт СКОРОСТЬ вращения, поэтому задержку «стик ->
реакция аппарата» надо мерить по гироскопу, а не по отфильтрованному углу.
Строки лога пишутся по кадрам, а пакеты MSP приходят своим темпом — без
возраста пакета эти два ряда не совместить по времени, и задержка,
центральная для всего контура, останется неизмеримой.

Проверяем и то, что возраст не подделан нулём, когда пакета ещё не было:
ноль означал бы «пакет только что пришёл», то есть ровно обратное правде.
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
cols = t._FLIGHT_LOG_COLUMNS.split(",")

print("=== 1. Колонки есть ===")
for imya in ("gyro_age_ms", "rc_age_ms", "att_age_ms", "alt_age_ms"):
    assert imya in cols, (
        "нет колонки %s — задержку «стик -> вращение» будет нечем считать"
        % imya)
    print("    %-12s позиция %d" % (imya, cols.index(imya)))

print("\n=== 2. Возраст считается от метки времени пакета ===")
t.flight_log.enabled = True
t.flight_log.event = lambda *a, **k: None
sobrano = {}
t.flight_log.row = lambda vals: sobrano.update(
    {"vals": list(vals)})
now = time.monotonic()
with t.state_lock:
    t.app_state["imu_ts"] = now - 0.040        # пакет пришёл 40 мс назад
    t.app_state["rc_link_ts"] = now - 0.120    # а этот 120 мс назад
    t.app_state["gyro"] = (10, -20, 30)
    t.app_state["acc"] = (0, 0, 4096)
t._capture_flight_row(time.monotonic())
assert sobrano, "строка не собралась"
v = sobrano["vals"]
gv = v[cols.index("gyro_age_ms")]
rv = v[cols.index("rc_age_ms")]
print("    gyro_age_ms = %.0f мс (ожидали ~40)" % gv)
print("    rc_age_ms   = %.0f мс (ожидали ~120)" % rv)
assert 30.0 < gv < 90.0, "возраст гироскопа посчитан неверно: %.1f" % gv
assert 110.0 < rv < 180.0, "возраст RC посчитан неверно: %.1f" % rv
assert rv > gv, "возрасты перепутаны местами"

print("\n=== 3. Пакета не было — не ноль, а пусто ===")
with t.state_lock:
    t.app_state.pop("imu_ts", None)
    t.app_state.pop("rc_link_ts", None)
sobrano.clear()
t._capture_flight_row(time.monotonic())
v = sobrano["vals"]
for imya in ("gyro_age_ms", "rc_age_ms"):
    z = v[cols.index(imya)]
    print("    %-12s -> %r" % (imya, z))
    assert z is None, (
        "%s = %r вместо пусто: ноль читается как «пакет только что пришёл», "
        "то есть ровно наоборот" % (imya, z))

print("\nOK: задержку «стик -> вращение» теперь есть чем измерить")
