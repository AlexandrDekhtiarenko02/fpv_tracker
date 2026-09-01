"""Размер при ЗАХВАТЕ должен измеряться, а не браться по умолчанию.

Оператор описал всю цепочку: лок на флешку в метре, sz = 16 и не меняется;
потом рамка уезжает на край флешки, и только тогда sz начинает шевелиться,
но лок уже за предметом.

Первое число здесь и есть причина. 16 в координатах кадра — это 8 в
координатах слежения, то есть ACQ_DEFAULT_LOCK_W, значение ПО УМОЛЧАНИЮ.
Размер при захвате не измерился вовсе.

Почему не измерился: область бралась одна, радиусом 12, окошко 25x25. Цель
крупнее окошка касается его края, измерение отказывает — подставляется
умолчание.

Дальше рушится всё: эталон вдвое больше коробки, то есть 16x16 — крошечный
кусок предмета. Он цепляется не за предмет, а за случайный участок фактуры и
уезжает на край, где контраст выше. Примерка масштабов на таком эталоне тоже
молчит: кусок фактуры при увеличении предмета выглядит почти так же.
"""
import io, os, re
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "_size_fail": {"why": 0, "R": 0, "w": 0},
      "_size_R_boost": 1.0}
ns["HIRES_TRACKING"] = eval(
    re.search(r"^HIRES_TRACKING = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
for name in ("TRACK_SCALE", "ACQ_SIZE_SEARCH_RADIUS", "SIZE_ACQ_RADII",
             "SIZE_SEARCH_RADIUS_MAX", "SIZE_R_BOOST_STEP", "SIZE_R_BOOST_MAX",
             "SIZE_UNIFORM_STD", "SIZE_ACQ_AGREE_TOL", "LOCK_PAD", "LOCK_MIN_W", "LOCK_MIN_H",
             "LOCK_MAX_W", "LOCK_MAX_H", "ACQ_DEFAULT_LOCK_W"):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1), dict(ns))
ns["CENTER_X_LORES"], ns["CENTER_Y_LORES"] = 160, 120
ns["clamp"] = lambda v, lo, hi: lo if v < lo else (hi if v > hi else v)
exec(re.search(r"^def estimate_size_at_position.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)
exec(re.search(r"^def estimate_size_at_crosshair.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)

rng = np.random.default_rng(2)
_TEX = np.clip(150.0 + cv2.GaussianBlur(
    np.random.default_rng(7).normal(0, 30, (256, 256)), (0, 0), 3.0) * 3,
    0, 255).astype(np.uint8)


def scene(side):
    """Флешка своей фактуры на фактурной стене."""
    g = np.clip(110.0 + cv2.GaussianBlur(
        rng.normal(0, 7, (240, 320)), (0, 0), 3.5) * 3, 0, 255).astype(np.uint8)
    r = max(3, side // 2)
    g[120 - r:120 + r, 160 - r:160 + r] = cv2.resize(
        _TEX, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    return g


DEFAULT = ns["ACQ_DEFAULT_LOCK_W"]
PAD = ns["LOCK_PAD"]

print("=== Размер при захвате для целей разного размера ===")
print("    %-10s %-14s %s" % ("цель", "измерено", "отношение к цели"))
measured = 0
for side in (10, 19, 30, 48, 70):
    lw, lh = ns["estimate_size_at_crosshair"](scene(side))
    if lw is None:
        print("    %-10d %-14s -" % (side, "НЕ ИЗМЕРЕНО"))
        continue
    measured += 1
    print("    %-10d %-14.1f %.2f" % (side, lw, lw / side))
    assert lw > DEFAULT * 1.2 or side <= DEFAULT, (
        "цель %d px получила размер по умолчанию %.0f" % (side, lw))
    assert 0.6 * PAD <= lw / side <= 1.8 * PAD, (
        "цель %d px измерена как %.0f — это не её размер" % (side, lw))

assert measured >= 4, "измерилось лишь %d целей из 5" % measured
print("\nOK: захват меряет размер цели, а не берёт умолчание")
