"""Видно ли, что борт НЕ принимает команды трекера.

Самая дорогая осечка проверки управления: захват держится, поправки считаются,
в логе цифры — а аппарат летит как летел, потому что MSP OVERRIDE в Betaflight
не поднят. Отличить это от «трекер плохо рулит» после посадки можно только по
паре ov/fc_ovr, а в воздухе — только по строке в кадре. Здесь проверяется, что
и то, и другое на месте.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
assert header is not None, "не нашёл _COLS"
cols = header.rstrip(",").split(",")

# Своя оценка трекера и ответ полётника — две разные колонки. Одной мало:
# override=1 при fc_ovr=0 и есть тот случай, ради которого всё это.
assert "override" in cols, "пропала колонка override"
assert "fc_ovr" in cols, (
    "нет колонки fc_ovr: по логу не отличить «трекер не доворачивал» от "
    "«полётник не принимал команды»")
print("колонки ov/fc_ovr на месте (%d, %d)"
      % (cols.index("override"), cols.index("fc_ovr")))

imena = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
for f in ("draw_control_state", "_preduprezhdenie_ob_overrayde"):
    assert f in imena, "нет %s: в очках не видно, берёт ли борт команды" % f

# Строка обязана рисоваться. Функция, которую никто не зовёт, — это её
# отсутствие, только незаметное.
assert "draw_control_state(frame)" in src, "draw_control_state никто не вызывает"
assert "_preduprezhdenie_ob_overrayde(apply_ov and sent_ok)" in src, (
    "предупреждение не вызывается из потока MSP (или не учитывает, ушёл ли кадр)")

# В режиме наблюдения строки быть не должно: там трекер и не обязан рулить,
# и красная надпись означала бы поломку там, где всё правильно.
telo = [n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "draw_control_state"][0]
istochnik = ast.get_source_segment(src, telo) or ""
assert "if OBSERVE_ONLY:" in istochnik and "return" in istochnik, (
    "draw_control_state не молчит в OBSERVE_ONLY")

# Проверка готовности должна знать про два режима: требовать OBSERVE_ONLY на
# проверке управления — значит писать «не готово» ровно тогда, когда готово.
got = [n for n in ast.walk(tree)
       if isinstance(n, ast.FunctionDef) and n.name == "_gotovnost_k_sboru"][0]
got_src = ast.get_source_segment(src, got) or ""
assert "if OBSERVE_ONLY:" in got_src, (
    "проверка готовности одинакова для сбора и для управления")
assert "ПРОВЕРКА УПРАВЛЕНИЯ" in got_src, "нет отдельного вывода для управления"
print("экранная строка, предупреждение и проверка готовности — на месте")
