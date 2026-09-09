"""Газ держит угол визирования — иначе аппарат проходит выше цели.

На курсе столкновения с неподвижной целью угол визирования обязан СТОЯТЬ.
Замерено 9 сентября 2026: он рос в 16 заходах из 18, медиана +2.13 °/с,
типично с 15° до 41° за восемь секунд. Аппарат сближался почти горизонтально
— рамка росла вдвое, а высота падала на 2-18 м, — при этом нос стоял на цели
с точностью до пары пикселей. Контур наводил НОС, но не управлял ТРАЕКТОРИЕЙ.

Разделение осей здесь принципиальное: тангаж решает, КУДА СМОТРИМ, газ — КУДА
ЛЕТИМ. Нагружать тангаж вторым делом значит вернуть борьбу двух регуляторов
на одной оси.
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

assert zn["LOS_THROTTLE_ENABLED"] is True, "газ по углу визирования выключен"

ZAMER_DPS = 2.13   # замеренная скорость роста на промахе
popravka = zn["LOS_THROTTLE_GAIN"] * (ZAMER_DPS - zn["LOS_RATE_DEADBAND_DPS"])
popravka = min(popravka, zn["LOS_THROTTLE_MAX"])
print("на замеренных %.2f °/с газ убирается на %.0f PWM" % (ZAMER_DPS, popravka))
assert popravka >= 25.0, (
    "поправка %.0f PWM слишком мала, чтобы изменить траекторию" % popravka)
assert popravka <= zn["LOS_THROTTLE_MAX"], "поправка выше собственного потолка"

# Мёртвая зона отсекает шум, но не полезный сигнал.
assert 0.0 < zn["LOS_RATE_DEADBAND_DPS"] < ZAMER_DPS / 3.0, (
    "мёртвая зона %.2f °/с съедает сам сигнал (замерено %.2f)"
    % (zn["LOS_RATE_DEADBAND_DPS"], ZAMER_DPS))

# Фильтр: ниже раскачки, но быстрее геометрии захода.
tau_s = -1.0 / (zn["NOMINAL_FPS"] * math.log(1.0 - zn["LOS_RATE_ALPHA"]))
srez = 1.0 / (2.0 * math.pi * tau_s)
print("срез фильтра угла %.2f Гц (постоянная %.2f с)" % (srez, tau_s))
assert srez < 1.3 / 2.0, (
    "срез %.2f Гц не ниже раскачки 1.3 Гц: газ пойдёт за колебанием" % srez)
assert srez > 0.15, (
    "срез %.2f Гц слишком низкий: газ не успеет за геометрией захода" % srez)

# Знак. Угол растёт -> пройдём выше -> надо снижаться -> газ УБРАТЬ.
assert "thr_adjust = -LOS_THROTTLE_GAIN * _izbytok" in src, (
    "знак поправки газа не тот: при растущем угле газ обязан убираться")

# Закон по времени до контакта остаётся запасным, а не соперником.
i_los = src.index("elif LOS_THROTTLE_ENABLED")
i_tau = src.index("elif THROTTLE_BY_TAU:")
assert i_los < i_tau, (
    "закон по времени до контакта стоит раньше — угол визирования до газа не "
    "дойдёт, а именно он меряет промах")

# Признак промаха обязан быть виден в логе.
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
assert "los_rate" in header.rstrip(",").split(","), (
    "скорость изменения угла не пишется в лог — промах нечем будет разобрать")

# Состояние сбрасывается между локами: разность через паузу даст выброс.
assert src.count("_los_ugol = None") >= 2, (
    "угол визирования не сбрасывается при потере управления")
print("газ по углу визирования: мёртвая зона %.1f °/с, %.0f PWM на (°/с), "
      "потолок %.0f" % (zn["LOS_RATE_DEADBAND_DPS"], zn["LOS_THROTTLE_GAIN"],
                        zn["LOS_THROTTLE_MAX"]))
