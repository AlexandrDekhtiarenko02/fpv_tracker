"""Растущий угол визирования правит ПРИЦЕЛ, а не газ.

На курсе столкновения с неподвижной целью угол визирования стоит. Растёт —
цель уходит под нас, пройдём выше.

ЭТУ РАБОТУ СНАЧАЛА ОТДАЛИ ГАЗУ, И ЭТО БЫЛА ОШИБКА. В поле вышло ровно то,
чего и следовало ждать от лечения геометрии энергией: угол продолжал расти
(1-5 °/с, перелёт в пяти заходах из восьми), а газ проседал до 91 PWM ниже
стика — почти до холостого. Убирая тягу, квад не только снижается, но и
теряет скорость: вектор скорости к цели не приближается, аппарат проваливается.

Прицел при этом стоял на цели с точностью до пары пикселей. Значит нос
смотрел верно, а ЛЕТЕЛ аппарат выше линии визирования — это ошибка
прицеливания, и лечится она опусканием точки прицеливания. Ровно это пилот
делал руками и называл «держать нос ниже цели».
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

assert zn["LOS_AIM_ENABLED"] is True, "поправка прицела по углу выключена"
assert zn["LOS_THROTTLE_ENABLED"] is False, (
    "газ снова держит угол визирования: это лечение геометрии энергией, и в "
    "поле оно дало растущий угол при просевшем газе")

ZAMER_DPS = 2.13   # замеренная скорость роста на промахе
sdvig = min(zn["LOS_AIM_GAIN"] * ZAMER_DPS, zn["LOS_AIM_MAX_PX"])
print("на замеренных %.2f °/с прицел опускается на %.0f px" % (ZAMER_DPS, sdvig))
OBYCHNAYA_OSHIBKA_PX = 15.0   # замеренный коридор ошибки прицела
assert sdvig > OBYCHNAYA_OSHIBKA_PX * 2, (
    "поправка %.0f px теряется в обычной ошибке прицела (%.0f px) — она не "
    "изменит траекторию" % (sdvig, OBYCHNAYA_OSHIBKA_PX))
assert zn["LOS_AIM_MAX_PX"] <= 120.0, (
    "предел %.0f px уводит прицел за край кадра, и контур погонится за точкой "
    "вне поля зрения" % zn["LOS_AIM_MAX_PX"])

# Фильтр: ниже раскачки, но быстрее геометрии захода.
import math as _m
tau_s = -1.0 / (zn["NOMINAL_FPS"] * _m.log(1.0 - zn["LOS_RATE_ALPHA"]))
srez = 1.0 / (2.0 * _m.pi * tau_s)
print("срез фильтра угла %.2f Гц (постоянная %.2f с)" % (srez, tau_s))
assert srez < 1.3 / 2.0, (
    "срез %.2f Гц не ниже раскачки 1.3 Гц: прицел пойдёт за колебанием" % srez)
assert srez > 0.15, (
    "срез %.2f Гц слишком низкий: прицел не успеет за геометрией захода" % srez)

# Знак. Угол растёт -> пройдём выше -> целимся НИЖЕ -> прицел опускается.
assert "los_aim_px = LOS_AIM_GAIN * _los_skorost" in src, (
    "знак поправки прицела не тот: при растущем угле целиться надо ниже")
assert "+ los_aim_px) - CENTER_Y" in src, (
    "поправка не входит в прицельную ошибку")

# Величина обязана СЧИТАТЬСЯ вне выключенной ветки газа, иначе замрёт.
i_gaz = src.index("elif LOS_THROTTLE_ENABLED")
i_rasch = src.index("_los_skorost += alpha_for_dt(LOS_RATE_ALPHA")
assert i_rasch < i_gaz, (
    "скорость угла снова считается внутри ветки газа, а та выключена — "
    "прицел получит вечный ноль и никак этого не покажет")

# Признак промаха обязан быть виден в логе.
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
kol = header.rstrip(",").split(",")
for imya in ("los_rate", "los_aim_px"):
    assert imya in kol, "нет колонки %s — промах нечем будет разобрать" % imya

# Состояние сбрасывается между заходами.
assert src.count("_los_ugol = None") >= 2, (
    "угол визирования не сбрасывается при потере управления")
print("прицел правится углом, газ этим больше не занят")
