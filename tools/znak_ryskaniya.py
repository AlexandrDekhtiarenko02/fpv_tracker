#!/usr/bin/env python3
"""Знак и масштаб gyro_z — чтобы включить демпфирование рыскания.

ЗАЧЕМ. У тангажа и крена демпфирование по гироскопу уже стоит, и знак у них
замерен регрессией по полётным логам (0.078 и 0.059 °/с на единицу, r≈0.5).
У рыскания его нет: член с перепутанным знаком не «работает слабее», а
подкачивает то самое вращение, которое должен гасить.

КАК МЕРЯЕТСЯ. gyro_z сопоставляется с фактической скоростью изменения курса
из MSP_ATTITUDE. Курс замкнут по кругу, поэтому разность приводится к
диапазону ±180° — без этого один переход через ноль портит всю выборку.

ЧТО НУЖНО ОТ ВЫЛЕТА. Хотя бы один заход, где аппарат ЗАМЕТНО доворачивал по
рысканию. Если стик рыскания стоял в центре, возбуждения нет, и замер
покажет шум — ровно так и вышло с тангажом на первых логах.

ЗАПУСК:

    python3 tools/znak_ryskaniya.py                 # по всем свежим заходам
    python3 tools/znak_ryskaniya.py <папка захода>
"""
import csv
import glob
import math
import os
import sys


def chislo(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def raznica_kursa(a, b):
    """Изменение курса с приведением к ±180°."""
    d = b - a
    while d > 180.0:
        d -= 360.0
    while d < -180.0:
        d += 360.0
    return d


def sobrat(papki):
    pary = []
    for p in papki:
        f = os.path.join(p, "строки.csv")
        if not os.path.exists(f):
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
        for i in range(1, len(rows)):
            gz = chislo(rows[i].get("gyro_z"))
            dt = chislo(rows[i].get("dt_ms"))
            k0 = chislo(rows[i - 1].get("fc_yaw"))
            k1 = chislo(rows[i].get("fc_yaw"))
            if None in (gz, dt, k0, k1) or dt <= 0:
                continue
            d = raznica_kursa(k0, k1)
            # Скачок больше 30° за кадр — это не поворот, а сбой оценки.
            if abs(d) > 30.0:
                continue
            pary.append((gz, d / (dt / 1000.0)))
    return pary


def main():
    if len(sys.argv) > 1:
        papki = sys.argv[1:]
    else:
        baza = os.path.expanduser("~/fpv_tracker/flight_logs/zahvaty")
        papki = sorted(glob.glob(os.path.join(baza, "*")),
                       key=os.path.getmtime)[-12:]
    pary = sobrat(papki)
    print("папок разобрано: %d, пар: %d" % (len(papki), len(pary)))
    if len(pary) < 200:
        print()
        print("МАЛО ДАННЫХ. Нужен заход, где аппарат заметно доворачивал по")
        print("рысканию. Если стик стоял в центре, мерить нечего.")
        return 1

    xs = [a for a, _ in pary]
    ys = [b for _, b in pary]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        print("гироскоп или курс не меняются — мерить нечего")
        return 1
    naklon = sxy / sxx
    r = sxy / math.sqrt(sxx * syy)
    print("gyro_z -> скорость изменения курса: %+.4f (°/с) на единицу, r=%+.2f"
          % (naklon, r))

    if abs(r) < 0.25:
        print()
        print("СВЯЗЬ СЛАБАЯ (r=%.2f). Возбуждения по рысканию не хватило —" % r)
        print("включать демпфирование по такому замеру нельзя.")
        return 1

    znak = +1 if naklon > 0 else -1
    print()
    print("ЗНАК: %+d" % znak)
    print("  Положительный gyro_z означает поворот %s."
          % ("вправо" if znak > 0 else "влево"))
    print()
    print("Чтобы включить, дописать на борту:")
    print("    echo 'YAW_RATE_DAMP_SIGN = %+d' >> ~/fpv_tracker/local_settings.py"
          % znak)
    print("    echo 'YAW_RATE_DAMP_ENABLED = True' >> ~/fpv_tracker/local_settings.py")
    print("    sudo systemctl restart tracker")
    print()
    print("Первый заход после этого — высоко и коротко: если знак всё же не")
    print("тот, аппарат начнёт раскручивать рыскание, и это видно сразу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
