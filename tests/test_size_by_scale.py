"""Размер меряется масштабом совпадения, а не сегментацией.

ЗАЧЕМ. Прежний способ — порог по яркости вокруг центра и связная компонента —
негоден в загромождённой сцене в принципе, а не по настройкам. Проверено на
модели комнаты с посторонними предметами:

    цель  20 px -> намерил 120   (слился с соседним предметом)
    цель  38 px -> намерил  80
    цель  64 px -> намерил 120

Он либо упирается в край области, либо сливает цель с соседями и ВРЁТ. На
борту это и наблюдалось: оператор видел изменение размера только когда
подносил предмет на 10-20 см, то есть когда тот занимал почти весь кадр.

Здесь размер меряется тем же признаком, которым ведётся слежение.
"""
import io, os, re
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2}
ns["HIRES_TRACKING"] = eval(
    re.search(r"^HIRES_TRACKING = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
for name in ("TRACK_SCALE", "SIZE_SCALE_STEP", "SIZE_SCALE_MIN_LEAD",
             "SIZE_SCALE_DOWNSAMPLE", "SIZE_SCALE_DS_MIN", "SEARCH_MARGIN_MIN"):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1), dict(ns))
for _fn in ("clamp", "clamp_rect_center", "crop_center"):
    _m = re.search(r"^def %s\(.*?(?=\n\ndef )" % _fn, src, re.S | re.M)
    if _m:
        exec(_m.group(0), ns)
ns.setdefault("clamp", lambda v, lo, hi: lo if v < lo else (hi if v > hi else v))
exec(re.search(r"^def measure_scale_change.*?(?=\n\ndef )", src, re.S | re.M).group(0), ns)

rng = np.random.default_rng(5)

# Одна и та же фактура цели, отрисованная в разном масштабе. Это принципиально:
# если рисовать шум заново для каждого размера, картинки будут разными, а не
# увеличенными, и мерить масштаб станет нечего.
_TGT = np.clip(
    190.0 + cv2.GaussianBlur(
        np.random.default_rng(1).normal(0, 26, (256, 256)), (0, 0), 3.0) * 3,
    0, 255).astype(np.uint8)
_WALL = np.clip(
    120.0 + cv2.GaussianBlur(rng.normal(0, 9, (240, 320)), (0, 0), 4.0) * 3,
    0, 255).astype(np.uint8)


def room(side, seed_objects=True):
    """Комната: фактурная стена, посторонние предметы и цель в центре."""
    g = _WALL.astype(np.float32).copy()
    if seed_objects:
        r2 = np.random.default_rng(99)
        for _ in range(6):
            x, y = r2.integers(20, 300), r2.integers(20, 220)
            w, h = r2.integers(20, 70), r2.integers(20, 70)
            cv2.rectangle(g, (x, y), (x + w, y + h), float(r2.integers(60, 190)), -1)
    g = cv2.GaussianBlur(g, (0, 0), 1.0)
    r = max(3, side // 2)
    patch = cv2.resize(_TGT, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    g[120 - r:120 + r, 160 - r:160 + r] = patch
    return np.clip(g, 0, 255).astype(np.uint8)


STEP = ns["SIZE_SCALE_STEP"]
BASE = 40


def template_from(g, side):
    r = side  # эталон вдвое шире цели, как TEMPLATE_SCALE = 2
    return g[120 - r:120 + r, 160 - r:160 + r].copy()


print("=== 1. Цель выросла — обнаруживается рост ===")
g0 = room(BASE)
ns["template_gray"] = template_from(g0, BASE // 2)
g1 = room(int(BASE * STEP))
k = ns["measure_scale_change"](g1, 160, 120)
print("    цель %d -> %d px, множитель %s" % (BASE, int(BASE * STEP), k))
assert k is not None and k > 1.0, "рост не обнаружен: %s" % k

print("\n=== 2. Цель уменьшилась — обнаруживается уменьшение ===")
g2 = room(int(BASE / STEP))
k = ns["measure_scale_change"](g2, 160, 120)
print("    цель %d -> %d px, множитель %s" % (BASE, int(BASE / STEP), k))
assert k is not None and k < 1.0, "уменьшение не обнаружено: %s" % k

print("\n=== 3. Размер не менялся — молчим, а не шумим ===")
k = ns["measure_scale_change"](g0, 160, 120)
print("    цель осталась %d px, множитель %s" % (BASE, k))
assert k == 1.0, "масштаб дрогнул там, где цель не менялась: %s" % k

print("\n=== 4. Загромождение не мешает: соседние предметы на месте ===")
for side in (24, 40, 64, 96):
    g = room(side)
    ns["template_gray"] = template_from(g, side // 2)
    grown = room(int(side * STEP))
    k = ns["measure_scale_change"](grown, 160, 120)
    print("    цель %3d px в комнате с предметами -> множитель %s" % (side, k))
    assert k is not None and k > 1.0, "цель %d px: рост не увиден (%s)" % (side, k)

print("\nOK: масштаб меряется совпадением и работает при любом размере цели")
