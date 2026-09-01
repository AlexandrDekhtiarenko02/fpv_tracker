"""Цветовой маяк: возврат рамки к цели, когда её нет в окне поиска."""
import io, os, re, sys, types
import numpy as np, cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2}
ns["HIRES_TRACKING"] = eval(re.search(r"^HIRES_TRACKING = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
for name in ("TRACK_SCALE", "COLOR_REF_DIST", "COLOR_RECOVER_MAX_JUMP",
             "COLOR_RECOVER_GOOD", "COLOR_RECOVER_WRONG", "COLOR_RECOVER_AFTER"):
    m = re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M)
    ns[name] = eval(m.group(1), dict(ns))
fn = re.search(r"^def find_color_beacon.*?(?=\n\ndef )", src, re.S | re.M).group(0)
exec(fn, ns)

CH, CW = 240, 320                       # цветность = половина кадра 640x480
u = np.full((CH, CW), 127, np.uint8)
v = np.full((CH, CW), 140, np.uint8)    # фон: коричневый, почти нейтраль
TU, TV = 90, 200                        # цель: насыщенный оранжевый
# цель в ЯРКОСТНЫХ координатах (300, 200) -> цветность (150, 100)
u[92:108, 142:158] = TU
v[92:108, 142:158] = TV
ns["chroma_u"], ns["chroma_v"] = u, v
ns["target_uv"] = (TU, TV)

print("=== 1. Рамка сползла на 60 px — маяк обязан вернуть её на цель ===")
r = ns["find_color_beacon"](360.0, 200.0, 16, 16)
assert r is not None, "маяк ничего не нашёл"
bx, by, val = r
print("  найдено (%.0f, %.0f), совпадение %.2f; цель в (300, 200)" % (bx, by, val))
assert abs(bx - 300) <= 8 and abs(by - 200) <= 8, "маяк промахнулся"
assert val <= ns["COLOR_RECOVER_GOOD"], "совпадение %.2f не прошло порог" % val

print("\n=== 2. Далёкий предмет того же цвета — не телепортируемся ===")
u2, v2 = u.copy(), v.copy()
u2[92:108, 142:158] = 127            # убрали цель
v2[92:108, 142:158] = 140
u2[20:36, 20:36] = TU                # такой же оранжевый в дальнем углу
v2[20:36, 20:36] = TV
ns["chroma_u"], ns["chroma_v"] = u2, v2
r2 = ns["find_color_beacon"](600.0, 440.0, 16, 16)
far = (r2 is None) or (r2[2] > ns["COLOR_RECOVER_GOOD"])
print("  результат: %s" % ("возврата не будет" if far else "ПРЫЖОК на (%.0f,%.0f)" % r2[:2]))
assert far, "маяк утащил рамку через весь кадр на посторонний предмет"

print("\n=== 3. Цели нужного цвета нет вовсе — молчим ===")
ns["chroma_u"] = np.full((CH, CW), 127, np.uint8)
ns["chroma_v"] = np.full((CH, CW), 140, np.uint8)
r3 = ns["find_color_beacon"](300.0, 200.0, 16, 16)
print("  совпадение %.2f (порог %.2f)" % (r3[2], ns["COLOR_RECOVER_GOOD"]))
assert r3[2] > ns["COLOR_RECOVER_GOOD"], "принял фон за цель"

print("\n=== 4. Нет подписи цвета — маяк не работает ===")
ns["target_uv"] = None
assert ns["find_color_beacon"](300.0, 200.0, 16, 16) is None
print("  вернул None")

print("\nOK: маяк возвращает рамку к цели и не прыгает на чужие предметы")
