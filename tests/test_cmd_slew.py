"""Команда прицельных осей не может шагнуть рывком.

Замерено на стенде 9 сентября: команда тангажа шагала с 1640 на 1100 за один
кадр — 540 PWM за 42 мс, — и аппарат отвечал резким рывком носом вверх. У газа
сглаживание выхода было с самого начала, у крена, тангажа и рыскания — нет.

Ограничение стоит на СКОРОСТИ, а не на величине: авторитет остаётся полным,
до любого края команда доходит за треть секунды.
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

assert zn["CMD_SLEW_ENABLED"] is True, "ограничение скорости выключено"
skorost = zn["CMD_SLEW_PWM_PER_S"]
fps = zn["CAM_FPS"]
za_kadr = skorost / fps
do_kraya = max(zn["MAX_ROLL_DEFLECT"], zn["MAX_PITCH_DEFLECT"]) / skorost
print("предел %g PWM/с = %.0f PWM за кадр при %g к/с" % (skorost, za_kadr, fps))
print("от центра до края: %.2f с" % do_kraya)

# Достаточно медленно, чтобы убрать ступеньку.
assert za_kadr <= 120, (
    "%.0f PWM за кадр — это всё ещё ступенька, ради которой всё затевалось"
    % za_kadr)
# И достаточно быстро, чтобы не потерять авторитет в финале захода.
assert do_kraya <= 0.8, (
    "до полного отклонения %.2f с — слишком вяло для последних секунд захода"
    % do_kraya)

# Ограничитель обязан стоять ПОСЛЕ насыщения: сначала куда, потом как быстро.
i_deflect = src.index("target_yaw = max(1000, min(2000, 1500 + yaw_offset))")
i_slew = src.index("if CMD_SLEW_ENABLED:")
assert i_slew > i_deflect, (
    "ограничитель стоит до насыщения — предел отклонения перестанет работать")

# Состояние обязано сбрасываться, когда трекер не управляет.
assert "_slew_roll = _slew_pitch = _slew_yaw = 1500.0" in src, (
    "состояние ограничителя не сбрасывается: новый лок начнёт подъезжать от "
    "насыщенной команды предыдущего")

# Шаг считается от длительности кадра, иначе предел зависел бы от частоты.
assert "_dolya = k / NOMINAL_FPS" in src, (
    "шаг не привязан к длительности кадра")

print("\n=== ДЛИННЫЙ КАДР НЕ ДАЁТ ПРАВА НА СТУПЕНЬКУ ===")
# Здесь жили оставшиеся рывки, и нашлись они только сравнением логов между
# версиями: обычный скачок команды удалось срезать с 46 до 5 PWM, а 99-й
# процентиль как был 60-90, так и остался. Все прежние правки меняли ВХОДЫ,
# а беда была в самом ограничителе: допуск рос вместе с длительностью кадра.
assert "CMD_SLEW_MAX_STEP" in zn, (
    "нет абсолютного предела шага: длинный кадр снова разрешит ступеньку")
NOM = zn["CMD_SLEW_PWM_PER_S"] / zn["NOMINAL_FPS"] * (zn["NOMINAL_FPS"] / zn["CAM_FPS"])
print("  %-10s %6s %10s %10s" % ("dt мс", "k", "было", "стало"))
for dt_ms, zamer in ((42, None), (60, None), (81, 95), (90, 107), (94, 113)):
    k = (dt_ms / 1000.0) * zn["NOMINAL_FPS"]
    bylo = zn["CMD_SLEW_PWM_PER_S"] * k / zn["NOMINAL_FPS"]
    stalo = min(bylo, zn["CMD_SLEW_MAX_STEP"])
    print("  %-10d %6.2f %10.0f %10.0f%s"
          % (dt_ms, k, bylo, stalo,
             ("   (в логе скачок %d)" % zamer) if zamer else ""))
    if zamer:
        assert abs(bylo - zamer) < 12, (
            "расчёт не сходится с замером: при dt=%d допуск %.0f, а в логе "
            "скачок %d — значит причина не та" % (dt_ms, bylo, zamer))
        assert stalo <= zn["CMD_SLEW_MAX_STEP"]
# На ровном ходу предел не вмешивается.
k_rovno = (1.0 / zn["CAM_FPS"]) * zn["NOMINAL_FPS"]
shag_rovno = zn["CMD_SLEW_PWM_PER_S"] * k_rovno / zn["NOMINAL_FPS"]
assert shag_rovno <= zn["CMD_SLEW_MAX_STEP"], (
    "абсолютный предел (%.0f) ниже шага на ровном ходу (%.0f) — ограничитель "
    "станет резать там, где резать нечего"
    % (zn["CMD_SLEW_MAX_STEP"], shag_rovno))
print("  на ровном ходу шаг %.0f, предел %.0f — не вмешивается"
      % (shag_rovno, zn["CMD_SLEW_MAX_STEP"]))
assert zn["CMD_SLEW_MAX_STEP"] < 70, (
    "предел %.0f всё ещё пропускает ступеньку" % zn["CMD_SLEW_MAX_STEP"])
assert "min(CMD_SLEW_PWM_PER_S * _dolya, CMD_SLEW_MAX_STEP)" in src, (
    "абсолютный предел не применяется")

print("\n=== ТАНГАЖ ВЕДЁТСЯ МЯГЧЕ ОСТАЛЬНЫХ ОСЕЙ ===")
# После абсолютного ограничения ступеньки исчезли (максимум 113 -> 55), но
# 10% кадров всё ещё просили больше 36 PWM — это и ощущается как «прыгает».
shag_p = min(zn["PITCH_SLEW_PWM_PER_S"] / zn["CAM_FPS"], zn["PITCH_SLEW_MAX_STEP"])
shag_r = min(zn["CMD_SLEW_PWM_PER_S"] / zn["CAM_FPS"], zn["CMD_SLEW_MAX_STEP"])
print("  тангаж        %5.1f PWM за кадр, до края за %.2f с"
      % (shag_p, zn["MAX_PITCH_DEFLECT"] / (shag_p * zn["CAM_FPS"])))
print("  крен/рыскание %5.1f PWM за кадр, до края за %.2f с"
      % (shag_r, zn["MAX_ROLL_DEFLECT"] / (shag_r * zn["CAM_FPS"])))
assert shag_p < shag_r, "тангаж не мягче остальных осей"
ZAMER_90 = 36.0   # 90-й процентиль скачка по замеру
assert shag_p < ZAMER_90, (
    "предел %.0f выше замеренного 90-го процентиля (%.0f) — смягчать нечего"
    % (shag_p, ZAMER_90))
# Но и не настолько мягко, чтобы контур перестал успевать в финале.
do_kraya = zn["MAX_PITCH_DEFLECT"] / (shag_p * zn["CAM_FPS"])
assert do_kraya <= 0.6, (
    "до полного отклонения %.2f с — в последние секунды захода это уже поздно"
    % do_kraya)
assert "_slew_pitch = _ogranich_skorost(_slew_pitch, target_pitch, shag_p)" in src, (
    "тангаж использует общий предел, а не свой")

print("\n=== СМЯГЧАЕМ ОГРАНИЧИТЕЛЕМ, А НЕ СГЛАЖИВАНИЕМ ===")
# Ограничитель нелинеен: на мелких движениях не работает и фазы не съедает.
# Сглаживание запаздывает всегда, и при запасе в 39° фильтр с постоянной
# 0.12 с забрал бы его целиком — вместо мягкости вышла бы раскачка.
_telo = src[src.index("if CMD_SLEW_ENABLED:"):]
_telo = _telo[:3000]
assert "alpha_for_dt" not in _telo, (
    "на прицельные оси добавлено сглаживание: оно запаздывает всегда и "
    "съест запас по фазе, дав раскачку вместо мягкости")
print("  сглаживания на прицельных осях нет — только ограничение скорости")
print("ограничитель на месте, после насыщения, со сбросом")
