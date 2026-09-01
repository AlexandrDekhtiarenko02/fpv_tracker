"""Примерка масштабов обязана СХОДИТЬСЯ, а не разгонять коробку сама по себе.

НАБЛЮДЕНИЕ ОПЕРАТОРА, которое всё объяснило: 'залокался, подвёл на два
сантиметра и ОСТАНОВИЛСЯ, а sz продолжил расти сам'.

Так и было. Коробка росла по измеренному множителю, а ЭТАЛОН при этом не
менялся: он обновляется только при совпадении форм, а формы расходились как
раз потому, что коробка выросла. Значит следующая примерка видела ровно ту же
разницу и снова говорила 'крупнее'. Обратная связь была разомкнута.

В логе 170104 это видно прямо: множитель 1.18 в 1315 кадрах против 0.847 в
60, и коробка 22% времени стояла на самом пределе.

Здесь проверяется, что круг замкнулся: цель выросла один раз и замерла —
коробка обязана догнать её и ОСТАНОВИТЬСЯ.
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
             "SIZE_SCALE_ALPHA", "SEARCH_MARGIN_MIN", "TEMPLATE_MIN",
             "TEMPLATE_MAX", "LOCK_MIN_W", "LOCK_MAX_W"):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1), dict(ns))
for fn in ("clamp", "clamp_rect_center", "crop_center"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef )" % fn, src, re.S | re.M)
    if m:
        exec(m.group(0), ns)
exec(re.search(r"^def measure_scale_change.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)
clamp = ns["clamp"]

_TEX = np.clip(150.0 + cv2.GaussianBlur(
    np.random.default_rng(7).normal(0, 30, (256, 256)), (0, 0), 3.0) * 3,
    0, 255).astype(np.uint8)
_WALL = np.clip(115.0 + cv2.GaussianBlur(
    np.random.default_rng(4).normal(0, 9, (240, 320)), (0, 0), 3.5) * 3,
    0, 255).astype(np.uint8)


def scene(side):
    g = _WALL.copy()
    r = max(4, side // 2)
    g[120 - r:120 + r, 160 - r:160 + r] = cv2.resize(
        _TEX, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    return g


def run(sizes, label):
    """Прогон контура так же, как в трекере: коробка и эталон меняются вместе."""
    box = 20.0
    base_side = sizes[0]
    r = int(clamp(box * 2, ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"])) // 2
    base = scene(base_side)[120 - r:120 + r, 160 - r:160 + r].copy()
    ns["template_gray"] = base.copy()
    acc = 1.0
    out = []
    for side in sizes:
        g = scene(side)
        for _ in range(6):
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
        out.append((side, box))
    print("    %s" % label)
    for side, b in out:
        print("      цель %3d px -> коробка %6.1f" % (side, b))
    return [b for _, b in out]


print("=== 1. Цель выросла один раз и ЗАМЕРЛА — коробка обязана остановиться ===")
res = run([30] * 2 + [36] + [36] * 6, "рост 30 -> 36, затем покой")
tail = res[-4:]
drift = (max(tail) - min(tail)) / max(tail)
print("      разброс на участке покоя: %.1f%%" % (100 * drift))
assert drift < 0.10, (
    "коробка продолжает ползти в покое на %.0f%% — обратная связь разомкнута"
    % (100 * drift))

print("\n=== 2. Коробка не упирается в предел на ровном месте ===")
assert res[-1] < ns["LOCK_MAX_W"] * 0.9, (
    "коробка уехала в предел %.0f при цели, выросшей всего на 20%%" % res[-1])
print("      коробка %.1f при пределе %d" % (res[-1], ns["LOCK_MAX_W"]))

print("\n=== 3. Настоящее сближение коробка отслеживает ===")
res2 = run([24, 30, 38, 48, 60], "цель растёт как при заходе")
assert res2[-1] > res2[0] * 1.5, (
    "коробка не пошла за целью: %.0f -> %.0f" % (res2[0], res2[-1]))
print("      коробка выросла в %.1f раза при росте цели в %.1f"
      % (res2[-1] / res2[0], 60 / 24.0))

print("\nOK: круг замкнут — коробка догоняет цель и останавливается")
