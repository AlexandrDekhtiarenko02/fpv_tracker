"""Отсев по цвету обязан помогать там, где цвет различает, и МОЛЧАТЬ там, где нет.

Главное требование к правке: не сделать хуже. Оно обеспечивается тем, что при
захвате измеряется различимость цели и её окружения по цветности, и цвет
включается только если различимость выше порога. Тест проверяет обе стороны.
"""
import io, os, threading
import numpy as np
import cv2

# Путь берём от самого файла теста: жёсткий путь к моей машине делал
# тесты незапускаемыми на малине — а именно там их и нужно прогонять
# после git pull, чтобы убедиться, что приехал рабочий код.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"),
              encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "threading": threading,
      "LORES_H": 240, "LORES_W": 320,
      "COLOR_MIN_TARGET_CHROMA_PX": 3, "COLOR_REF_DIST": 40.0,
      "COLOR_MIN_SEPARATION": 12.0, "COLOR_PENALTY": 0.25,
      "chroma_u": None, "chroma_v": None, "target_uv": None,
      "color_active": False}
for fn, end in (("extract_chroma", "measure_color_separation"),
                ("measure_color_separation", "color_penalty_map"),
                ("color_penalty_map", "build_template")):
    ns_src = src.split("def %s(" % fn)[1].split("\ndef %s" % end)[0]
    exec("def %s(" % fn + ns_src, ns)

H, W = 240, 320


def make_yuv(target_u, target_v, bg_u, bg_v):
    """Кадр YUV420: цель 24x24 в центре на фоне."""
    buf = np.zeros((H * 3 // 2, W), np.uint8)
    buf[:H, :] = 120                                   # яркость роли не играет
    u = np.full((H // 2, W // 2), bg_u, np.uint8)
    v = np.full((H // 2, W // 2), bg_v, np.uint8)
    cy, cx = H // 4, W // 4                            # центр в координатах цветности
    u[cy - 6:cy + 6, cx - 6:cx + 6] = target_u
    v[cy - 6:cy + 6, cx - 6:cx + 6] = target_v
    q = H // 4
    buf[H:H + q, :] = u.reshape(q, W)
    buf[H + q:H + 2 * q, :] = v.reshape(q, W)
    return buf


print("=== 1. Раскладка буфера читается верно ===")
buf = make_yuv(200, 90, 128, 128)
u, v = ns["extract_chroma"](buf)
print("  форма U %s, V %s | цвет цели U=%d V=%d" % (u.shape, v.shape, u[60, 80], v[60, 80]))
assert u.shape == (120, 160) and u[60, 80] == 200 and v[60, 80] == 90

print("\n=== 2. Цветная цель на сером фоне -> цвет ВКЛЮЧАЕТСЯ ===")
ns["chroma_u"], ns["chroma_v"] = u, v
sig, sep = ns["measure_color_separation"](160, 120, 48, 48)
print("  подпись цели %s | различимость %.1f (порог %.1f)" % (
    tuple(round(x) for x in sig), sep, 12.0))
assert sig is not None and sep >= 12.0, "цвет обязан включиться"

print("\n=== 3. Серая цель на сером фоне -> цвет НЕ используется ===")
buf2 = make_yuv(129, 127, 128, 128)
u2, v2 = ns["extract_chroma"](buf2)
ns["chroma_u"], ns["chroma_v"] = u2, v2
sig2, sep2 = ns["measure_color_separation"](160, 120, 48, 48)
print("  различимость %.1f -> цвет %s" % (sep2, "включён" if sep2 >= 12.0 else "ВЫКЛЮЧЕН"))
assert sep2 < 12.0, "на сером фоне цвет обязан остаться выключенным"

print("\n=== 4. Слишком мелкая цель -> цвет НЕ используется ===")
ns["chroma_u"], ns["chroma_v"] = u, v
sig3, sep3 = ns["measure_color_separation"](160, 120, 8, 8)
print("  цель 8 px -> подпись %s" % ("есть" if sig3 else "НЕТ, отказ"))
assert sig3 is None, "у мелкой цели цветных пикселей мало, доверять нельзя"

print("\n=== 5. Штраф: на цели ~0, на фоне ~максимум ===")
ns["chroma_u"], ns["chroma_v"] = u, v
ns["target_uv"] = (200.0, 90.0)
ns["color_active"] = True
pm = ns["color_penalty_map"](160 - 30, 120 - 30, 24, 24, (37, 37))
mid = pm[pm.shape[0] // 2, pm.shape[1] // 2]
edge = pm[0, 0]
print("  штраф в центре (цель) %.3f | по краю (фон) %.3f" % (mid, edge))
assert mid < 0.2 and edge > 0.8, "цвет обязан различать цель и фон"

print("\n=== 6. Цвет выключен -> штрафа нет вовсе ===")
ns["color_active"] = False
assert ns["color_penalty_map"](130, 90, 24, 24, (37, 37)) is None
print("  карта штрафа = None, карта откликов не трогается")

print("\n=== 7. Буфер ШИРЕ кадра (выравнивание строк) ===")
# Именно на этом цвет и молчал: строки брались целиком, reshape падал,
# исключение проглатывалось, различимость всегда выходила ровно 0.0.
for pad in (0, 64):
    Wb = W + pad
    big = np.zeros((H * 3 // 2, Wb), np.uint8)
    src_buf = make_yuv(200, 90, 128, 128)
    big[:, :W] = src_buf
    uu, vv = ns["extract_chroma"](big)
    ok = uu is not None and uu.shape == (120, 160) and uu[60, 80] == 200
    print("  ширина буфера %3d -> %s" % (Wb, "цветность прочитана" if ok else "ОТКАЗ"))
    assert ok, "выравненный буфер обязан читаться"
print("\n=== 8. Кольцо фона НЕ должно лежать на самой цели ===")
# Ровно этот случай ломал цвет на борту: объект крупнее коробки, и «фон»
# брался квадратом, который целиком лежал на объекте — цвет сравнивался
# сам с собой и различимость выходила около нуля.
big = make_yuv(200, 90, 128, 128)          # цель 24x24 в цветности
u3, v3 = ns["extract_chroma"](big)
ns["chroma_u"], ns["chroma_v"] = u3, v3
# коробка ЗАНИЖЕНА вдвое против реального объекта
sig, sep_small_box = ns["measure_color_separation"](160, 120, 24, 24)
print("  коробка занижена вдвое -> различимость %.1f" % sep_small_box)
assert sep_small_box >= 12.0, "кольцо обязано доставать до фона: %.1f" % sep_small_box
print("  кольцо дотянулось до фона, цвет различает")

print("\nOK: цвет читается и при выравненных строках")

print("\nOK: цвет помогает, когда различает, и молчит, когда нет")
