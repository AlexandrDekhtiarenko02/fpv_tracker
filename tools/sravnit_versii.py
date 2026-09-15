#!/usr/bin/env python3
"""Сравнение рывков команды МЕЖДУ ВЕРСИЯМИ кода.

Смотреть надо ХВОСТ распределения, а не медиану: пилот чувствует редкие
крупные рывки. Однажды медиана упала с 46 до 5 PWM, а 99-й процентиль стоял
на 60-90 и не двигался — правки меняли входы, а беда была в выходном
ограничителе.

Папки заходов названы хешем коммита, поэтому разбивка берётся из имён.

    python3 tools/sravnit_versii.py                      # тангаж
    python3 tools/sravnit_versii.py cmd_roll             # другая ось
    python3 tools/sravnit_versii.py cmd_pitch ~/Desktop/Logs

Папка с заходами ищется сама: сначала та, что в аргументе, потом FPV_LOGS,
потом обычные места на борту и на Маке. Инструмент нужен на обеих машинах —
на борту сразу после вылета, на Маке при разборе.
"""
import csv, glob, math, os, re, sys
from collections import defaultdict

# Больше этого промежутка соседние записи соседними не считаются: между ними
# что-то выпало, и разность охватила бы разрыв.
MAX_RAZRYV_S = 0.15

MESTA = (
    "~/fpv_tracker/flight_logs/zahvaty",      # борт
    "~/Desktop/Logs/zahvaty",                 # Мак, забранные логи
    "~/Desktop/fpv_tracker/flight_logs/zahvaty",
    "./flight_logs/zahvaty",
    "./zahvaty",
)


def nayti_papku(yavno=None):
    """Где лежат заходы. Явный путь важнее, дальше — привычные места."""
    kandidaty = []
    if yavno:
        kandidaty.append(yavno)
    if os.environ.get("FPV_LOGS"):
        kandidaty.append(os.environ["FPV_LOGS"])
    kandidaty.extend(MESTA)
    for k in kandidaty:
        put = os.path.expanduser(k)
        # Вложенную папку проверяем ПЕРВОЙ: передают обычно каталог логов, а
        # заходы лежат в zahvaty/ внутри него. Иначе вернули бы родителя, в
        # котором заходов нет, и получили бы «нет заходов с хешем версии».
        vnutri = os.path.join(put, "zahvaty")
        if os.path.isdir(vnutri):
            return vnutri
        if os.path.isdir(put):
            return put
    return None


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
    BAZA = nayti_papku(sys.argv[2] if len(sys.argv) > 2 else None)
    if BAZA is None:
        print("не нашёл папку с заходами. Укажи её явно:")
        print("    python3 tools/sravnit_versii.py %s <папка>" % kol)
        return 1
    print("папка: %s" % BAZA)
    po = defaultdict(lambda: {"sk": [], "lok": 0, "kadr": 0, "t": 0.0,
                              "p90_po_lokam": [], "ugol": [], "ramka": []})
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
        # ГОДНОСТЬ КАДРА И СОСЕДСТВО — РАЗНЫЕ ВЕЩИ.
        #
        # Раньше строки сначала отфильтровывались, а потом бралась разность
        # между соседями В ОТФИЛЬТРОВАННОМ списке. Если между ними выпадали
        # кадры (потеря цели, заморозка), разность охватывала разрыв и
        # выглядела как огромный скачок. Так в отчёте появился максимум 50 PWM
        # при пределе оси 32 — величина, которой быть не могло.
        #
        # Теперь пара берётся только из ПОДРЯД ИДУЩИХ кадров, и оба должны
        # быть годными.
        def godnyy(r):
            return ((ch(r.get("t")) or 0) - t0 > 1.0
                    and str(r.get("final_hold")) not in ("True", "1")
                    and r.get("state") == "TRACKED")

        sk = []
        n_godnyh = 0
        for i in range(len(rows)):
            if godnyy(rows[i]):
                n_godnyh += 1
            if i == 0:
                continue
            a, b = rows[i - 1], rows[i]
            if not (godnyy(a) and godnyy(b)):
                continue
            # И по времени соседи: пропущенная запись тоже даёт разрыв.
            ta, tb = ch(a.get("t")), ch(b.get("t"))
            if ta is None or tb is None or (tb - ta) > MAX_RAZRYV_S:
                continue
            va, vb = ch(a.get(kol)), ch(b.get(kol))
            if va is None or vb is None:
                continue
            sk.append(abs(vb - va))
        if n_godnyh < 20 or not sk:
            continue
        d = po[m.group(1)]
        d["lok"] += 1
        d["kadr"] += n_godnyh
        d["t"] = max(d["t"], os.path.getmtime(p))
        d["sk"].extend(sk)
        # ПОКАЗАТЕЛЬ ПО КАЖДОМУ ЛОКУ ОТДЕЛЬНО.
        #
        # Без него сравнение версий обманывает: заходы отличаются друг от
        # друга круче, чем версии. Замерено, что разброс 90-го процентиля
        # между локами одной версии доходит до 38 PWM, тогда как разница
        # между соседними версиями была 3. Такая разница ничего не значит.
        d["p90_po_lokam"].append(q(sk, 0.9))
        ug = [ch(r.get("depression_deg")) for r in rows]
        ra = [ch(r.get("box_size_px")) for r in rows]
        ug = [x for x in ug if x is not None]
        ra = [x for x in ra if x is not None]
        if ug:
            d["ugol"].append(q(ug, 0.5))
        if ra:
            d["ramka"].append(q(ra, 0.5))
    if not po:
        print("нет заходов с хешем версии в имени")
        return 1
    print("скачки «%s» между кадрами, PWM (только середина захода)" % kol)
    print()
    print("%-10s %6s %8s %8s %8s %8s %8s %11s %9s %8s"
          % ("версия", "локов", "кадров", "50%", "90%", "99%", "макс",
             "90% по локам", "угол°", "рамка"))
    for v, d in sorted(po.items(), key=lambda kv: kv[1]["t"]):
        s = d["sk"]
        if len(s) < 50:
            continue
        p90 = d["p90_po_lokam"]
        razbros = ("%.0f..%.0f" % (min(p90), max(p90))) if p90 else "—"
        print("%-10s %6d %8d %8.1f %8.1f %8.1f %8.1f %11s %9.1f %8.0f"
              % (v, d["lok"], d["kadr"], q(s, 0.5), q(s, 0.9),
                 q(s, 0.99), max(s), razbros,
                 q(d["ugol"], 0.5) if d["ugol"] else 0.0,
                 q(d["ramka"], 0.5) if d["ramka"] else 0.0))
    print()
    print("КАК ЧИТАТЬ. «90% по локам» — разброс этого показателя МЕЖДУ")
    print("заходами одной версии. Если разница между версиями меньше этого")
    print("разброса, она ничего не значит: заходы отличаются круче самих")
    print("правок. Столбцы «угол» и «рамка» показывают, сопоставимы ли")
    print("условия вообще — версия, летавшая по крупной цели, несравнима с")
    print("версией, летавшей по мелкой.")
    print()
    print("Хвост важнее медианы: пилот чувствует редкие крупные рывки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
