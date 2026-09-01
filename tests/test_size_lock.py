"""Замок «коробка не растёт, потому что не измеряется» должен размыкаться.

ЗАМЕРЕНО по заходу 154500: 85% отказов оценки размера — код 2, «пятно залило
область поиска» (1302 кадра против 228 у «нет пятна в центре»).

Причина была замком. Область поиска бралась как cur_w * 1.6, а cur_w упирался
в LOCK_MAX_W = 38. Значит область не могла превысить 60 px, и цель шире 64 px
отвергалась как слишком большая — навсегда. Коробка не росла, потому что не
измерялась, и не измерялась, потому что не росла.
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
for name in ("TRACK_SCALE", "ACQ_SIZE_SEARCH_RADIUS", "SIZE_SEARCH_RADIUS_MAX",
             "SIZE_R_BOOST_STEP", "SIZE_R_BOOST_MAX", "SIZE_UNIFORM_STD", "LOCK_PAD",
             "LOCK_MIN_W", "LOCK_MIN_H", "LOCK_MAX_W", "LOCK_MAX_H"):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1), dict(ns))
ns["clamp"] = lambda v, lo, hi: lo if v < lo else (hi if v > hi else v)
exec(re.search(r"^def estimate_size_at_position.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)
est = ns["estimate_size_at_position"]

W, H = 320, 240


def scene(side):
    g = np.full((H, W), 110, np.uint8)
    r = side // 2
    cv2.rectangle(g, (160 - r, 120 - r), (160 + r, 120 + r), 200, -1)
    return g


print("=== 1. Крупная цель измеряется, пусть и не с первой попытки ===")
for side in (16, 30, 60, 90, 120):
    g = scene(side)
    ns["_size_R_boost"] = 1.0
    got, tries = None, 0
    # Расширение области — механизм из НЕСКОЛЬКИХ кадров: каждый отказ
    # раздвигает область, и следующий кадр смотрит шире.
    for tries in range(1, 7):
        w, h = est(g, 160, 120, cur_w=20)
        if w is not None:
            got = w
            break
    print("    цель %3d px -> %s" % (
        side, "измерил %.0f с попытки %d" % (got, tries) if got else "НЕ СМОГ за 6 попыток"))
    assert got is not None, "цель %d px измерить не удалось" % side

print("\n=== 2. Предел коробки допускает терминальный режим ===")
frac = ns["LOCK_MAX_W"] ** 2 / float(W * H)
need = eval(re.search(r"^TERMINAL_BOX_FRAC_THRESHOLD = (.+?)(?:\s+#.*)?$",
                      src, re.M).group(1))
print("    предел коробки %d px -> максимум %.1f%% площади кадра, порог терминала %.0f%%"
      % (ns["LOCK_MAX_W"], 100 * frac, 100 * need))
assert frac >= need, (
    "терминальный режим НЕДОСТИЖИМ: коробка не может занять больше %.1f%% "
    "при пороге %.0f%%" % (100 * frac, 100 * need))

print("\n=== 3. На мелкой цели область не раздувается (цена не платится) ===")
ns["_size_R_boost"] = 1.0
g = scene(16)
w, _ = est(g, 160, 120, cur_w=15)
print("    цель 16 px -> измерил %.0f, область осталась %d px, расширение %.1fx"
      % (w, ns["_size_fail"]["R"], ns["_size_R_boost"]))
assert ns["_size_R_boost"] == 1.0, "область раздулась там, где не нужно"
assert ns["_size_fail"]["R"] <= 40, "область слишком велика для мелкой цели"

print("\nOK: замок размыкается, терминальный режим достижим, мелкая цель дешёвая")
