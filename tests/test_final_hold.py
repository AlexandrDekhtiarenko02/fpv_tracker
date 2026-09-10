"""В финале команда замирает, а не доворачивает.

В последние мгновения доворачивать поздно и вредно. Поздно — аппарат
физически не успевает изменить траекторию: на 20 м/с последние пять метров
проходятся за четверть секунды, а отклик петли замерен в 125 мс. Вредно —
именно там всё сходится против нас: рамка огромная, кадр идёт вдвое дольше
(50 мс против 25), слежение по раздутому эталону шатается, и поправка вносит
больше шума, чем исправляет.

Признак близости — рост рамки ОТ ЗАХВАТА: прямое измерение, безразмерное, и
порог сам подстраивается под дистанцию захвата. Дальность через высоту врёт
на малых углах, время до контакта пока не работает вовсе.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y
MASHTAB = float(t.MAIN_W) / float(t.LORES_W)


def zahod(rost, dy_px):
    """Кадр захода: рамка выросла в rost раз, цель смещена на dy_px."""
    # Размер при захвате — в координатах lores, коробка — в координатах кадра.
    t.lock_w0 = t.lock_h0 = 20.0
    storona = 20.0 * MASHTAB * rost
    pol = storona / 2.0
    t.target_box_main = (int(CX - pol), int(CY - pol + dy_px),
                         int(CX + pol), int(CY + pol + dy_px))
    t.target_visible = True
    t.target_controllable = True
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 20.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["alt_cm"] = 3000
        t.app_state["alt_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    t.update_control_from_target()
    return t.global_pitch_cmd, t.global_roll_cmd


t._final_zamorozhen = False
print("=== 1. До порога контур рулит: команда следует за ошибкой ===")
komandy = []
for dy in (0, 40, -40):
    p, _r = zahod(rost=1.5, dy_px=dy)
    komandy.append(p)
    print("    рост 1.5, смещение %+3d px -> тангаж %d" % (dy, p))
assert len(set(komandy)) > 1, (
    "команда не меняется при разной ошибке — контур не рулит там, где должен")

print("\n=== 2. За порогом команда замирает ===")
t._final_zamorozhen = False
p0, r0 = zahod(rost=t.FINAL_HOLD_ROST + 0.5, dy_px=0)
print("    первый кадр финала -> тангаж %d, крен %d" % (p0, r0))
zamerlo = True
for dy in (60, -60, 100):
    p, r = zahod(rost=t.FINAL_HOLD_ROST + 0.5, dy_px=dy)
    print("    смещение %+4d px -> тангаж %d, крен %d" % (dy, p, r))
    if (p, r) != (p0, r0):
        zamerlo = False
assert zamerlo, (
    "команда в финале продолжает меняться вслед за ошибкой — заморозки нет")

print("\n=== 3. Порог считается от РАЗМЕРА ПРИ ЗАХВАТЕ ===")
# Тот же абсолютный размер рамки, но захват был с разной дистанции.
t._final_zamorozhen = False
t.lock_w0 = t.lock_h0 = 20.0
zahod(rost=2.0, dy_px=0)
blizko = t._final_zamorozhen
t._final_zamorozhen = False
t.lock_w0 = t.lock_h0 = 10.0          # захват был вдвое дальше
_p, _r = zahod(rost=1.0, dy_px=0)     # рамка та же по величине
# rost=1.0 при lock_w0=10 даёт коробку 10*масштаб, то есть рост 1.0 — не финал.
print("    рост 2.0 -> финал %s;  рост 1.0 -> финал %s"
      % (blizko, t._final_zamorozhen))
assert not blizko, "рост 2.0 ниже порога %.1f, а финал включился" % t.FINAL_HOLD_ROST
assert not t._final_zamorozhen, "рост 1.0 не может быть финалом"

print("\n=== 4. Выключается штатно ===")
t.FINAL_HOLD_ENABLED = False
t._final_zamorozhen = False
t.lock_w0 = t.lock_h0 = 20.0
a, _ = zahod(rost=t.FINAL_HOLD_ROST + 2.0, dy_px=0)
b, _ = zahod(rost=t.FINAL_HOLD_ROST + 2.0, dy_px=80)
t.FINAL_HOLD_ENABLED = True
print("    при выключенной заморозке команда снова меняется: %d -> %d" % (a, b))
assert a != b, "выключатель не работает"

print("\nOK: до порога рулим, за порогом держим последнюю команду")
