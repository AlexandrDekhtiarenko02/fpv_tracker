"""Короткое время без роста рамки по концам окна не публикуется.

Горб шума посередине даёт крутой наклон при тех же концах. Раньше это
становилось tau около секунды, и заморозка садилась далеко от цели.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()


def sbros():
    t._size_hist.clear()
    t._rassh_nakop = 0.0
    t._flow_rasshirenie = None
    t._flow_rasshirenie_t = 0.0
    t._growth_raw.clear()
    t.prev_box_size_px = None
    t.box_growth_smoothed = 0.0


def kadr(size, now):
    return t._estimate_closure(size, size, t.CENTER_Y, now, 1.0)


print("=== 1. Честное сближение время публикует ===")
sbros()
tau0 = 8.0
fps = 24.0
dt = 1.0 / fps
t0 = 5000.0
poslednee = None
for i in range(int(fps * 3.0)):
    tek = t0 + i * dt
    ist = tau0 - i * dt
    size = 20.0 * tau0 / ist
    poslednee = kadr(size, tek)
tau = poslednee["tau_s"]
ist_kon = tau0 - 3.0 + dt
print("    tau = %s, истина %.2f" % (tau, ist_kon))
assert tau is not None, "честный рост рамки не дал времени"
assert abs(tau - ist_kon) < 0.6, "tau %.2f далеко от истины %.2f" % (tau, ist_kon)

print("\n=== 2. Подъём в конце окна и возврат — времени нет ===")
sbros()
# Рамка почти та же в начале и в конце, но ближе к концу окна она
# постояла крупнее. Голый наклон от этого становится крутым.
t0 = 8000.0
n = int(fps * 1.25)
taus = []
for i in range(n):
    tek = i * dt
    if tek < 0.55:
        size = 30.0
    elif tek < 1.05:
        size = 72.0
    else:
        size = 32.4
    taus.append(kadr(size, t0 + tek)["tau_s"])
# Что сказал бы голый наклон на последнем окне.
xs = [a for a, _ in t._size_hist]
ys = [math.log(v) for _, v in t._size_hist]
m = len(xs)
mx = sum(xs) / m
my = sum(ys) / m
den = sum((x - mx) ** 2 for x in xs)
nak = sum((xs[i] - mx) * (ys[i] - my) for i in range(m)) / den
golyi = 1.0 / nak - (xs[-1] - mx) if nak > t.TAU_MIN_GROWTH_PER_S else None
print("    голый наклон дал бы %s, опубликовано %s" % (golyi, taus[-1]))
assert golyi is not None and golyi < t.FINAL_HOLD_TAU_S, (
    "горб не воспроизвёл ложное короткое время, проверять нечего")
assert taus[-1] is None, "после возврата рамки ложное время осталось"

print("\nOK: время есть только когда рамка реально выросла")
