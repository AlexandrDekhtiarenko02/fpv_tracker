"""Наклон только добавляет пикирование, никогда не отнимает.

Замерено на стенде 9 сентября: при наклоне носа 32° угловой контур честно
тянул обратно к своей уставке 15°, выдавая -120 в насыщении весь заход.
Он работал правильно и делал не то: прицельный PID опускал нос к цели, а
наклон в те же кадры поднимал его. Ровно та борьба двух регуляторов на одной
оси, из-за которой убрали таймерный разгон.

Здесь же проверяется, что наклон не тянет за собой чужой буст газа: пока он
писал свой вес в launch_intensity, срабатывал буст таймерного разгона и молча
добавлял 10% газа. В логе это выглядело как cmd_thr на 131 выше base_thr при
thr_adjust = 0.
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

# 1. Отсечка снизу нулём. Нос вниз = PWM выше 1500, значит отрицательный
#    вклад означал бы «поднять нос» — этого наклон делать не вправе.
assert "if launch_pitch_pwm < 0.0:" in src, (
    "нет отсечки: наклон снова сможет поднимать нос против прицельного PID")
assert "elif launch_pitch_pwm > GLIDE_MAX_PWM:" in src, "пропал верхний предел"
assert zn["PITCH_SIGN"] == +1, (
    "знак изменён — отсечка снизу нулём верна только когда нос вниз = "
    "положительный PWM")

# 2. Буст газа принадлежит таймерному разгону, а не наклону.
assert "OVERRIDE_THROTTLE and LAUNCH_ENABLED and launch_intensity" in src, (
    "буст газа не привязан к LAUNCH_ENABLED: наклон снова потянет его за "
    "собой и добавит 10% газа почти весь заход")

# 3. Вес наклона живёт в своей переменной и в своей колонке лога.
assert "glide_ves" in src, "вес наклона снова пишется в launch_intensity"
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
cols = header.rstrip(",").split(",")
assert "glide_ves" in cols, "вес наклона не виден в логе"
assert "launch_int" in cols, "пропала колонка таймерного разгона"

# 4. Режим сближения требует подтверждения: на стенде время до контакта
#    выдало 0.61 с там, где ничего не сближалось.
assert zn["CLOSING_CONFIRM_FRAMES"] >= 3, (
    "выдержка меньше трёх кадров одиночный выброс не отсечёт")
assert "_closing_schet >= CLOSING_CONFIRM_FRAMES" in src, (
    "режим сближения снова включается с первого кадра")

print("наклон только вниз, буст газа отвязан, режим сближения с выдержкой %d "
      "кадров" % zn["CLOSING_CONFIRM_FRAMES"])
