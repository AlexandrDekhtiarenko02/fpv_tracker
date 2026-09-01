"""Пересчёт эталона по масштабу не должен отбрасывать накопленный вид цели.

Эталон нужного масштаба получается пересчётом из ИСХОДНОГО — только так
удаётся не размывать его накопленными интерполяциями (пересчёт по цепочке уже
губил эталон, резкость падала в двести раз).

Но такой пересчёт каждый раз возвращает вид, который был ПРИ ЗАХВАТЕ. Свет
поменялся, ракурс поехал — а эталон прежний. На однотонном фоне это сходит с
рук: совпадать всё равно не с чем. На фактурном устаревший эталон начинает
совпадать с фоном, и слежение портится. Оператор это и заметил, перейдя с
однотонного фона на фактурный.

Здесь проверяется, что вид всё-таки копится: исходный эталон подмешивает
свежие данные и следует за изменением сцены, при этом не мутнея.
"""
import io, os, re
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ALPHA = eval(re.search(r"^TEMPLATE_BASE_ALPHA = (.+?)(?:\s+#.*)?$",
                       src, re.M).group(1))

rng = np.random.default_rng(9)
BASE = np.clip(140 + cv2.GaussianBlur(rng.normal(0, 28, (40, 40)), (0, 0), 1.2) * 3,
               0, 255).astype(np.uint8)


def blend(base, fresh, a):
    return cv2.addWeighted(base, 1.0 - a, fresh, a, 0)


print("=== 1. Эталон следует за изменением сцены ===")
# Сцена постепенно темнеет — так меняется свет на заходе.
base = BASE.copy()
for step in range(40):
    fresh = np.clip(BASE.astype(np.float32) - 40, 0, 255).astype(np.uint8)
    base = blend(base, fresh, ALPHA)
d0 = float(np.mean(BASE)) - float(np.mean(BASE.astype(np.float32) - 40))
d1 = float(np.mean(base)) - float(np.mean(BASE.astype(np.float32) - 40))
print("    расхождение со сценой: было %.1f, стало %.1f" % (d0, d1))
assert d1 < d0 * 0.2, "эталон не догнал изменение сцены"

print("\n=== 2. И при этом НЕ мутнеет ===")
# Ключевое отличие от пересчёта по цепочке: подмешиваются свежие данные,
# а не результат предыдущего пересчёта.
def sharp(img):
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


base2 = BASE.copy()
for step in range(60):
    base2 = blend(base2, BASE, ALPHA)          # свежие данные каждый раз
chain = BASE.copy()
for step in range(60):                          # а так делать нельзя
    chain = cv2.resize(cv2.resize(chain, (28, 28)), (40, 40))
print("    резкость: исходная %.0f | с подмешиванием свежего %.0f | пересчёт по цепочке %.0f"
      % (sharp(BASE), sharp(base2), sharp(chain)))
assert sharp(base2) > sharp(BASE) * 0.8, "подмешивание размыло эталон"
assert sharp(chain) < sharp(BASE) * 0.2, "модель цепочки не воспроизводит потерю резкости"

print("\n=== 3. Скорость адаптации разумная ===")
# Слишком быстро — эталон уедет на фон за несколько кадров. Слишком медленно —
# не догонит смену света.
half = np.log(0.5) / np.log(1.0 - ALPHA)
print("    половина изменения набирается за %.1f пересчётов масштаба" % half)
assert 2.0 <= half <= 12.0, "скорость адаптации вне разумного: %.1f" % half

print("\nOK: вид копится, эталон не мутнеет")
