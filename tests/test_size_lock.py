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

ns = {"np": np, "cv2": cv2, "_size_fail": {"why": 0, "R": 0, "w": 0, "roi": None,
                     "mask": None, "box": None}, "ACQ_DEBUG_DUMP": False,
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


print("=== 1. Коробка следует за целью на сближении и отдалении ===")
# Проверяем именно ПРОЦЕСС, а не разовый вызов. В живом контуре цель растёт
# постепенно, и измеренный размер становится подсказкой для следующего замера.
# Разовый вызов с крошечной подсказкой при огромной цели нереалистичен: так
# трекер не попадает никогда, потому что коробка растёт вместе с целью.
ALPHA = eval(re.search(r"^SIZE_ADAPT_ALPHA = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
PAD = ns["LOCK_PAD"]

sizes = [16, 24, 34, 48, 64, 86, 110, 86, 64, 48, 34, 24, 16]   # подошла и ушла
box = 15.0
hits = 0
print("    %-10s %-12s %s" % ("цель", "коробка", "отношение к цели"))
for side in sizes:
    g = scene(side)
    for _ in range(4):          # несколько кадров на каждом расстоянии
        w, h = est(g, 160, 120, cur_w=box)
        if w is not None:
            hits += 1
            box += ALPHA * (w - box)
    print("    %-10d %-12.1f %.2f" % (side, box, box / side))

assert hits > len(sizes), "замеры почти не проходили: %d удач на %d шагов" % (
    hits, 4 * len(sizes))
# Коробка намеренно шире цели (LOCK_PAD), поэтому сверяем с этим множителем.
assert 0.7 * PAD <= box / sizes[-1] <= 1.5 * PAD, (
    "после возврата к мелкой цели коробка %.0f не сжалась обратно (цель %d)"
    % (box, sizes[-1]))
print("    коробка прошла путь туда и обратно, %d удачных замеров" % hits)

print("\n=== 2. Предел коробки допускает режим сближения ===")
frac = ns["LOCK_MAX_W"] ** 2 / float(W * H)
need = eval(re.search(r"^TERMINAL_BOX_FRAC_THRESHOLD = (.+?)(?:\s+#.*)?$",
                      src, re.M).group(1))
print("    предел коробки %d px -> максимум %.1f%% площади кадра, порог терминала %.0f%%"
      % (ns["LOCK_MAX_W"], 100 * frac, 100 * need))
assert frac >= need, (
    "режим сближения НЕДОСТИЖИМ: коробка не может занять больше %.1f%% "
    "при пороге %.0f%%" % (100 * frac, 100 * need))

print("\n=== 3. На мелкой цели область не раздувается (цена не платится) ===")
ns["_size_R_boost"] = 1.0
g = scene(16)
w, _ = est(g, 160, 120, cur_w=15)
print("    цель 16 px -> измерил %.0f, область осталась %d px, расширение %.1fx"
      % (w, ns["_size_fail"]["R"], ns["_size_R_boost"]))
assert ns["_size_R_boost"] == 1.0, "область раздулась там, где не нужно"
assert ns["_size_fail"]["R"] <= 40, "область слишком велика для мелкой цели"

print("\nOK: замок размыкается, режим сближения достижим, мелкая цель дешёвая")
