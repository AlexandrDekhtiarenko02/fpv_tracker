"""Чёрная полоса сверху preview — найден и закрыт настоящий источник:
несовпадение аспекта/высоты DRM preview-окна с камерой и с активным
видеорежимом (стендовая правка после b1fb124/0759d88/93fd335).

История диагностики:
  - PREVIEW_Y=-2 (гипотеза "простой vertical offset DRM-композитора") не
    убрал полосу на стенде.
  - _mean_top_strip()/TOPMEAN подтвердили на равномерной стене
    MEAN==TOPMEAN (202==202) — полосы ВНУТРИ main-буфера камеры нет,
    источник строго в display path после буфера.
  - kmsprint -m + прямой осмотр слоёв по SSH дали настоящую причину:
    активный видеовыход — Composite-1, NTSC 720x480i, HDMI отключён.
    Окно preview стояло 720x576 (0,0). Камера отдаёт 640x480 (4:3) —
    это НЕ вписывается без искажения в окно 720x576 (аспект ~1.25:1), и
    picamera2 промасштабировал источник до 720x540 (аспект сохранён:
    720*480/640=540) и отцентрировал внутри окна — letterbox-паддинг
    (576-540)/2=18px сверху и снизу. Реальный экран — только 480 строк:
    верхний 18-строчный паддинг ПОПАДАЕТ в видимую зону (строки 0-17 без
    картинки — это и была чёрная полоса), а нижние ~78 строк самой
    картинки (18+540=558>480) уходили за пределы экрана и не были видны
    вовсе.

Фикс: PREVIEW_W/PREVIEW_H = точный нативный размер камеры (MAIN_W/MAIN_H)
и одновременно ровно высота активного NTSC-режима (480 строк) — источник
вписывается 1:1, без масштабирования и без letterbox вообще.
_mean_top_strip/TOPMEAN/cam_top_row_mean остаются в коде постоянной
диагностикой (то же назначение, что у mean_gray) — по ним можно будет
перепроверить, что полосы больше нет и после этой правки.

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


print("=== 1. PREVIEW_W/H = нативный размер камеры (без letterbox) ===")
pw, ph = const("PREVIEW_W, PREVIEW_H")
main_w, main_h = const("MAIN_W, MAIN_H")
print("    PREVIEW_W=%d PREVIEW_H=%d (камера: %dx%d)"
      % (pw, ph, main_w, main_h))
assert (pw, ph) == (main_w, main_h), (
    "PREVIEW_W/H=(%d, %d) не совпадает с MAIN_W/H=(%d, %d) — источник "
    "снова придётся масштабировать под другой аспект, и picamera2 сам "
    "добавит letterbox-паддинг, ровно как в найденном баге" % (pw, ph, main_w, main_h))

print("\n=== 2. PREVIEW_X/Y = (0, 0) — офсет окна не при чём, полоса была "
      "не от него ===")
px, py = const("PREVIEW_X, PREVIEW_Y")
print("    PREVIEW_X=%d PREVIEW_Y=%d" % (px, py))
assert (px, py) == (0, 0), (
    "PREVIEW_X/PREVIEW_Y=(%d, %d) — найденная причина полосы (letterbox "
    "из-за несовпадения аспекта/высоты окна) не связана с offset'ом окна, "
    "менять его не было оснований" % (px, py))

print("\n=== 3. Только display path: main/lores и CENTER_* не завязаны "
      "на PREVIEW_* ===")
# CENTER_X/CENTER_Y/CENTER_X_LORES/CENTER_Y_LORES считаются от MAIN_W/H и
# LORES_W/H, а не от PREVIEW_*, и это единственное разрешённое соседство.
i_center = src.index("CENTER_X, CENTER_Y = MAIN_W")
line_center = src[i_center:i_center + 200]
assert "PREVIEW" not in line_center, (
    "координаты прицела ссылаются на PREVIEW_* — офсет экрана просочился "
    "в геометрию трекера")

print("\n=== 4. PREVIEW_X/Y не встречаются в функциях трекинга/геометрии ===")
# Не построчный подсчёт всех \bPREVIEW_[XY]\b по файлу — он ловит и прозу
# в комментариях-пояснениях (блок констант, история диагностики). По
# существу важно другое: PREVIEW_X/Y не должны встречаться ВНУТРИ тел
# функций, которые считают прицел, рамку или координаты слежения.
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

print("\n=== 5. Диагностика (_mean_top_strip/TOPMEAN) осталась в коде ===")
# Полоса закрыта, но MEAN/TOPMEAN — та же полезная диагностика "пропало
# видео", что mean_gray — снимать её не было причин.
i_fn = src.index("def _mean_top_strip(main_array):")
i_fn_end = src.index("\ndef ", i_fn + 1)
fn_body = src[i_fn:i_fn_end]
assert "_MAIN_TOP_STRIP_ROWS" in fn_body, (
    "_mean_top_strip не использует _MAIN_TOP_STRIP_ROWS — константа "
    "полосы измерения не согласована")

_CALLBACK_SITES = src.count("_last_main_top_mean = _mean_top_strip(mm.array)")
assert _CALLBACK_SITES >= 2, (
    "_mean_top_strip вызывается в camera_callback реже двух раз — в "
    "функции два независимых места, где MappedArray(request, \"main\") "
    "открывается ДО отрисовки оверлея (idle-ветка и активная), и оба "
    "обязаны измерять top-strip тем же способом, что _last_main_mean")
assert "cam_top_row_mean" in src and "TOPMEAN" in src, (
    "диагностика top-strip больше не попадает в CSV/консольный дебаг")
print("    _mean_top_strip/cam_top_row_mean/TOPMEAN остаются в коде")

print("\nOK: PREVIEW_W/H приведены к нативному размеру камеры (без "
      "letterbox, без потери нижней части картинки за пределами активного "
      "480-строчного режима), офсет остаётся (0,0) — он не был причиной, "
      "диагностика top-strip сохранена для будущей перепроверки")
