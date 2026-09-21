"""Источник чёрной полосы сверху preview не установлен — офсет DRM-окна
возвращён на 0, вместо него добавлена диагностика (стендовая правка после
b1fb124/0759d88).

История: PREVIEW_Y=-2 (гипотеза "простой vertical offset DRM-
композитора") был проверен на стенде и НЕ убрал полосу. Значит гипотеза
неверна, и слепо пробовать -4/-6/-10 бессмысленно — сначала нужно узнать,
где полоса физически рождается:
  - main-кадр отдаётся камерой 640x480, DRM-окно задано 720x576 — разные
    соотношения сторон, и полоса может быть артефактом масштабирования
    DRM plane, а не offset'а;
  - либо полоса УЖЕ есть в самих данных main-буфера (тогда дело в
    камере/ISP, а не в display path вовсе).

Диагностика: _mean_top_strip() меряет среднюю яркость (G-канал) первых
_MAIN_TOP_STRIP_ROWS строк main-кадра ДО отрисовки оверлея, в camera_callback
— в двух местах, где эта функция уже мерит _last_main_mean (общую яркость)
для диагностики «пропало видео». Результат пишется в CSV (cam_top_row_mean)
и в консольный дебаг (TOPMEAN), рядом с MEAN (общая яркость) — если
top_row заметно темнее общего кадра, полоса внутри данных камеры; если
совпадает, источник строго в физическом DRM/VTX выводе.

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


print("=== 1. PREVIEW_Y возвращён на 0 (гипотеза про offset не "
      "подтвердилась) ===")
px, py = const("PREVIEW_X, PREVIEW_Y")
print("    PREVIEW_X=%d PREVIEW_Y=%d" % (px, py))
assert (px, py) == (0, 0), (
    "PREVIEW_X/PREVIEW_Y=(%d, %d) — стенд показал, что PREVIEW_Y=-2 не "
    "убирает полосу; пока источник не диагностирован, офсет должен "
    "оставаться (0, 0), а не новое случайное значение" % (px, py))

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

print("\n=== 4. Диагностика: _mean_top_strip меряет верх main-кадра ДО "
      "оверлея ===")
i_fn = src.index("def _mean_top_strip(main_array):")
i_fn_end = src.index("\ndef ", i_fn + 1)
fn_body = src[i_fn:i_fn_end]
assert "_MAIN_TOP_STRIP_ROWS" in fn_body, (
    "_mean_top_strip не использует _MAIN_TOP_STRIP_ROWS — константа "
    "полосы измерения не согласована")
assert ", 1]" in fn_body or ", 1].mean()" in fn_body, (
    "_mean_top_strip не берёт G-канал (индекс 1) — несогласовано с "
    "_last_main_mean, который меряет ту же яркость по всему кадру")

_CALLBACK_SITES = src.count("_last_main_top_mean = _mean_top_strip(mm.array)")
assert _CALLBACK_SITES >= 2, (
    "_mean_top_strip вызывается в camera_callback реже двух раз — в "
    "функции два независимых места, где MappedArray(request, \"main\") "
    "открывается ДО отрисовки оверлея (idle-ветка и активная), и оба "
    "обязаны измерять top-strip тем же способом, что _last_main_mean")
print("    _mean_top_strip задействован в обоих местах camera_callback, "
      "где меряется _last_main_mean")

print("\n=== 5. Результат идёт в CSV и в консольный дебаг рядом с общей "
      "яркостью ===")
assert "cam_top_row_mean" in src, (
    "диагностика не попадает в CSV — на разборе полёта её не увидеть")
i_row = src.index("_cam_exp_us, _cam_gain, _cam_colour_gain_r, _cam_colour_gain_b,")
tail = src[i_row:i_row + 200]
assert "_last_main_top_mean" in tail, (
    "cam_top_row_mean в CSV-заголовке есть, но _last_main_top_mean не "
    "пишется в ту же строку _row_values — столбцы разъедутся")
assert "TOPMEAN" in src, (
    "top-strip яркость не выведена в консольный дебаг рядом с MEAN — "
    "на бенче её не сравнить с общей яркостью глазами в реальном времени")
print("    cam_top_row_mean пишется в CSV, TOPMEAN — в консольный дебаг")

print("\nOK: PREVIEW_Y возвращён на 0 (гипотеза про offset не "
      "подтвердилась стендом), изоляция display path сохранена, "
      "добавлена диагностика top-strip vs общая яркость main-кадра для "
      "определения реального источника полосы")
