#!/usr/bin/env python3
"""Прогнать НАСТОЯЩИЙ трекер по ВСЕМ записям и свести в одну таблицу.

Смысл: правку видно не по одной записи, а по всем сразу. Иначе легко починить
один случай и сломать три других — этим мы уже занимались весь день, когда
сравнивали прогоны между собой.

    python3 tools/sweep.py
    python3 tools/sweep.py --set MOTION_PENALTY=0.9 --set SEARCH_MARGIN_MIN=18
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "flight_logs", "recordings")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[],
                    help="ИМЯ=значение — подменить настройку перед прогоном")
    ap.add_argument("--dir", default=REC)
    ap.add_argument("--only", help="брать только записи, чьё имя содержит это")
    a = ap.parse_args()

    t = offline.load_tracker()
    for item in a.set:
        name, _, val = item.partition("=")
        old = getattr(t, name, None)
        setattr(t, name, type(old)(val) if isinstance(old, (int, float))
                and not isinstance(old, bool) else eval(val))
        print("подменено: %s %s -> %s" % (name, old, getattr(t, name)))
    if a.set:
        print()

    names = sorted(f[:-len(".meta.txt")] for f in os.listdir(a.dir)
                   if f.endswith(".meta.txt"))
    if a.only:
        pats = a.only.split(",")
        names = [n for n in names if any(p in n for p in pats)]
    print("%-18s %7s %7s %9s %10s" % ("запись", "кадров", "совпад", "коробка", "НА ФОНЕ"))
    print("-" * 56)
    tot_bad = tot_n = 0
    grows = []
    for nm in names:
        frames, rows, w, h = offline.load_recording(os.path.join(a.dir, nm))
        out = offline.run(t, frames)
        ok = [r for r in out if r[1] is not None]
        if not ok:
            print("%-18s  цель не удержана" % nm)
            continue
        pct, n = offline.on_background(out, offline.motion_mask(frames))
        sc = sum(r[4] for r in ok) / len(ok)
        grow = ok[-1][3] / max(ok[0][3], 1e-6)
        grows.append(grow)
        tot_bad += pct * n / 100.0
        tot_n += n
        print("%-18s %7d %7.3f %6.0f→%-3.0f %9.1f%%"
              % (nm, len(ok), sc, ok[0][3] * 2, ok[-1][3] * 2, pct))
    print("-" * 56)
    print("ИТОГО на фоне: %.1f%% кадров | коробка растёт в среднем в %.2f раза"
          % (100.0 * tot_bad / max(tot_n, 1), sum(grows) / max(len(grows), 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
