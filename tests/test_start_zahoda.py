"""Первая секунда захвата не должна ничем швырять.

ТРИ ОШИБКИ СОШЛИСЬ ЗДЕСЬ, и все видны в одном логе.

  1. Время до контакта в первые кадры — мусор: окно подгонки не набралось,
     шесть точек за четверть секунды дают наклон по шуму. В логе оно шло
     0.126 -> 0.192 -> 0.268 -> 1.549, то есть РОСЛО, хотя обязано убывать.

  2. По этому мусору срабатывала заморозка (tau <= 1.5 с), и команда замирала
     в первую же секунду захода: cmd_pitch = 1566 двенадцать кадров подряд.

  3. При неизвестном времени наклон получал ПОЛНЫЙ вес — 120 PWM в упор с
     первого кадра. Пилот увидел это как «швыряет вверх, будто ускорение,
     которое мы отключили».
"""
import ast
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
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

print("=== 1. Окно оценки времени заполняется по ВРЕМЕНИ ===")
assert "TAU_FIT_MIN_SPAN_S" in zn, (
    "нет требования к размаху окна: шесть точек за четверть секунды снова "
    "пройдут как полноценная оценка")
span = zn["TAU_FIT_MIN_SPAN_S"]
okno = zn["TAU_FIT_WINDOW_S"]
print("    размах не меньше %.1f с при окне %.1f с (%.0f%% заполнения)"
      % (span, okno, 100.0 * span / okno))
assert span >= okno * 0.5, (
    "размах %.1f с — меньше половины окна, наклон будет по шуму" % span)
assert span < okno, "размах не может быть больше самого окна"
assert "_razmah >= TAU_FIT_MIN_SPAN_S" in src, "проверка размаха не применяется"

print("\n=== 2. Заморозка требует подтверждения (по ВРЕМЕНИ, не по кадрам) ===")
assert "_vyderzhka_final" in src and "_vyderzhka_gotova(" in src, (
    "заморозка больше не подтверждается выдержкой — включится с одной оценки")
assert "final_pora = _final_zamorozhen or _final_podtverzhdeno" in src, (
    "решение о финале не опирается на подтверждённую выдержку")
assert "FINAL_CONFIRM_TIME_S" in zn, "нет времени подтверждения финала"
t_final = zn["FINAL_CONFIRM_TIME_S"]
print("    подтверждение %.2f с непрерывно (монотонное время, не зависит от FPS)"
      % t_final)
assert t_final >= 0.15, (
    "выдержка финала %.2f с слишком коротка — одиночный выброс пройдёт" % t_final)

print("\n=== 3. Неизвестное время НЕ даёт полный наклон ===")
i = src.index("if _tau_now is None:")
kusok = src[i:i + 1400]
assert "ves = 0.0" in kusok.split("elif")[0], (
    "при неизвестном времени наклон снова получает вес: сразу после захвата "
    "времени нет всегда, и наклон уйдёт в упор с первого кадра")
print("    при неизвестном времени вес наклона 0")

print("\n=== 4. Вес наклона не может шагнуть ===")
assert "GLIDE_SLEW_S" in zn, "вес наклона переключается скачком"
za_kadr = zn["GLIDE_MAX_PWM"] * (1.0 / zn["CAM_FPS"]) / zn["GLIDE_SLEW_S"]
print("    полный ход за %.1f с -> %.1f PWM за кадр"
      % (zn["GLIDE_SLEW_S"], za_kadr))
assert za_kadr <= 8.0, (
    "%.1f PWM за кадр — это всё ещё ступенька" % za_kadr)
assert zn["GLIDE_SLEW_S"] <= 4.0, "наклон не успеет за затянутым заходом"

print("\n=== 5. Состояние чистится между заходами ===")
for imya in ("_glide_ves_tek = 0.0", "_los_aim_tek = 0.0"):
    assert src.count(imya) >= 2, (
        "%s не сбрасывается при потере цели: следующий заход начнётся с "
        "накопленным состоянием прошлого" % imya.split()[0])
assert "_vyderzhka_sbros(_vyderzhka_final)" in src, (
    "выдержка финала не сбрасывается при потере цели: следующий заход "
    "начнётся с накопленным временем прошлого")
print("    вес наклона, выдержка финала и поправка прицела сбрасываются")

print("\nOK: первая секунда захвата ничем не швыряет")
