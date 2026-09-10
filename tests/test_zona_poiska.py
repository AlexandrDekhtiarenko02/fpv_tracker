"""Прицел имеет приоритет; зона поиска — только запасной ход.

Наводить перекрестье ТОЧНО на цель неудобно: аппарат трясёт, цель мелкая, и
промах на десяток пикселей означает захват фона рядом с ней. Пилот целится
«в район» — значит в этом районе и надо искать то, что выделяется.

ПОРЯДОК ВАЖНЕЕ САМОГО ПОИСКА. Если под прицелом уже есть выраженность,
выбор окончен: берём её и ни с чем не сравниваем. Сравнение и было ошибкой —
рядом почти всегда найдётся предмет контрастнее, и захват перекидывало на него
с уже наведённой цели. Зона включается, только когда под прицелом пусто.

Внутри зоны выбирает РАССТОЯНИЕ, а не выраженность. Выраженность отвечает на вопрос «что
тут вообще есть», а не «что пилот имел в виду»: край дороги, столб или тень
почти всегда контрастнее небольшой цели, и по яркости захват уезжал бы на них.
Поэтому выраженность работает порогом, а выбирает близость к прицелу.

Второе требование не менее важно: над однородной поверхностью зона обязана
МОЛЧАТЬ и отдавать захват ровно туда, куда навёл пилот. Иначе она из помощи
превращается в непредсказуемость.
"""
import io
import math
import os
import re

import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "math": math}
for name in ("ACQ_SNAP_ENABLED", "ACQ_SNAP_SIGMA_MELKO", "ACQ_SNAP_SIGMA_KRUPNO",
             "ACQ_SNAP_PEAK_OKNO", "ACQ_SNAP_YADRO_LORES",
             "ACQ_SNAP_SIGMA_YADRO", "ACQ_SNAP_YADRO_MIN_OTN",
             "ACQ_SNAP_YADRO_MIN_ABS", "ACQ_SNAP_MIN_OTN", "ACQ_SNAP_MIN_ABS"):
    ns[name] = eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1))
W, H = 320, 240
ns["CENTER_X_LORES"], ns["CENTER_Y_LORES"] = W // 2, H // 2
ns["ACQ_SNAP_RADIUS_LORES"] = 30
exec(re.search(r"^def _nayti_pyatno.*?(?=\n\ndef )", src, re.S | re.M).group(0), ns)
nayti = ns["_nayti_pyatno"]

rng = np.random.default_rng(7)


def fon(shum=3.0):
    f = np.full((H, W), 120.0, np.float32)
    f += rng.normal(0, shum, f.shape)
    return np.clip(cv2.GaussianBlur(f, (0, 0), 1.0), 0, 255).astype(np.uint8)


