"""Координаты пишутся с полной точностью, а не с тремя знаками.

Найдено при разборе 4 сентября 2026: весь лог писался с «%.3f», и для широты
это шаг в 111 метров, для долготы 70. По такому логу нельзя ни восстановить
траекторию, ни поправить задним числом координаты цели, ни проверить
дальность по GPS — а вся сегодняшняя калибровка держалась именно на ней.

Опаснее всего то, что данные при этом выглядели нормально: 50.663000 —
правдоподобное число, и что оно округлено до сотни метров, видно только если
специально присмотреться к нулям.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
cols = t._FLIGHT_LOG_COLUMNS.split(",")

print("=== 1. Координаты сохраняют все знаки ===")
vals = [None] * len(cols)
la, lo = 50.6636061, 29.9342583
vals[cols.index("gps_lat")] = la
vals[cols.index("gps_lon")] = lo
p = t._fmt_row(vals).split(",")
for imya, ist in (("gps_lat", la), ("gps_lon", lo)):
    zap = p[cols.index(imya)]
    print("    %-9s %.7f -> %s" % (imya, ist, zap))
    oshibka_m = abs(float(zap) - ist) * 111320.0
    assert oshibka_m < 0.5, (
        "%s записана с ошибкой %.1f м — по такому логу траекторию не "
        "восстановить" % (imya, oshibka_m))

print("\n=== 2. Три знака у координат — это провал ===")
# Ровно то, что было: убеждаемся, что проверка поймала бы прежний формат.
staraya = "%.3f" % la
poterya = abs(float(staraya) - la) * 111320.0
print("    прежний формат дал бы %s, то есть промах %.0f м" % (staraya, poterya))
assert poterya > 10.0, "проверка не поймает возврат к трём знакам"

print("\n=== 3. Остальным колонкам точность не раздули ===")
# Иначе строка распухнет на ровном месте: колонок 136, а нужна точность двум.
vals = [None] * len(cols)
vals[cols.index("range_m")] = 141.23456789
vals[cols.index("alt_cm")] = 1234.5678
p = t._fmt_row(vals).split(",")
for imya in ("range_m", "alt_cm"):
    z = p[cols.index(imya)]
    print("    %-9s -> %s" % (imya, z))
    assert len(z.split(".")[1]) <= 3, "лишние знаки у %s" % imya

print("\n=== 4. Пустые и нечисловые не сломались ===")
vals = [None] * len(cols)
vals[cols.index("armed")] = True
vals[cols.index("gps_lat")] = None
p = t._fmt_row(vals).split(",")
assert p[cols.index("gps_lat")] == "", "пустое значение стало не пустым"
assert p[cols.index("armed")] == "1", "признак арма сломался"
assert len(p) == len(cols), "число полей разошлось с шапкой"
print("    пустое остаётся пустым, признаки на месте, полей %d" % len(p))

print("\nOK: по логу теперь можно восстановить, где именно был борт")
