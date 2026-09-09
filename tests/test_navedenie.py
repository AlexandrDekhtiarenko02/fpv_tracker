"""Закон наведения: постоянный пеленг, а не погоня.

Отклик на ПОЛОЖЕНИЕ цели в кадре — чистая погоня: аппарат непрерывно
доворачивает на цель и идёт по дуге, приходя с большим углом и промахиваясь.
Отклик на СНОС цели по кадру — наведение по постоянному пеленгу: снос равен
нулю ровно тогда, когда мы на курсе столкновения, и путь выходит прямым.

Замерено 9 сентября 2026 на заходах с управлением: вклад положения в команду
тангажа 10.1 PWM по медиане, вклад сноса 5.0 — погоня перевешивала вдвое.
Здесь проверяется, что перевес на стороне сноса.
"""
import ast
import io
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

assert zn["LEAD_AIM_ENABLED"] is True, "упреждение выключено — останется погоня"

# Замеренные величины на заходах 9 сентября.
POZ_PX = 5.6          # медиана |ady|
SNOS_PX_KADR = 0.88   # медиана |tgt_vy|

for os_imya, p_gain, ff_gain in (("тангаж", zn["P_GAIN_PITCH"], zn["FF_GAIN_PITCH"]),
                                 ("крен", zn["P_GAIN_ROLL"], zn["FF_GAIN_ROLL"])):
    vklad_poz = POZ_PX * p_gain
    vklad_snos = SNOS_PX_KADR * (p_gain * zn["LEAD_FRAMES"] + ff_gain)
    otn = vklad_snos / vklad_poz
    print("%-7s положение %5.1f PWM, снос %5.1f PWM, отношение %.2f"
          % (os_imya, vklad_poz, vklad_snos, otn))
    assert otn >= 1.0, (
        "%s: снос слабее положения (%.2f) — заход останется по дуге" % (os_imya, otn))
    # И не наоборот: снос считается разностью по кадрам и шумит.
    assert otn <= 4.0, (
        "%s: снос перевешивает положение в %.1f раза — контур пойдёт за "
        "шумом разности кадров, а цель уползёт из кадра" % (os_imya, otn))

# Упреждение горизонтом больше полусекунды при задержке петли 125 мс — это
# уже не упреждение, а экстраполяция в пустоту.
gorizont_s = zn["LEAD_FRAMES"] / zn["NOMINAL_FPS"]
print("горизонт упреждения %.2f с при задержке петли ~0.125 с" % gorizont_s)
assert 0.1 <= gorizont_s <= 0.5, (
    "горизонт %.2f с вне разумного" % gorizont_s)

# Фильтр на скорость обязателен: снос — разность соседних кадров.
assert zn["LEAD_VEL_ALPHA"] <= 0.4, (
    "фильтр скорости слишком быстрый: при таком весе сноса контур пойдёт за "
    "пиксельным шумом")
# LEAD_MAX_PX задан выражением (масштаб кадра), литералом не читается —
# проверяем сам факт потолка.
assert "LEAD_MAX_PX" in src and "lead_x = LEAD_MAX_PX" in src, (
    "пропал потолок упреждения: при шумном видении прицел уедет на полкадра")
print("фильтр скорости alpha=%.2f, потолок упреждения на месте"
      % zn["LEAD_VEL_ALPHA"])