print("=== 1. Под прицелом есть цель — берём её, ни с чем не сравнивая ===")
# Рядом нарочно ставим предмет ЗАМЕТНО контрастнее: раньше захват уезжал на него.
for podpis, jarche in (("равный", 40), ("много ярче", 15)):
    g = fon()
    cv2.circle(g, (W // 2, H // 2), 5, 60, -1)            # под прицелом
    cv2.circle(g, (W // 2 + 20, H // 2 + 4), 7, jarche, -1)   # рядом, ярче
    p = nayti(g)
    assert p is not None, "не нашла ничего"
    sdvig = math.hypot(p[0] - W // 2, p[1] - H // 2)
    print("    сосед %-11s -> причина %-7s сдвиг от прицела %.1f px"
          % (podpis, p[2], sdvig))
    assert p[2] == "прицел", (
        "захват уехал на соседа (%s): под прицелом уже была цель" % podpis)
    assert sdvig < 0.5, "сдвиг %.1f px при захвате по прицелу" % sdvig

print("\n=== 2. Под прицелом пусто — уезжаем на ближайшее ===")
for sdvig_celi, storona in ((22, "вправо"), (-24, "влево")):
    g = fon()
    cx, cy = W // 2 + sdvig_celi, H // 2 - 4
    cv2.circle(g, (cx, cy), 6, 35, -1)
    p = nayti(g)
    assert p is not None, "цель %s не найдена вовсе" % storona
    assert p[2] == "пятно", "цель %s: под прицелом пусто, а причина %s" % (storona, p[2])
    oshibka = math.hypot(p[0] - cx, p[1] - cy)
    print("    цель %s: найдено (%.0f,%.0f), промах %.1f px"
          % (storona, p[0], p[1], oshibka))
    assert oshibka <= 6.0, "промах %.1f px" % oshibka

print("\n=== 3. Пустой фон — зона обязана молчать ===")
molchit = sum(1 for _ in range(12) if nayti(fon()) is None)
print("    промолчала в %d случаях из 12" % molchit)
assert molchit >= 11, (
    "на однородном фоне зона срабатывает (%d из 12): захват станет "
    "непредсказуемым там, где пилот целится вручную" % molchit)

print("\n=== 4. Под прицелом пусто: ближнее слабое побеждает дальнее яркое ===")
g = fon()
cv2.circle(g, (W // 2 + 12, H // 2 + 3), 5, 95, -1)     # ближе, скромнее
cv2.circle(g, (W // 2 + 26, H // 2 - 8), 7, 25, -1)     # дальше, ярче
p = nayti(g)
assert p is not None and p[2] == "пятно"
d_bl = math.hypot(p[0] - (W // 2 + 12), p[1] - (H // 2 + 3))
d_dl = math.hypot(p[0] - (W // 2 + 26), p[1] - (H // 2 - 8))
print("    выбрано (%.0f,%.0f): до ближнего %.1f px, до дальнего %.1f px"
      % (p[0], p[1], d_bl, d_dl))
assert d_bl < d_dl, (
    "выбрано дальнее яркое: значит решает выраженность, а не близость")

print("\n=== 4б. Граница: сосед вплотную — держим прицел, далёкий — уезжаем ===")
for d, ozhid in ((12, "прицел"), (22, "пятно")):
    g = fon()
    cv2.circle(g, (W // 2 + d, H // 2), 6, 35, -1)
    p = nayti(g)
    assert p is not None, "сосед в %d px: не нашла ничего" % d
    print("    сосед в %2d px -> %s" % (d, p[2]))
    assert p[2] == ozhid, (
        "сосед в %d px дал «%s», ожидалось «%s»: граница между «прицел на "
        "предмете» и «прицел на пустом месте» уехала" % (d, p[2], ozhid))

print("\n=== 5. Фактурный предмет: захват НЕ ездит по нему ===")
# Здесь ломались два прежних подхода. У фактуры вершин много, и требование
# «своя вершина под прицелом» гнало захват с выбранного места на соседнюю
# вершину ТОГО ЖЕ предмета.
def faktura(cx, cy, r, seed):
    g = fon()
    rr = np.random.default_rng(seed)
    y, x = np.mgrid[0:H, 0:W]
    vnutri = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    shum = rr.normal(0, 26, (H, W))
    shum = cv2.GaussianBlur(shum.astype(np.float32), (0, 0), 1.3)
    pole = g.astype(np.float32)
    pole[vnutri] = np.clip(70 + shum, 0, 255)[vnutri]
    return np.clip(pole, 0, 255).astype(np.uint8)

uehalo = 0
for smesh in (-8, -4, 0, 4, 8):
    # Прицел стоит В РАЗНЫХ местах одного фактурного предмета.
    g = faktura(W // 2 - smesh, H // 2, 16, 100 + smesh)
    p = nayti(g)
    assert p is not None, "на фактуре не нашла ничего (смещение %+d)" % smesh
    if p[2] != "прицел":
        uehalo += 1
        print("    смещение %+d: УЕХАЛО на (%.0f,%.0f)" % (smesh, p[0], p[1]))
    else:
        print("    смещение %+d: захват по прицелу" % smesh)
assert uehalo == 0, (
    "%d из 5 точек фактурного предмета захват уехал: пилот выбирает часть "
    "предмета, а его перекидывает на другую" % uehalo)

print("\nOK: прицел имеет приоритет, зона — только запасной ход")
