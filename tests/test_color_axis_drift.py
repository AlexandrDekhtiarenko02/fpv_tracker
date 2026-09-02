"""Цель уходит на фон ДРУГОГО цвета — ось обязана перестроиться.

Ось строится как «цвет цели минус цвет фона». Цвет цели — её опознавательный
признак, он снят при захвате и не меняется: иначе, стоит рамке чуть сползти,
целью начнёт считаться фон.

А вот ФОН по ходу захода меняется по-настоящему: оранжевая машина уходит с
серого асфальта на жёлтое поле. Ось со старым фоном становится неверной, и
цель в проекции перестаёт быть яркой.

Проверяется три вещи: ось перестраивается под новый фон; цвет цели при этом
НЕ уезжает; а если цель и фон сравнялись по цвету — слежение по проекции
честно отключается.
"""
import io, os, re, math
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "math": math, "color_axis": None}
for name in ("TRACK_ON_COLOR_DROP_SEP",):
    ns[name] = eval(
        re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1))


class _Log(object):
    def __init__(self):
        self.msgs = []

    def event(self, t):
        self.msgs.append(t)


ns["flight_log"] = _Log()
exec(re.search(r"^def refresh_color_axis.*?(?=\n\ndef )", src, re.S | re.M).group(0), ns)

W, H = 160, 120


def scene(bg_u, bg_v, tgt_u, tgt_v, cx=80, cy=60, r=6):
    u = np.full((H, W), float(bg_u), np.float32)
    v = np.full((H, W), float(bg_v), np.float32)
    u[cy - r:cy + r, cx - r:cx + r] = tgt_u
    v[cy - r:cy + r, cx - r:cx + r] = tgt_v
    return u.astype(np.uint8), v.astype(np.uint8)


TGT = (123, 161)          # оранжевое, как на записи
ASPHALT = (137, 130)      # серый асфальт
FIELD = (100, 150)        # жёлто-зелёное поле — ДРУГОЙ фон

print("=== 1. Цель ушла на фон другого цвета — ось перестроилась ===")
du = TGT[0] - ASPHALT[0]
dv = TGT[1] - ASPHALT[1]
sep = math.hypot(du, dv)
ns["color_axis"] = (du / sep, dv / sep, ASPHALT[0], ASPHALT[1], 5.0, TGT[0], TGT[1])
ns["chroma_u"], ns["chroma_v"] = scene(FIELD[0], FIELD[1], TGT[0], TGT[1])
ok = ns["refresh_color_axis"](160, 120, 24, 24)
axis = ns["color_axis"]
assert ok and axis is not None, "ось потерялась там, где цель прекрасно видна"
print("    фон в оси: было (%d, %d) -> стало (%.0f, %.0f), в кадре (%d, %d)"
      % (ASPHALT[0], ASPHALT[1], axis[2], axis[3], FIELD[0], FIELD[1]))
assert abs(axis[2] - FIELD[0]) <= 3 and abs(axis[3] - FIELD[1]) <= 3, (
    "фон в оси не догнал новый фон кадра")

print("\n=== 2. Цвет цели при этом НЕ уехал ===")
print("    цель в оси: (%.0f, %.0f), исходная (%d, %d)"
      % (axis[5], axis[6], TGT[0], TGT[1]))
assert (axis[5], axis[6]) == TGT, "цвет цели изменился — так рамка уедет на фон"

print("\n=== 3. Цель на фоне СВОЕГО цвета — проекция честно отключается ===")
ns["color_axis"] = (du / sep, dv / sep, ASPHALT[0], ASPHALT[1], 5.0, TGT[0], TGT[1])
ns["chroma_u"], ns["chroma_v"] = scene(TGT[0] - 1, TGT[1] + 1, TGT[0], TGT[1])
ok = ns["refresh_color_axis"](160, 120, 24, 24)
print("    итог: %s" % ("вернулись на яркость" if ns["color_axis"] is None
                        else "ПРОДОЛЖАЕМ ПО ЦВЕТУ — плохо"))
assert ns["color_axis"] is None and not ok, (
    "цель и фон одного цвета, а мы всё ещё ведём по проекции")
assert any("НЕ РАЗЛИЧАЕТ" in m for m in ns["flight_log"].msgs), (
    "переход обратно на яркость не записан в журнал")
print("    в журнале: %s" % ns["flight_log"].msgs[-1])

print("\nOK: фон догоняет сцену, цвет цели держится, вырождение ловится")
