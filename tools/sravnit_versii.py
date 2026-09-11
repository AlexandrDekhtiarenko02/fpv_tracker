#!/usr/bin/env python3
"""Сравнение рывков команды МЕЖДУ ВЕРСИЯМИ кода.

Смотреть надо ХВОСТ распределения, а не медиану: пилот чувствует редкие
крупные рывки. Однажды медиана упала с 46 до 5 PWM, а 99-й процентиль стоял
на 60-90 и не двигался — правки меняли входы, а беда была в выходном
ограничителе.

Папки заходов названы хешем коммита, поэтому разбивка берётся из имён.

    python3 tools/sravnit_versii.py           # тангаж
    python3 tools/sravnit_versii.py cmd_roll  # другая ось
"""
import csv, glob, math, os, re, sys
from collections import defaultdict

BAZA = os.path.expanduser("~/fpv_tracker/flight_logs/zahvaty")


def ch(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def q(a, pr):
    a = sorted(a)
    return a[int(pr * len(a))] if a else float("nan")


def main():
    kol = sys.argv[1] if len(sys.argv) > 1 else "cmd_pitch"
    po = defaultdict(lambda: {"sk": [], "lok": 0, "kadr": 0, "t": 0.0})
    for p in sorted(glob.glob(os.path.join(BAZA, "*"))):
        m = re.search(r"_([0-9a-f]{7})$", os.path.basename(p))
        if not m:
            continue
        f = os.path.join(p, "строки.csv")
        if not os.path.exists(f):
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
        if len(rows) < 40:
            continue
        t0 = ch(rows[0].get("t")) or 0.0
        # Середина захода: без первой секунды (оценки ещё набираются) и без
        # замороженных кадров — там команда не меняется по замыслу.
        sred = [r for r in rows
                if (ch(r.get("t")) or 0) - t0 > 1.0
                and str(r.get("final_hold")) not in ("True", "1")
                and r.get("state") == "TRACKED"]
        if len(sred) < 20:
            continue
        d = po[m.group(1)]
        d["lok"] += 1
        d["kadr"] += len(sred)
        d["t"] = max(d["t"], os.path.getmtime(p))
        c = [ch(r.get(kol)) for r in sred]
        for i in range(1, len(c)):
            if c[i] is not None and c[i - 1] is not None:
                d["sk"].append(abs(c[i] - c[i - 1]))
    if not po:
        print("нет заходов с хешем версии в имени")
        return 1
    print("скачки «%s» между кадрами, PWM (только середина захода)" % kol)
    print()
    print("%-10s %6s %8s %8s %8s %8s %8s"
          % ("версия", "локов", "кадров", "50%", "90%", "99%", "макс"))
    for v, d in sorted(po.items(), key=lambda kv: kv[1]["t"]):
        s = d["sk"]
        if len(s) < 50:
            continue
        print("%-10s %6d %8d %8.1f %8.1f %8.1f %8.1f"
              % (v, d["lok"], d["kadr"], q(s, 0.5), q(s, 0.9),
                 q(s, 0.99), max(s)))
    print()
    print("Хвост важнее медианы: пилот чувствует редкие крупные рывки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
