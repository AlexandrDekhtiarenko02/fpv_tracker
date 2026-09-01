"""Начальный размер коробки: не слишком мал и не слишком велик.

Оператор: 'отодвинул флешку на полтора метра — лок вообще её не видел'. Это
было следствием подъёма начальной коробки с 8 до 20: для 13-пиксельной цели
эталон 40x40 набивается фоном, и на фактурном фоне трекер держится за фон.

Но и вернуть 8 нельзя: эталон 16x16 слишком мал, чтобы в нём различался
масштаб, и рост цели перестаёт замечаться.

Здесь закреплена середина, найденная замером.
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
             "SIZE_SCALE_ALPHA", "SIZE_SCALE_DOWNSAMPLE", "SIZE_SCALE_DS_MIN",
             "SEARCH_MARGIN_MIN", "TEMPLATE_MIN", "TEMPLATE_MAX",
             "TEMPLATE_SCALE", "LOCK_MIN_W", "LOCK_MAX_W",
             "ACQ_DEFAULT_LOCK_W"):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1), dict(ns))
for fn in ("clamp", "clamp_rect_center", "crop_center"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef )" % fn, src, re.S | re.M)
    if m:
        exec(m.group(0), ns)
exec(re.search(r"^def measure_scale_change.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)
clamp = ns["clamp"]
DEF = ns["ACQ_DEFAULT_LOCK_W"]

TEX = np.clip(150.0 + cv2.GaussianBlur(
    np.random.default_rng(7).normal(0, 30, (256, 256)), (0, 0), 3.0) * 3,
    0, 255).astype(np.uint8)
WALL = np.clip(115.0 + cv2.GaussianBlur(
    np.random.default_rng(4).normal(0, 11, (240, 320)), (0, 0), 2.2) * 3,
    0, 255).astype(np.uint8)


def scene(side):
    g = WALL.copy()
    r = max(3, side // 2)
    g[120 - r:120 + r, 160 - r:160 + r] = cv2.resize(
        TEX, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    return g


def follow(box0, sizes):
    box = float(box0)
    t = int(clamp(box * ns["TEMPLATE_SCALE"], ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"]))
    r = t // 2
    base = scene(sizes[0])[120 - r:120 + r, 160 - r:160 + r].copy()
    ns["template_gray"] = base.copy()
    acc = 1.0
    for side in sizes:
        g = scene(side)
        for _ in range(8):
            k = ns["measure_scale_change"](g, 160, 120)
            if k is None or k == 1.0:
                continue
            grow = 1.0 + (k - 1.0) * ns["SIZE_SCALE_ALPHA"]
            box = clamp(box * grow, ns["LOCK_MIN_W"], ns["LOCK_MAX_W"])
            acc = clamp(acc * grow, 0.25, 6.0)
            bh, bw = base.shape[:2]
            nw = int(round(clamp(bw * acc, ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"])))
            nh = int(round(clamp(bh * acc, ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"])))
            if (nw, nh) != ns["template_gray"].shape[::-1]:
                ns["template_gray"] = cv2.resize(base, (nw, nh),
                                                 interpolation=cv2.INTER_LINEAR)
    return box / box0


SIZES = [13, 16, 20, 25, 32, 40]
TRUE = SIZES[-1] / float(SIZES[0])

print("=== Мелкая цель на фактурном фоне: 13 -> 40 px (в %.2f раза) ===" % TRUE)
for box0 in (8, DEF, 20):
    got = follow(box0, SIZES)
    mark = "  <- по умолчанию" if box0 == DEF else ""
    print("    начальная коробка %2d (эталон %2d): коробка выросла в %.2f раза%s"
          % (box0, int(box0 * ns["TEMPLATE_SCALE"]), got, mark))

got = follow(DEF, SIZES)
assert 0.7 * TRUE <= got <= 1.35 * TRUE, (
    "при коробке по умолчанию %d рост отслежен как %.2f при истинном %.2f"
    % (DEF, got, TRUE))

print("\n=== Эталон примерно вдвое шире цели, как задумано ===")
tmpl = DEF * ns["TEMPLATE_SCALE"]
print("    цель 13 px, эталон %d px -> отношение %.1f" % (tmpl, tmpl / 13.0))
assert 1.4 <= tmpl / 13.0 <= 2.6, (
    "эталон %d для цели 13 px: слишком %s" % (tmpl, "мал" if tmpl / 13.0 < 1.4 else "велик"))

print("\nOK: начальный размер держит и мелкую цель, и рост")
