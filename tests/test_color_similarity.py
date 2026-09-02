"""Цветовая картинка должна выделять ЦВЕТ ЦЕЛИ, а не «всё, что не фон».

СЛУЧАЙ ОПЕРАТОРА: ярко-зелёная цель на ярко-синем фоне — и трекер ушёл на
серую стену.

Причина была в том, что картинка строилась ПРОЕКЦИЕЙ вдоль оси «цель минус
фон». Такая мера одномерна: она отвечает на вопрос «насколько далеко от фона»,
а не «похоже ли на цель». Всё, что отличается от фона в ту же сторону,
выглядело как цель:

    зелёная цель  255      серый стенд  251     НЕОТЛИЧИМЫ

Теперь считается БЛИЗОСТЬ К ЦВЕТУ ЦЕЛИ. Тогда и синий фон, и серая стена
одинаково далеки от зелёного, а ярче всех только сама цель.
"""
import io, os, re, math
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "math": math}
ns["TRACK_ON_COLOR_SCALE"] = eval(
    re.search(r"^TRACK_ON_COLOR_SCALE = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
exec(re.search(r"^def color_projection.*?(?=\n\ndef )", src, re.S | re.M).group(0), ns)


def uv(r, g, b):
    return (128 - 0.169 * r - 0.331 * g + 0.5 * b,
            128 + 0.5 * r - 0.419 * g - 0.081 * b)


GREEN = uv(40, 200, 60)     # цель
BLUE = uv(40, 70, 210)      # фон, на котором её вели
GREY = uv(140, 140, 140)    # стена, на которую уходил трекер
ORANGE = uv(230, 120, 40)

H, W = 60, 80
u = np.full((H, W), BLUE[0], np.float32)
v = np.full((H, W), BLUE[1], np.float32)
u[10:20, 10:20], v[10:20, 10:20] = GREEN                 # цель
u[10:20, 40:50], v[10:20, 40:50] = GREY                  # серая стена
u[35:45, 40:50], v[35:45, 40:50] = ORANGE                # посторонний предмет
ns["chroma_u"] = np.clip(u, 0, 255).astype(np.uint8)
ns["chroma_v"] = np.clip(v, 0, 255).astype(np.uint8)

du = GREEN[0] - BLUE[0]
dv = GREEN[1] - BLUE[1]
sep = math.hypot(du, dv)
ns["color_axis"] = (du / sep, dv / sep, BLUE[0], BLUE[1], 5.0, GREEN[0], GREEN[1])

img = ns["color_projection"]((H, W))
assert img is not None, "картинка не построилась"


def area(y0, y1, x0, x1):
    return float(img[y0:y1, x0:x1].mean())


tgt = area(12, 18, 12, 18)
grey = area(12, 18, 42, 48)
blue = area(45, 55, 5, 25)
orange = area(37, 43, 42, 48)

print("=== Яркость в цветовой картинке ===")
for n, val in (("ЦЕЛЬ (зелёная)", tgt), ("серая стена", grey),
               ("синий фон", blue), ("оранжевый предмет", orange)):
    print("    %-20s %6.0f" % (n, val))

print("\n=== 1. Цель ярче всего остального с запасом ===")
for n, val in (("серая стена", grey), ("синий фон", blue),
               ("оранжевый предмет", orange)):
    print("    цель %.0f против «%s» %.0f -> запас %.0f" % (tgt, n, val, tgt - val))
    assert tgt > val + 60, (
        "цель отличается от «%s» всего на %.0f — так трекер и уходил на стену"
        % (n, tgt - val))

print("\n=== 2. Фон и посторонние предметы одинаково темны ===")
assert max(grey, blue, orange) < 150, (
    "что-то из фона осталось ярким: %.0f" % max(grey, blue, orange))

print("\nOK: выделяется цвет цели, а не «всё, что не фон»")
