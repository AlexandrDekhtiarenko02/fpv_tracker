"""Время до контакта обязано убывать на секунду за секунду.

Это проверка САМОЙ ОЦЕНКИ, без внешней истины: если она верна, то за секунду
полёта обязана уменьшиться на секунду. На реальных заходах выходило -0.41 при
квартилях от -10 до +1.6, то есть оценка не работала вовсе.

Метод был верен, а вход негоден: ростом считалось изменение КОРОБКИ, а коробка
не измерение — масштаб примеряется тремя ступенями (0.847/1.0/1.18) и потом
сглаживается. Производная ступенчатого ряда — мусор. Здесь ряд строится по
расширению точек потока: оно непрерывно.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()


def progon(tau0, fps=24.0, shum=0.0, stupeni=False, seed=1):
    """Сближение с постоянной скоростью: в момент t до контакта tau0 - t."""
    import random
    rnd = random.Random(seed)
    t._size_hist.clear()
    t._rassh_nakop = 0.0
    t._growth_raw.clear()
    dt = 1.0 / fps
    t0 = 1000.0
    out = []
    razmer = 20.0
    for i in range(int(fps * (tau0 - 1.0))):
        tek = t0 + i * dt
        tau_ist = tau0 - i * dt
        # Размер обратно пропорционален дальности: s = C / (V * tau).
        s_ist = 20.0 * tau0 / tau_ist
        if stupeni:
            # Как ведёт себя КОРОБКА: ступени масштаба плюс сглаживание.
            while s_ist / razmer >= 1.18:
                razmer *= 1.18
            izmer = razmer
        else:
            izmer = s_ist * (1.0 + rnd.gauss(0.0, shum))
        t._size_hist.append((tek, izmer))
        while t._size_hist and (tek - t._size_hist[0][0]) > t.TAU_FIT_WINDOW_S:
            t._size_hist.popleft()
        if len(t._size_hist) >= t.TAU_FIT_MIN_POINTS:
            xs = [x for x, _ in t._size_hist]
            ys = [math.log(v) for _, v in t._size_hist]
            n = len(xs)
            mx = sum(xs) / n
            my = sum(ys) / n
            den = sum((x - mx) ** 2 for x in xs)
            if den > 1e-6:
                nak = sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / den
                if nak > t.TAU_MIN_GROWTH_PER_S:
                    tau = 1.0 / nak - (tek - mx)
                    if tau > 0:
                        out.append((tek, tau, tau_ist))
    return out


def naklon(par):
    if len(par) < 8:
        return None
    xs = [a for a, _, _ in par]
    ys = [b for _, b, _ in par]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den if den else None


print("=== 1. Идеальный непрерывный ряд ===")
p = progon(tau0=10.0)
n = naklon(p)
osh = max(abs(b - c) for _, b, c in p)
print("    d(tau)/dt = %+.3f (должно -1), наибольшая ошибка %.2f с" % (n, osh))
assert abs(n + 1.0) < 0.08, "оценка не убывает как положено: %+.3f" % n
assert osh < 0.6, "оценка расходится с истиной на %.2f с" % osh

print("\n=== 2. Как вело себя ПРЕЖНЕЕ (ступени коробки) ===")
p_st = progon(tau0=10.0, stupeni=True)
n_st = naklon(p_st)
print("    d(tau)/dt = %s" % ("%+.3f" % n_st if n_st is not None else "не считается"))
assert n_st is None or abs(n_st + 1.0) > 0.25, (
    "ступенчатый ряд дал верный ответ — значит опыт не воспроизводит беду, "
    "и сравнивать не с чем")

print("\n=== 3. Непрерывный ряд с шумом измерения ===")
for sh in (0.01, 0.03):
    p = progon(tau0=10.0, shum=sh, seed=7)
    n = naklon(p)
    print("    шум %.0f%% -> d(tau)/dt = %+.3f" % (sh * 100, n))
    assert abs(n + 1.0) < 0.35, "при шуме %.0f%% оценка развалилась" % (sh * 100)

print("\n=== 4. Расширение за кадр — величина того же порядка ===")
# При tau = 5 с и 24 к/с рост за кадр = 1 + dt/tau.
ozhid = 1.0 + (1.0 / 24.0) / 5.0
print("    при tau=5 с расширение за кадр %.4f" % ozhid)
assert t.FLOW_RASSH_MIN < ozhid < t.FLOW_RASSH_MAX, (
    "пределы отсечки (%g..%g) режут нормальное сближение"
    % (t.FLOW_RASSH_MIN, t.FLOW_RASSH_MAX))
# А при tau = 0.5 с рост за кадр уже 1.083 — тоже обязан проходить.
kraynee = 1.0 + (1.0 / 24.0) / 0.5
print("    при tau=0.5 с расширение за кадр %.4f" % kraynee)
assert kraynee < t.FLOW_RASSH_MAX, (
    "верхний предел %g режет финал захода" % t.FLOW_RASSH_MAX)

print("\nOK: на непрерывном ряду оценка верна, на ступенчатом — нет")
