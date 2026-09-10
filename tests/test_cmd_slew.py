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
assert "CMD_SLEW_PWM_PER_S * (k / NOMINAL_FPS)" in src, (
    "шаг не привязан к длительности кадра")
print("ограничитель на месте, после насыщения, со сбросом")
