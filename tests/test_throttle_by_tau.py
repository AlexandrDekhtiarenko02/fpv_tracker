"""Газ работает по ВРЕМЕНИ до контакта, а не по пикселям в кадре.

Замерено по 46 заходам, как газом работал живой пилот: скорость выросла в 1.6
раза, тангаж на 5°, а газ меньше чем на 1%. Связь газа с вертикальной
скоростью r = 0.00 — снижение получалось от наклона, не от газа. То есть газ
у пилота распорядитель энергии, а не следящая ось.

А прежний код подруливал газом по пикселям кадр за кадром, лез в работу
тангажа и не трогал ручку лишь в 23% кадров. Новый закон не трогает в 75%.

Почему по времени, а не по дальности: на 60-90 м время до контакта разбросано
4.6-12.3 с, на 130-200 м — 1.9-12.5 с. Диапазоны накладываются, по дальности
нельзя понять, у тебя две секунды или двенадцать.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()


def popravka(tau, sigma=None):
    """Тот же закон, что в коде."""
    if tau is None:
        return 0.0
    if tau < t.TAU_THROTTLE_LO_S:
        a = -(t.TAU_THROTTLE_LO_S - tau) * t.TAU_THROTTLE_GAIN
    elif tau > t.TAU_THROTTLE_HI_S:
        a = (tau - t.TAU_THROTTLE_HI_S) * t.TAU_THROTTLE_GAIN
    else:
        a = 0.0
    if sigma is not None and tau > 0.1:
        a *= 1.0 / (1.0 + (sigma / tau) ** 2)
    return max(-t.TAU_THROTTLE_MAX, min(t.TAU_THROTTLE_MAX, a))


print("=== 1. Между границами газ НЕ трогается ===")
# Это главное отличие от прежнего закона: широкая мёртвая зона.
assert t.TAU_THROTTLE_HI_S - t.TAU_THROTTLE_LO_S >= 3.0, (
    "мёртвая зона %.1f с слишком узка — газ снова начнёт подруливать "
    "постоянно, как по пикселям"
    % (t.TAU_THROTTLE_HI_S - t.TAU_THROTTLE_LO_S))
for tau in (t.TAU_THROTTLE_LO_S, 5.0, 7.0, t.TAU_THROTTLE_HI_S):
    a = popravka(tau)
    print("    tau %.1f с -> поправка %+.0f" % (tau, a))
    assert a == 0.0, "внутри границ газ обязан молчать"

print("\n=== 2. Знак верный по обе стороны ===")
bystro = popravka(t.TAU_THROTTLE_LO_S - 2.0)
medlenno = popravka(t.TAU_THROTTLE_HI_S + 2.0)
print("    слишком быстро -> %+.0f (сброс)" % bystro)
print("    заход тянется  -> %+.0f (добавить)" % medlenno)
assert bystro < 0, "при нехватке времени газ обязан УБАВЛЯТЬСЯ"
assert medlenno > 0, "при затянутом заходе газ обязан ДОБАВЛЯТЬСЯ"

print("\n=== 3. Ненадёжному времени газ не верит ===")
# Замерено: на вялом сближении рамка почти не растёт и время врёт в разы.
tau = 1.0
tochno = abs(popravka(tau, sigma=0.05))
gruibo = abs(popravka(tau, sigma=3.0))
print("    tau 1.0±0.05 -> %.0f | tau 1.0±3.0 -> %.0f" % (tochno, gruibo))
assert gruibo < 0.3 * tochno, (
    "при погрешности втрое больше самой величины поправка обязана почти "
    "исчезнуть, а вышло %.0f против %.0f" % (gruibo, tochno))

print("\n=== 4. Поправка ограничена ===")
a = abs(popravka(0.0))
b = abs(popravka(60.0))
print("    крайние случаи: %.0f и %.0f (предел %.0f)" % (a, b, t.TAU_THROTTLE_MAX))
assert a <= t.TAU_THROTTLE_MAX and b <= t.TAU_THROTTLE_MAX
assert t.TAU_THROTTLE_MAX <= 200.0, (
    "предел %.0f слишком велик: газ сможет перебить пилота" % t.TAU_THROTTLE_MAX)

print("\n=== 5. Удержание времени: газ не слепнет от пропажи ===")
# Время заполнено лишь в 51% кадров. Без удержания газ бездействовал бы
# половину кадров не по решению, а от незнания.
assert hasattr(t, "TAU_HOLD_S"), "нет удержания времени"
print("    удержание %.1f с" % t.TAU_HOLD_S)
assert 0.5 <= t.TAU_HOLD_S <= 3.0, (
    "удержание %.1f с вне разумного: слишком долго — газ пойдёт по протухшей "
    "оценке" % t.TAU_HOLD_S)

print("\n=== 6. Режим сближения по времени, а не по доле кадра ===")
# Замерено: доля кадра доходила до порога 0.18 лишь в 1.2% кадров, то есть
# режим не включался практически никогда.
assert hasattr(t, "CLOSING_TAU_S"), "нет порога по времени"
print("    порог %.1f с (доля кадра осталась запасным признаком)" % t.CLOSING_TAU_S)
assert 1.0 <= t.CLOSING_TAU_S <= 8.0, "порог вне разумного"
assert t.CLOSING_TAU_S <= t.TAU_THROTTLE_HI_S, (
    "режим сближения включается позже, чем газ начинает считать заход "
    "затянутым — это противоречие")

print("\nOK: газ распоряжается энергией, а не подруливает по пикселям")
