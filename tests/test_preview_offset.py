"""DRM preview window sizing — полная история диагностики чёрной полосы/
полей вокруг картинки, закрыта окончательно (стендовые правки после
b1fb124/0759d88/93fd335).

История диагностики:
  - PREVIEW_Y=-2 (гипотеза "простой vertical offset DRM-композитора") не
    убрал полосу сверху на стенде.
  - _mean_top_strip()/TOPMEAN подтвердили на равномерной стене
    MEAN==TOPMEAN (202==202) — полосы ВНУТРИ main-буфера камеры нет,
    источник строго в display path после буфера.
  - kmsprint -m + прямой осмотр слоёв по SSH дали настоящую причину
    полосы СВЕРХУ: активный видеовыход — Composite-1, NTSC 720x480i,
    HDMI отключён. Окно preview стояло 720x576 (0,0). Камера отдаёт
    640x480 (4:3) — это НЕ вписывается без искажения в окно 720x576
    (аспект ~1.25:1), и picamera2 промасштабировал источник до 720x540
    (аспект сохранён: 720*480/640=540) и отцентрировал внутри окна —
    letterbox-паддинг (576-540)/2=18px сверху и снизу. Реальный экран —
    только 480 строк: верхний 18-строчный паддинг ПОПАДАЛ в видимую зону
    (строки 0-17 без картинки — это и была полоса), а нижние ~78 строк
    самой картинки уходили за пределы экрана и не были видны вовсе.
  - Фикс PREVIEW_H=MAIN_H=480 (высота ровно как активный режим) убрал
    полосу СВЕРХУ полностью.
  - PREVIEW_W=MAIN_W=640 (без масштабирования вовсе) убрал letterbox, но
    по ширине активный режим (ACTIVE_MODE_W=720) шире окна — при
    PREVIEW_X=0 вся разница (80px) уходила ЦЕЛИКОМ в правый край
    ("огромная полоса справа"). Центрирование (PREVIEW_X=40) сделало
    поля симметричными (по 40px), но не убрало их.
  - Эксперимент PREVIEW_W=ACTIVE_MODE_W=720 (окно на всю ширину) — ответ
    получен ПРЯМЫМ ОСМОТРОМ /sys/kernel/debug/dri/*/state на живой
    системе (не по документации): реальный DRM-слой камеры оказался
    БИТ-В-БИТ ТЕМ ЖЕ, что и при PREVIEW_W=640 — size=640x480,
    crtc-pos=640x480+40+0, src-pos=640x480 (масштаб 1:1, без
    растяжения). Picamera2 выбирает единый коэффициент
    min(window_w/src_w, window_h/src_h) — раз PREVIEW_H уже равен
    нативной высоте источника (480/480=1.0, самый узкий множитель),
    любое увеличение PREVIEW_W сверх 640 добавляет только пустой
    pillarbox, картинку не растягивает и не обрезает.

ВЫВОД (окончательный, подтверждён кадром ядра, не документацией): поля
по бокам при этом источнике/режиме архитектурно неустранимы через окно
DRM preview, пока PREVIEW_H=480 держит закрытой полосу сверху.
PREVIEW_W=640 — финальное значение: расширение до ACTIVE_MODE_W не даёт
никакого визуального эффекта (доказано), только вводит в заблуждение
насчёт намерения кода.

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


print("=== 1. PREVIEW_H = MAIN_H — полоса сверху остаётся закрытой ===")
pw, ph = const("PREVIEW_W, PREVIEW_H")
main_w, main_h = const("MAIN_W, MAIN_H")
active_mode_w = const("ACTIVE_MODE_W")
print("    PREVIEW_W=%d PREVIEW_H=%d (камера: %dx%d, активный режим "
      "шириной %d)" % (pw, ph, main_w, main_h, active_mode_w))
assert ph == main_h, (
    "PREVIEW_H=%d != MAIN_H=%d — это была ПОДТВЕРЖДЁННАЯ причина полосы "
    "сверху (окно выше активного 480-строчного режима даёт letterbox, "
    "который попадает в видимую зону); менять высоту без нового "
    "стендового подтверждения нельзя" % (ph, main_h))

print("\n=== 2. PREVIEW_W = MAIN_W — расширение до ACTIVE_MODE_W проверено "
      "и не даёт эффекта ===")
assert pw == main_w, (
    "PREVIEW_W=%d != MAIN_W=%d — прямой осмотр /sys/kernel/debug/dri/*/"
    "state на стенде показал, что PREVIEW_W=ACTIVE_MODE_W (720) даёт "
    "БИТ-В-БИТ тот же слой (640x480+40+0, масштаб 1:1), что и "
    "PREVIEW_W=MAIN_W — расширять окно сверх нативной ширины камеры "
    "бессмысленно, пока PREVIEW_H уже равен нативной высоте" % (pw, main_w))
print("    подтверждено: PREVIEW_W=ACTIVE_MODE_W не меняет фактическую "
      "геометрию слоя (проверено на живой системе)")

print("\n=== 3. PREVIEW_X центрирует окно в активном режиме, PREVIEW_Y=0 ===")
m_xy = re.search(r"^PREVIEW_X, PREVIEW_Y = (.+?)(?:\s+#.*)?$", src, re.M)
px, py = eval(m_xy.group(1), {}, {"ACTIVE_MODE_W": active_mode_w, "PREVIEW_W": pw})
print("    ACTIVE_MODE_W=%d PREVIEW_W=%d -> PREVIEW_X=%d PREVIEW_Y=%d"
      % (active_mode_w, pw, px, py))
assert py == 0, (
    "PREVIEW_Y=%d — найденная причина полосы сверху (letterbox из-за "
    "несовпадения аспекта/высоты окна) не связана с вертикальным "
    "offset'ом, менять его не было оснований" % py)
_expected_px = (active_mode_w - pw) // 2
assert px == _expected_px, (
    "PREVIEW_X=%d не центрирует окно в активном режиме (ожидали %d = "
    "(ACTIVE_MODE_W-PREVIEW_W)//2) — стенд уже показал, что PREVIEW_X=0 "
    "уводит всю неиспользуемую площадь целиком в один край ('огромная "
    "полоса справа', картинка уехала влево)" % (px, _expected_px))

print("\n=== 4. Только display path: main/lores и CENTER_* не завязаны "
      "на PREVIEW_* ===")
# CENTER_X/CENTER_Y/CENTER_X_LORES/CENTER_Y_LORES считаются от MAIN_W/H и
# LORES_W/H, а не от PREVIEW_*, и это единственное разрешённое соседство.
i_center = src.index("CENTER_X, CENTER_Y = MAIN_W")
line_center = src[i_center:i_center + 200]
assert "PREVIEW" not in line_center, (
    "координаты прицела ссылаются на PREVIEW_* — офсет экрана просочился "
    "в геометрию трекера")

print("\n=== 5. PREVIEW_X/Y не встречаются в функциях трекинга/геометрии ===")
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

print("\n=== 6. Диагностика (_mean_top_strip/TOPMEAN) осталась в коде ===")
# Полоса сверху закрыта, но MEAN/TOPMEAN — та же полезная диагностика
# "пропало видео", что mean_gray — снимать её не было причин.
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

print("\nOK: PREVIEW_H держит закрытой полосу сверху, PREVIEW_W=MAIN_W "
      "финален (расширение проверено на живой системе и не даёт "
      "эффекта), PREVIEW_X центрирует, диагностика top-strip сохранена")
