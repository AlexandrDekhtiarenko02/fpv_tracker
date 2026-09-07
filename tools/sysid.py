"""Идентификация объекта по НАБЛЮДАТЕЛЬНЫМ логам (OBSERVE_ONLY).

Что тут можно узнать, а что нельзя.

МОЖНО: как аппарат отвечает на стик. В ACRO стик задаёт скорость вращения,
гироскоп её меряет — из пары «стик, гироскоп» берутся задержка отклика и
грубый коэффициент. Это не требует, чтобы контур управлял: пилот двигал
стиками, аппарат отвечал, всё записано.

НЕЛЬЗЯ: настроить замкнутый контур. P/I/D в обратной связи проверяются только
в полёте с включённым MSP OVERRIDE. Здесь контур ни разу не рулил — в логах
лежат команды, которые он ПОСЧИТАЛ БЫ, но они никуда не уходили.

Слабость данных: пилот летел мелкими корректирующими движениями, а не
чёткими ступеньками. Поэтому связь стик→гироскоп зашумлена, и коэффициент
выходит грубым. Для точной идентификации нужен отдельный полёт с чистыми
ступенчатыми вводами по одной оси за раз.

    python3 tools/sysid.py flight_logs/zahvaty
"""
import csv
import glob
import io
import os
import statistics as st
import sys


def _col(rows, k):
    out = []
    for x in rows:
        v = (x.get(k) or "").strip()
        try:
            out.append(float(v))
        except ValueError:
            out.append(None)
    return out


def _fit(a, b):
    p = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(p) < 20:
        return 0.0, 0.0, 0
    n = len(p)
    mx = sum(x for x, _ in p) / n
    my = sum(y for _, y in p) / n
    sxx = sum((x - mx) ** 2 for x, _ in p)
    syy = sum((y - my) ** 2 for _, y in p)
    sxy = sum((x - mx) * (y - my) for x, y in p)
    r = sxy / ((sxx * syy) ** 0.5) if sxx > 0 and syy > 0 else 0.0
    slope = sxy / sxx if sxx > 0 else 0.0
    return r, slope, n


def main(koren):
    papki = sorted(glob.glob(os.path.join(koren, "*", "строки.csv")))
    if not papki:
        print("нет логов в", koren)
        return 1
    print("логов:", len(papki))

    print("\n=== ЗАДЕРЖКА И КОЭФФИЦИЕНТ отклика на стик ===")
    for stick, gyro, imya in (("rc_r", "gyro_x", "КРЕН"),
                              ("rc_p", "gyro_y", "ТАНГАЖ")):
        po_sdvigu = {}
        for f in papki:
            raw = io.open(f, encoding="utf-8", errors="replace").read().replace("\x00", "")
            r = list(csv.DictReader(io.StringIO(raw)))
            s = [(v - 1500) if v is not None else None for v in _col(r, stick)]
            g = _col(r, gyro)
            dv = [x for x in s if x is not None]
            if not dv or (max(dv) - min(dv)) < 20:   # стик стоял на месте
                continue
            for sh in range(6):
                a = s[:len(s) - sh] if sh else s
                b = g[sh:] if sh else g
                rr, sl, _ = _fit(a, b)
                po_sdvigu.setdefault(sh, []).append((rr, sl))
        if not po_sdvigu:
            print("  %s: ни одного захода с движением стика" % imya)
            continue
        luchshiy = max(po_sdvigu, key=lambda sh: st.median(x[0] for x in po_sdvigu[sh]))
        print("  %s:" % imya)
        for sh in sorted(po_sdvigu):
            rs = [x[0] for x in po_sdvigu[sh]]
            sls = [x[1] for x in po_sdvigu[sh]]
            metka = "  <- пик" if sh == luchshiy else ""
            print("    задержка %d кадр (%3.0f мс): r=%.3f, %.2f °/с на ед.стика%s"
                  % (sh, sh * 42, st.median(rs), st.median(sls), metka))

    print("\n=== НАСЫЩЕНИЕ КОМПЕНСАЦИИ ТАНГАЖА (не зависит от пилота) ===")
    comp_sat = tot = 0
    for f in papki:
        raw = io.open(f, encoding="utf-8", errors="replace").read().replace("\x00", "")
        r = list(csv.DictReader(io.StringIO(raw)))
        for c in _col(r, "pitch_comp_px"):
            if c is None:
                continue
            tot += 1
            if abs(c) >= 99:
                comp_sat += 1
    if tot:
        print("  кадров: %d, компенсация на пределе 100 px: %.0f%%" % (tot, 100 * comp_sat / tot))
        print("  предел 100 px = всего 8.5° тангажа (PIXELS_PER_PITCH_DEG=11.7),")
        print("  а в пикировании тангаж 20-40°: компенсация физически не дотягивает.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "flight_logs/zahvaty"))
