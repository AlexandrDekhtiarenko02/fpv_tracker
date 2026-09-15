"""Демпфирование по гироскопу и запас устойчивости контура тангажа.

Замерено 9 сентября 2026 по полётам с управлением: раскачка 1.8-2.5 Гц,
амплитуда ошибки росла с 10 px до 90 px за восемь секунд. Причина
структурная, а не в подборе чисел: в ACRO команда задаёт угловую СКОРОСТЬ, а
ошибка прицела — это УГОЛ, то есть объект управления интегратор (90° фазы).
Сверху задержка канала: пик взаимной корреляции «команда -> отклик
гироскопа» на 42-83 мс плюс два кадра на отправку, всего около 125 мс. На
2.2 Гц это 99°, вместе 189° — за границей устойчивости.
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

assert zn["PITCH_RATE_DAMP_ENABLED"] is True, "демпфирование тангажа выключено"
assert zn["ROLL_RATE_DAMP_ENABLED"] is True, (
    "демпфирование крена выключено: крен раскачивался сильнее тангажа")

# Масштаб гироскопа — замеренный, не выдуманный.
assert 0.05 <= zn["GYRO_UNIT_DPS"] <= 0.12, (
    "масштаб %.3f вне замеренного: 0.078 (°/с) на единицу"
    % zn["GYRO_UNIT_DPS"])

# Демпфирование мягче единичного внутреннего контура. Аппарат даёт около
# 1.4 (°/с) на единицу PWM, значит единичный контур — 0.7 PWM на (°/с).
edinichnyy = 0.7
assert 0.0 < zn["ROLL_RATE_DAMP"] < edinichnyy, (
    "демпфирование крена %.2f не мягче единичного внутреннего контура"
    % zn["ROLL_RATE_DAMP"])
assert zn["ROLL_RATE_DAMP_MAX"] < zn["MAX_ROLL_DEFLECT"], (
    "потолок демпфирования крена не ниже полного отклонения оси")
assert 0.0 < zn["PITCH_RATE_DAMP"] < edinichnyy, (
    "%.2f PWM на (°/с) — не мягче единичного внутреннего контура (%.2f); "
    "такой контур сам станет источником колебаний"
    % (zn["PITCH_RATE_DAMP"], edinichnyy))

# Демпфирование поправляет прицеливание, а не подменяет его.
assert zn["PITCH_RATE_DAMP_MAX"] < zn["MAX_PITCH_DEFLECT"], (
    "потолок демпфирования не ниже полного отклонения оси")

# Несвежий гироскоп добавляет фазы вместо того, чтобы возвращать её.
assert "now_mono - _g_ts) <= GYRO_FRESH_S" in src, (
    "нет проверки свежести гироскопа: запоздалая поправка раскачивает")
assert zn["GYRO_FRESH_S"] <= 0.2, (
    "окно свежести %.2f с слишком широкое: замеренный возраст 67 мс, "
    "90%% ниже 126 мс" % zn["GYRO_FRESH_S"])

# Запас по фазе. Снижение P опускает частоту среза, а с ней и потерю фазы
# на задержке. Прежние 3.5 давали срез 2.2 Гц и 189° — неустойчиво.
zaderzhka_s = 0.125
srez_prezhde = 2.2
srez = srez_prezhde * zn["P_GAIN_PITCH"] / 3.5
# И отдельно: усиление не должно просить больше, чем выход способен выдать.
# Избыток срезается ограничителем и остаётся ступенькой.
OSHIBKA_90_PX = 15.9      # замеренный 90-й процентиль ошибки прицела
D_SK, DAMP_SK = 5.7, 13.9  # замеренные скачки прочих слагаемых
import math as _m2
spros = _m2.sqrt((OSHIBKA_90_PX * zn["P_GAIN_PITCH"]) ** 2
                 + D_SK ** 2 + DAMP_SK ** 2)
predel = min(zn["PITCH_SLEW_PWM_PER_S"] / zn["CAM_FPS"],
             zn["PITCH_SLEW_MAX_STEP"])
print("запрос на 90%% ~%.0f PWM при пределе оси %.0f" % (spros, predel))
assert spros <= predel * 1.1, (
    "усиление просит %.0f при пределе %.0f: избыток срежется и останется "
    "ступенькой — это не усиление, а рывок" % (spros, predel))
faza = 90.0 + 360.0 * zaderzhka_s * srez
print("P=%.1f -> частота среза ~%.1f Гц, фаза ~%.0f°, запас ~%.0f°"
      % (zn["P_GAIN_PITCH"], srez, faza, 180.0 - faza))
assert faza < 155.0, (
    "запас по фазе %.0f° — меньше 25°, контур снова будет расшатываться"
    % (180.0 - faza))

# Вклад демпфирования в логе видно отдельно.
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
assert "rate_damp" in header.rstrip(",").split(","), (
    "вклад демпфирования не виден в логе — его размер нечем проверить")
print("демпфирование %.2f PWM/(°/с), потолок %.0f, окно свежести %.0f мс"
      % (zn["PITCH_RATE_DAMP"], zn["PITCH_RATE_DAMP_MAX"],
         zn["GYRO_FRESH_S"] * 1000))
