#!/usr/bin/env python3
"""Масштаб, знак и смещение гироскопа — по записанному заходу.

ЗАЧЕМ. После выключения экстраполяции тангажа (7878eb4) гироскоп остался
единственным источником двух вещей: демпфирования скорости (PITCH_RATE_DAMP —
это вся производная по тангажу) и защиты «не считаем λ̇ при быстром вращении».
Обе работают с угловой скоростью напрямую. Если масштаб или знак неверны,
демпфер не «гасит слабее», а гасит не то, и защита вращения срабатывает по
шуму: на трёх заходах d324285 она срабатывала 12–30 раз за полёт и в 92–100%
случаев ложно, при |gyro| 176–219 °/с против фактических 1–3 °/с.

ЧТО СЧИТАЕТСЯ. По каждой оси (gyro_y↔тангаж, gyro_x↔крен, gyro_z↔рыскание):

  1. Регрессия фактической скорости угла (Δfc_*/dt из MSP_ATTITUDE) на сырые
     единицы гироскопа. Наклон и есть масштаб в (°/с)/единицу, r показывает,
     сколько в паре настоящего движения. Тот же способ, которым масштаб был
     получен 9 сентября (0.078 по тангажу r=0.52, 0.059 по крену r=0.51).
  2. Интеграл: Σ gyro·GYRO_UNIT_DPS·dt против суммарного изменения угла.
     Ловит ошибку знака и масштаба там, где регрессия по кадрам тонет в шуме.
  3. Смещение и размах сырых единиц: медиана, p10/p90, RMS в единицах и в
     °/с при текущем масштабе — рядом с RMS фактической скорости угла.

ЧТО НУЖНО ОТ ЗАПИСИ. Возбуждение. В спокойном заходе тангаж меняется на 1–3 °/с,
и на этом фоне не отличить масштаб от шума: r выйдет около нуля, и это будет
правдой о записи, а не о гироскопе. Поэтому калибровка делается на земле:

  1. Аппарат на ровной поверхности, арм не нужен, трекер запущен
     (record.sh пишет строки.csv и так).
  2. Дать запись, медленно наклонить нос вниз на 30–40° за 2–3 с, подержать,
     вернуть обратно. Повторить 3–4 раза. То же по крену.
  3. Быстрые вращения тоже полезны (проверяют защиту λ̇), но ОТДЕЛЬНО и после
     медленных: на медленных измеряется масштаб, на быстрых — знак.
  4. python3 tools/proverka_giroskopa.py <папка записи>

На такой записи изменение угла известно из самого лога (fc_pitch), возбуждение
в разы больше шума, и наклон регрессии — это и есть масштаб.

ЗАПУСК:

    python3 tools/proverka_giroskopa.py                     # последние заходы
    python3 tools/proverka_giroskopa.py <папка захода> ...   # свои
    python3 tools/proverka_giroskopa.py <файл строки.csv> ...
"""
import csv
import glob
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Оси: (сырое поле гироскопа, поле угла от полётника, имя).
OSI = (("gyro_y", "fc_pitch", "ТАНГАЖ"),
       ("gyro_x", "fc_roll", "КРЕН"),
       ("gyro_z", "fc_yaw", "РЫСКАНИЕ"))

# Скачок угла больше этого за кадр — не движение, а сбой оценки или пропуск.
# Тот же порог, что в tools/znak_ryskaniya.py.
SKACHOK_MAX_DEG = 30.0


