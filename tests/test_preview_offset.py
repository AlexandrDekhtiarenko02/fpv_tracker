"""Чёрная полоса сверху preview лечится офсетом DRM-окна, а не кропом
tracking-кадра.

Симптом: на физическом видеовыходе сверху видна тонкая чёрная полоса —
offset композитора DRM, картинка на пару пикселей сдвинута вниз. Лечится
PREVIEW_Y < 0 (поднимаем окно вверх) — это параметр ТОЛЬКО экрана,
start_preview(Preview.DRM, ...) не имеет отношения ни к main/lores
буферам, ни к CENTER_X/CENTER_Y, ни к геометрии трекера.

Проверяется по исходному тексту (реальный DRM недоступен в offline-
прогоне, как и весь остальной блок запуска камеры — см. test_cam_fps.py/
test_camera_settle.py для того же класса проверок).
"""
import io
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()


def const(name):
    return eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1))


print("=== 1. PREVIEW_Y отрицательный (поднимает картинку вверх) ===")
px, py = const("PREVIEW_X, PREVIEW_Y")
print("    PREVIEW_X=%d PREVIEW_Y=%d" % (px, py))
assert py < 0, "PREVIEW_Y не отрицателен — полоса сверху не лечится"
assert -6 <= py <= -1, (
    "PREVIEW_Y=%d вне разумного диапазона (-1..-6) для 'пары пикселей "
    "офсета композитора'" % py)

print("\n=== 2. Только display path: main/lores и CENTER_* не завязаны "
      "на PREVIEW_* ===")
# CENTER_X/CENTER_Y/CENTER_X_LORES/CENTER_Y_LORES считаются от MAIN_W/H и
# LORES_W/H, а не от PREVIEW_*, и это единственное разрешённое соседство.
i_center = src.index("CENTER_X, CENTER_Y = MAIN_W")
line_center = src[i_center:i_center + 200]
assert "PREVIEW" not in line_center, (
    "координаты прицела ссылаются на PREVIEW_* — офсет экрана просочился "
    "в геометрию трекера")

print("\n=== 3. PREVIEW_X/Y не встречаются в функциях трекинга/геометрии ===")
# Не построчный подсчёт всех \bPREVIEW_[XY]\b по файлу — он ловит и
# прозу в комментариях-пояснениях (см. блок констант выше), и диагностический
# print внутри except-отката start_preview, которые не являются "утечкой"
# офсета в геометрию. По существу важно другое: PREVIEW_X/Y не должны
# встречаться ВНУТРИ тел функций, которые считают прицел, рамку или
# координаты слежения — именно это проверяем прямо.
_GEOMETRY_FUNCS = (
    "def process_locked_tracker", "def update_control_from_target",
    "def template_match_locked", "def flow_predict",
)
found_any = False
for fn in _GEOMETRY_FUNCS:
    i = src.find(fn)
    if i < 0:
        continue
    found_any = True
    j = src.find("\ndef ", i + 1)
    body = src[i:j if j > 0 else len(src)]
    assert "PREVIEW_" not in body, (
        "%s ссылается на PREVIEW_X/Y — офсет экрана просочился в "
        "функцию, которая считает координаты трекера" % fn)
assert found_any, "ни одна из проверяемых функций трекинга не найдена в файле"
print("    не встречаются ни в одной из проверенных функций трекинга")

print("\n=== 4. Отрицательный офсет не роняет запуск — есть откат на y=0 ===")
i_sp = src.index("picam2.start_preview(Preview.DRM, x=PREVIEW_X, y=PREVIEW_Y")
hvost = src[i_sp:i_sp + 700]
assert "except Exception" in hvost, (
    "нет обработки отказа DRM backend на отрицательный y — сбой при "
    "старте уронил бы всю программу вместо отката на y=0")
assert "y=0" in hvost, (
    "нет явного отката на y=0 в обработчике отказа — 'не подменять офсет "
    "кропом' требует именно отката внутри display path, не тишины")

print("\nOK: PREVIEW_Y отрицателен, изолирован в display path, есть "
      "безопасный откат при отказе DRM backend")
