"""Крен правится скоростью АЗИМУТА, а не только ошибкой прицела.

ТА ЖЕ БОЛЕЗНЬ, ЧТО ПЕРЕЛЁТ, ТОЛЬКО ВБОК. На курсе столкновения азимут цели
стоит; крутится — проходим мимо стороной.

Замерено на заходах 9d4ecb6 (22 захода):
    азимут крутится с медианой 2.86 °/с, на 90-м процентиле 8.19
    а ошибка прицела по горизонтали при этом мизерная: в заходе с азимутом
    -8.19 °/с она была 4 px, в заходе с -12.43 °/с — 19 px
    команда крена шла за ошибкой С НУЛЕВЫМ сдвигом (r = 0.883) и не имела
    опережения вовсе; вклад упреждения по сносу — 0.20 PWM из 8.4, то есть 2%

Перекрестье на цели, а аппарат идёт мимо. Ошибка прицела этого не показывает
в принципе — нужен азимут.
"""
import ast
import io
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)
zn = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        imya = getattr(node.targets[0], "id", None)
        if imya:
            try:
                zn[imya] = ast.literal_eval(node.value)
            except Exception:
                pass

print("=== 1. Поправка соразмерна замеренному уводу ===")
assert zn["LOS_AIM_X_ENABLED"] is True, "поправка вбок выключена"
ZAMER_MED, ZAMER_90 = 2.86, 8.19
OSHIBKA_PX = 7.0          # замеренная ошибка прицела по горизонтали, 90%
for dps, podpis in ((ZAMER_MED, "медиана"), (ZAMER_90, "90%")):
    px = min(zn["LOS_AIM_X_GAIN"] * (dps - zn["LOS_AZ_DEADBAND_DPS"]),
             zn["LOS_AIM_X_MAX_PX"])
    print("    азимут %.2f °/с (%s) -> прицел вбок %.0f px" % (dps, podpis, px))
px_med = min(zn["LOS_AIM_X_GAIN"] * (ZAMER_MED - zn["LOS_AZ_DEADBAND_DPS"]),
             zn["LOS_AIM_X_MAX_PX"])
assert px_med > OSHIBKA_PX, (
    "поправка %.0f px не больше самой ошибки прицела (%.0f) — она потеряется"
    % (px_med, OSHIBKA_PX))
assert zn["LOS_AIM_X_MAX_PX"] <= 40.0, (
    "предел %.0f px слишком велик: поправка подменит прицеливание"
    % zn["LOS_AIM_X_MAX_PX"])

print("\n=== 2. Курс замкнут по кругу — развёртывание обязательно ===")
assert "_d -= 360.0" in src and "_d += 360.0" in src, (
    "переход курса через ноль не обрабатывается: один такой переход испортит "
    "окно оценки целиком")

print("\n=== 3. Наклон по окну, а не разность соседних кадров ===")
assert "_az_hist.append((now_mono, _az_nakop))" in src, (
    "азимут снова считается разностью: курс приходит ступенями так же, как "
    "тангаж, и на разностях это давало ложную скорость впятеро выше")
# Тот же опыт, что поймал беду с вертикалью.
FPS = zn["CAM_FPS"]
OKNO = zn["LOS_FIT_WINDOW_S"]
MINP = zn["LOS_FIT_MIN_POINTS"]
import collections as _c


def po_oknu(ryad):
    h = _c.deque()
    out = []
    for tt, v in ryad:
        h.append((tt, v))
        while h and (tt - h[0][0]) > OKNO:
            h.popleft()
        if len(h) >= MINP:
            xs = [a for a, _ in h]
            ys = [b for _, b in h]
            n = len(xs)
            mx = sum(xs) / n
            my = sum(ys) / n
            den = sum((x - mx) ** 2 for x in xs)
            if den > 1e-6:
                out.append(sum((xs[i] - mx) * (ys[i] - my)
                               for i in range(n)) / den)
    return out


ryad, stupen = [], 0.0
for i in range(int(FPS * 6)):
    tt = i / FPS
    istina = 3.0 * tt          # настоящий увод 3 °/с
    if istina - stupen >= 2.5:
        stupen += 2.5
    ryad.append((tt, stupen))
m = max(abs(x) for x in po_oknu(ryad))
print("    ступени курса по 2.5° при уводе 3.0 °/с -> оценка не выше %.2f" % m)
assert m < 5.0, (
    "ступени дают %.1f °/с при уводе 3.0 — прицел будет рвать вбок" % m)

print("\n=== 4. Поправка не может прыгнуть ===")
za_kadr = zn["LOS_AIM_SLEW_PX_S"] / zn["CAM_FPS"]
print("    не быстрее %.1f px за кадр, полный ход за %.1f с"
      % (za_kadr, zn["LOS_AIM_X_MAX_PX"] / zn["LOS_AIM_SLEW_PX_S"]))
assert za_kadr <= 3.0

print("\n=== 5. Состояние чистится между заходами ===")
for imya in ("_az_nakop = 0.0", "_az_pred = None", "_los_aim_x_tek = 0.0"):
    assert src.count(imya) >= 2, (
        "%s не сбрасывается: накопленный курс прошлой цели даст выброс на "
        "первом же кадре новой" % imya.split()[0])

print("\n=== 6. Признак видно в логе ===")
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
kol = header.rstrip(",").split(",")
for imya in ("az_rate", "los_aim_x_px"):
    assert imya in kol, "нет колонки %s — увод вбок нечем будет разобрать" % imya

print("\nOK: крен получил то, чего у него не было вовсе")
