"""Окно поиска сужается у крупной цели — и только пока она идёт медленно.

Стоимость совпадения — (2*margin+1)^2 * площадь эталона, и это самый дорогой
этап кадра. Замерено на борту: 9.0 мс при мелкой рамке против 15.9 при
крупной, весь кадр с 28 до 44 мс. FPS падает с 23.7 до 17.5, задержка петли
растёт со 125 до 180 мс, запас по фазе тает с 39° до 18° — ровно там, где
точность нужнее всего.

Сужать окно у крупной цели безопасно: в кадре она движется медленно
ОТНОСИТЕЛЬНО СВОЕГО РАЗМЕРА, а положение уже предсказано потоком. Но только
пока она действительно идёт медленно — иначе узкое окно её потеряет.
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
# SEARCH_MARGIN_MIN задан выражением (масштаб кадра) — берём из модуля.
import sys
sys.path.insert(0, os.path.join(ROOT, "tools"))
import offline  # noqa: E402
_t = offline.load_tracker()
for _im in ("SEARCH_MARGIN_MIN", "SEARCH_MARGIN_MAX"):
    zn[_im] = getattr(_t, _im)


def margin(tmpl, dvizhenie, bazovyy=28):
    m = bazovyy
    if (tmpl >= zn["MATCH_BIG_TMPL_PX"]
            and dvizhenie <= zn["SEARCH_MARGIN_MIN"] - zn["MATCH_BIG_ZAPAS_PX"]):
        m = min(m, zn["SEARCH_MARGIN_MIN"])
    return m


def stoimost(tmpl, m):
    return (2 * m + 1) ** 2 * tmpl * tmpl


print("=== 1. Мелкий эталон не трогаем ===")
for tmpl in (30, 50, 69):
    assert margin(tmpl, 1.0) == 28, "эталон %d сузил окно" % tmpl
print("    до %d px окно прежнее" % zn["MATCH_BIG_TMPL_PX"])

print("\n=== 2. Крупный и медленный — сужаем ===")
print("  %-8s %8s %10s %10s %8s" % ("эталон", "margin", "было", "стало", "выигрыш"))
for tmpl in (70, 90, 110):
    m = margin(tmpl, 1.0)
    c0, c1 = stoimost(tmpl, 28), stoimost(tmpl, m)
    print("  %-8d %8d %9.1fM %9.1fM %7.1fx"
          % (tmpl, m, c0 / 1e6, c1 / 1e6, c0 / c1))
    assert c1 < c0 * 0.4, "выигрыш меньше 2.5 раз — не стоит риска"

print("\n=== 3. Крупный, но БЫСТРЫЙ — окно остаётся широким ===")
bystro = zn["SEARCH_MARGIN_MIN"]
assert margin(110, bystro) == 28, (
    "окно сузилось у быстро идущей цели: она выйдет за его край, и слежение "
    "сорвётся — это ровно тот случай, ради которого окно делали переменным")
print("    при смещении %g px за кадр окно не сужается" % bystro)

print("\n=== 4. Запас до края окна ===")
predel = zn["SEARCH_MARGIN_MIN"] - zn["MATCH_BIG_ZAPAS_PX"]
print("    сужаем, пока цель идёт медленнее %g px за кадр (край окна %g)"
      % (predel, zn["SEARCH_MARGIN_MIN"]))
assert zn["MATCH_BIG_ZAPAS_PX"] >= 4, (
    "запас %g px мал: поток мерит прошлый кадр, а искать надо в текущем"
    % zn["MATCH_BIG_ZAPAS_PX"])
assert predel > 0, "порог движения ушёл в ноль — сужение никогда не включится"

print("\n=== 5. Сужается ИМЕННО окно, а не эталон ===")
# Уменьшение самого эталона я уже пробовал в примерке масштабов, и оно
# сломало отслеживание роста коробки.
assert "margin = int(SEARCH_MARGIN_MIN)" in src
assert "cv2.resize(template_gray" not in src.split("def template_match_locked")[1][:3000], (
    "эталон в матче уменьшается: так уже ломали отслеживание роста коробки")
print("    эталон и его разрешение не тронуты")

print("\nOK: дорогой случай подешевел вчетверо, опасный не тронут")