def nastroyki():
    """GYRO_UNIT_DPS и GYRO_FRESH_S из настоящего кода (с local_settings)."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import offline
        t = offline.load_tracker()
        return float(t.GYRO_UNIT_DPS), float(t.GYRO_FRESH_S)
    except Exception:
        pass
    # Не удалось импортировать трекер (нет cv2/numpy) — берём из текста.
    src = open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
    unit = float(re.search(r"^GYRO_UNIT_DPS = ([\d.eE+-]+)", src, re.M).group(1))
    fresh = float(re.search(r"^GYRO_FRESH_S = ([\d.eE+-]+)", src, re.M).group(1))
    return unit, fresh


def chislo(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def raznica_ugla(a, b, zamknut):
    """Изменение угла; для рыскания приводим к ±180°."""
    d = b - a
    if zamknut:
        while d > 180.0:
            d -= 360.0
        while d < -180.0:
            d += 360.0
    return d


def najti_stroki(arg):
    """Аргумент — папка захода или сам строки.csv."""
    if os.path.isfile(arg):
        return arg
    for imya in ("строки.csv", "stroki.csv"):
        p = os.path.join(arg, imya)
        if os.path.exists(p):
            return p
    hits = sorted(glob.glob(os.path.join(arg, "*строки*.csv")))
    return hits[0] if hits else None


def prochitat(put):
    with open(put, encoding="utf-8", errors="replace") as fh:
        return list(csv.DictReader(fh))


def protsentil(xs, q):
    if not xs:
        return None
    ys = sorted(xs)
    i = q * (len(ys) - 1)
    lo = int(math.floor(i))
    hi = min(lo + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (i - lo)


def razbor_osi(rows, gyro_pole, ugol_pole, zamknut, unit, fresh_s):
    """Пары (gyro, скорость угла) + интеграл + статистика сырых единиц."""
    pary = []
    gyro_syroy = []
    vozrasta = []
    integral = 0.0        # Σ gyro·unit·dt, в градусах
    integral_ugol = 0.0   # Σ фактического изменения угла, в градусах
    for i in range(1, len(rows)):
        g = chislo(rows[i].get(gyro_pole))
        dt = chislo(rows[i].get("dt_ms"))
        u0 = chislo(rows[i - 1].get(ugol_pole))
        u1 = chislo(rows[i].get(ugol_pole))
        if None in (g, dt, u0, u1) or dt <= 0:
            continue
        gyro_syroy.append(g)
        v = chislo(rows[i].get("gyro_age_ms"))
        if v is not None:
            vozrasta.append(v)
        d = raznica_ugla(u0, u1, zamknut)
        if abs(d) > SKACHOK_MAX_DEG:
            continue
        dt_s = dt / 1000.0
        integral += g * unit * dt_s
        integral_ugol += d
        pary.append((g, d / dt_s))
    return {
        "pary": pary,
        "gyro": gyro_syroy,
        "vozrasta": vozrasta,
        "integral": integral,
        "integral_ugol": integral_ugol,
        "fresh_s": fresh_s,
    }


def regressiya(pary):
    xs = [a for a, _ in pary]
    ys = [b for _, b in pary]
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return {"naklon": sxy / sxx, "r": sxy / math.sqrt(sxx * syy), "n": n,
            "rms_y": math.sqrt(syy / n)}


def rms(xs):
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else None


def pechat_osi(imya, d, unit):
    print()
    print("=== %s ===" % imya)
    pary = d["pary"]
    reg = regressiya(pary)
    if reg is None:
        print("    пар меньше трёх — мерить нечего (нет %s в логе?)" % imya)
        return None
    g = d["gyro"]
    print("    кадров: %d, в регрессии: %d" % (len(g), reg["n"]))
    print("    масштаб из регрессии: %+.4f (°/с)/ед   r=%+.2f"
          % (reg["naklon"], reg["r"]))
    print("    в коде GYRO_UNIT_DPS: %+.4f (°/с)/ед" % unit)
    if abs(reg["naklon"]) > 1e-6:
        print("    расхождение: ×%.2f%s"
              % (unit / reg["naklon"],
                 "" if reg["naklon"] * unit > 0 else "  (знак противоположный)"))
    print("    интеграл: Σ gyro·unit·dt = %+8.1f°   факт Δугла = %+8.1f°"
          % (d["integral"], d["integral_ugol"]))
    if abs(d["integral_ugol"]) > 1.0:
        print("              отношение %+6.2f (должно быть ≈ +1)"
              % (d["integral"] / d["integral_ugol"]))
    print("    сырые единицы: медиана %+.0f, p10 %+.0f, p90 %+.0f, RMS %.0f"
          % (protsentil(g, 0.5), protsentil(g, 0.1), protsentil(g, 0.9),
             rms(g)))
    print("    в °/с при текущем масштабе: RMS %.1f, p90 |%.1f|"
          % (rms(g) * unit, protsentil([abs(x) for x in g], 0.9) * unit))
    print("    фактическая скорость угла: RMS %.1f °/с" % reg["rms_y"])
    if d["vozrasta"]:
        st = sum(1 for v in d["vozrasta"] if v / 1000.0 > d["fresh_s"])
        print("    возраст гироскопа: медиана %.0f мс, старше GYRO_FRESH_S "
              "(%.0f мс) — %.1f%% кадров"
              % (protsentil(d["vozrasta"], 0.5), d["fresh_s"] * 1000,
                 100.0 * st / len(d["vozrasta"])))
    return reg


def main():
    unit, fresh_s = nastroyki()
    if len(sys.argv) > 1:
        args = sys.argv[1:]
    else:
        baza = os.path.expanduser("~/fpv_tracker/flight_logs/zahvaty")
        args = sorted(glob.glob(os.path.join(baza, "*")),
                      key=os.path.getmtime)[-6:]
    fayly = []
    for a in args:
        p = najti_stroki(a)
        if p:
            fayly.append(p)
        else:
            print("пропущено (строки.csv не найден): %s" % a)
    if not fayly:
        print("нечего разбирать: нужны папки заходов или файлы строки.csv")
        return 1

    print("GYRO_UNIT_DPS = %.4f, GYRO_FRESH_S = %.2f с" % (unit, fresh_s))
    print("записей: %d" % len(fayly))

    # Наклоны и знаки по каждой оси, чтобы сравнить ЗАПИСИ между собой:
    # масштаб обязан быть одним, а знак — устойчивым. Разброс знака между
    # записями означает, что измеряется не гироскоп, а шум.
    itogi = {}
    for f in fayly:
        rows = prochitat(f)
        print()
        print("########## %s (%d кадров) ##########" % (os.path.basename(f),
                                                        len(rows)))
        for gyro_pole, ugol_pole, imya in OSI:
            d = razbor_osi(rows, gyro_pole, ugol_pole, gyro_pole == "gyro_z",
                           unit, fresh_s)
            reg = pechat_osi(imya, d, unit)
            if reg is not None:
                itogi.setdefault(imya, []).append(
                    (os.path.basename(f), reg["naklon"], reg["r"],
                     d["integral"], d["integral_ugol"]))

    print()
    print("================= ИТОГ ПО ЗАПИСЯМ =================")
    verdict_ok = True
    rekomendatsii = []
    for imya, sp in itogi.items():
        print()
        print("--- %s ---" % imya)
        naklony = []
        for f, naklon, r, integ, integ_u in sp:
            print("    %-24s %+.4f (°/с)/ед  r=%+.2f  интеграл %+.1f° при "
                  "Δ %.1f°" % (f, naklon, r, integ, integ_u))
            # В итог идут только записи с возбуждением: без него наклон —
            # это шум, и среднее по шуму ничего не значит.
            if abs(r) >= 0.25:
                naklony.append(naklon)
        if not naklony:
            print("    НИ ОДНОЙ записи с |r| ≥ 0.25: возбуждения не хватило.")
            print("    Нужна стендовая запись (см. ЧТО НУЖНО ОТ ЗАПИСИ выше).")
            verdict_ok = False
            continue
        sred = sum(naklony) / len(naklony)
        razbros = max(naklony) - min(naklony)
        print("    записей с годной связью: %d, средний масштаб %+.4f, "
              "разброс %.4f" % (len(naklony), sred, razbros))
        if min(naklony) * max(naklony) < 0:
            print("    ЗНАК ПЛАВАЕТ между записями — по такому замеру")
            print("    включать демпфирование нельзя.")
            verdict_ok = False
            continue
        if abs(sred) < 1e-6:
            verdict_ok = False
            continue
        otn = sred / unit
        print("    против GYRO_UNIT_DPS = %.4f:  ×%.2f" % (unit, otn))
        if 0.8 <= otn <= 1.25:
            print("    масштаб сходится в пределах 20–25%, правка не нужна.")
        else:
            print("    масштаб НЕ сходится.")
            rekomendatsii.append("GYRO_UNIT_DPS = %.4f" % sred)
            verdict_ok = False

    print()
    if verdict_ok and not rekomendatsii:
        print("ВЫВОД: гироскоп согласован с GYRO_UNIT_DPS, знак устойчив.")
        return 0
    print("ВЫВОД: гироскоп НЕ согласован с тем, что стоит в коде.")
    print("Два потребителя гироскопа сейчас работают неверно:")
    print("  • демпфер скорости (PITCH_RATE_DAMP — вся производная по тангажу);")
    print("  • защита «не считаем λ̇ при вращении» (LOS_MAX_VRASH_DPS).")
    print("Экстраполяция тангажа выключена (EXTRAP_TANGAZH = False) и не")
    print("пострадает; включать её до этого замера нельзя.")
    if rekomendatsii:
        print()
        print("Если замер сделан на стендовой записи с медленным наклоном,")
        print("правка пишется так (tracker.py не трогать):")
        for r in rekomendatsii:
            print("    echo '%s' >> ~/fpv_tracker/local_settings.py" % r)
        print("    sudo systemctl restart tracker")
    else:
        print()
        print("Числа для правки брать неоткуда: ни одна запись не дала")
        print("связи |r| ≥ 0.25. Сначала стенд, потом повторный прогон.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
