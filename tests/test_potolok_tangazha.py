"""Потолок тангажа, ранний сброс газа, интегратор и упреждение крена.

Заходы 34–43: стик тангажа выше 1500 докручивал нос до 50–60°,
газ держал стик захвата, крен догонял рамку.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import offline

t = offline.load_tracker()

print("=== 1. Потолок: +10° от захвата ===")
assert t._potolok_tangazha(30.0, 18.0) is True
assert t._potolok_tangazha(28.0, 18.0) is False
assert t._potolok_tangazha(None, 18.0) is False

print("=== 2. Интегратор сбрасывается, когда сырая цель уже не просит ===")
assert t._sbros_integrala(30.0, -20.0) == 0.0
assert t._sbros_integrala(30.0, 0.0) == 0.0
assert t._sbros_integrala(30.0, 12.0) == 30.0
assert t._sbros_integrala(-10.0, 5.0) == 0.0
assert t._sbros_integrala(-10.0, -4.0) == -10.0

print("=== 3. Тангаж не копит положительные P и I выше потолка ===")
# Ошибка вниз 40 px, интегратор уже +25. Сырая цель выше центра.
t.pitch_integral = 25.0
off, prev, integ = t._pid_axis_step(
    40.0, 40.0, 25.0, 0.0,
    t.P_GAIN_PITCH, 0.0, t.I_GAIN_PITCH, 0.0,
    t.PITCH_INTEGRAL_MAX, 1.0, t.PITCH_SIGN, t.MAX_PITCH_DEFLECT,
    dbg_key="pitch", k=1.0)
assert integ > 0.0 and off > 0.0
p_ch = t._pid_dbg["pitch"][0]
integ = t._sbros_integrala(integ, -15.0)
assert integ == 0.0
if p_ch > 0.0:
    p_ch = 0.0
off = t.PITCH_SIGN * (p_ch + integ)
assert off == 0.0, off
assert 1500 + off <= 1500

print("=== 4. Сброс газа только близко или при ушедшем носе ===")
# Далеко, даже с коротким tau и просадкой — не трогаем.
# Так 5-й заход 8efea18 сел в 1200 при 400 м и наборе.
assert t._srez_gaza(1560, 400.0, 0.4, 12.0, 12.0, 500.0, True) is None
assert t._srez_gaza(1560, 200.0, 7.0, 20.0, 18.0, -350.0, False) is None
assert t._srez_gaza(1433, 402.0, 0.5, 12.7, 12.1, 500.0, True) is None
# Близко или нос ниже захвата на 8° — потолок 1280, не выше стика.
assert t._srez_gaza(1560, 80.0, 12.0, 20.0, 18.0, 0.0, False) == 1280
assert t._srez_gaza(1560, 140.0, 3.0, 20.0, 18.0, 0.0, False) == 1280
assert t._srez_gaza(1560, 250.0, 12.0, 28.0, 18.0, 0.0, False) == 1280
assert t._srez_gaza(1250, 80.0, 3.0, 20.0, 18.0, 0.0, False) == 1250
# 1200: нос ушёл на 12° или уже близко и просадка быстрее 8 м/с.
assert t._srez_gaza(1560, 200.0, 12.0, 32.0, 18.0, 0.0, False) == 1200
assert t._srez_gaza(1560, 70.0, 3.0, 20.0, 18.0, -900.0, False) == 1200
# Далёкая просадка, даже быстрее 8 м/с, газ не сажает.
assert t._srez_gaza(1560, 250.0, 12.0, 20.0, 18.0, -900.0, False) is None

print("=== 5. Прибавка газа запрещена только при низком носе ===")
assert t._pribavka_zapreshchena(24.0, 18.0, 10.0) is True   # +6°
assert t._pribavka_zapreshchena(20.0, 18.0, -500.0) is False  # просадка — можно
assert t._pribavka_zapreshchena(20.0, 18.0, 10.0) is False

print("=== 6. Крен держит крен курса, а не догоняет рамку ===")
now = 1000.0
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
# Скорость азимута есть, борт ещё ровный: стик кладёт крен, но не в упор.
pwm = t._kren_na_kurs(4.0, now, 0.0, 0.0, now, 1.0)
assert 0 < pwm <= t.PN_ROLL_BANK_SLEW / t.NOMINAL_FPS * t.PN_ROLL_PWM_PER_DEG + 1.0, pwm
# Крен уже равен желаемому: ручка отпущена, рамка у центра ничего не добавляет.
zhel = t.PN_ROLL_BANK_PER_DPS * (4.0 - t.LOS_AZ_DEADBAND_DPS)
t._pn_bank_i = 0.0
t._pn_bank_tek = zhel
derzhit = t._kren_na_kurs(4.0, now, zhel, 0.0, now, 1.0)
assert abs(derzhit) < 8.0, derzhit
t._pn_bank_i = 0.0
t._pn_bank_tek = zhel
u_centra = t._kren_na_kurs(4.0, now, zhel, 15.0, now, 1.0)
assert abs(u_centra - derzhit) < 1.0, (u_centra, derzhit)
# У края кадра поводок есть, но это не переклад.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
povodok = t._kren_na_kurs(0.0, now, 0.0, 160.0, now, 1.0)
assert 0 < povodok < 50.0, povodok
# Без крена с полётника стик молчит.
assert t._kren_na_kurs(4.0, now, None, 0.0, now, 1.0) == 0.0
assert t.FF_GAIN_ROLL == 0.0
assert t.ROLL_LEAD_S >= 0.4 - 1e-9
assert t.LEAD_FRAMES == 8

print("=== 7. Крен возвращает горизонт, а не держит бок ===")
# 20 ШИМ слежения при банке +15°: уровень тянет назад на 80, не даёт лежать.
uroven = t._kren_k_gorizontu(100.0, 15.0)
assert uroven < 0.0, uroven
assert t._kren_k_gorizontu(0.0, 0.0) == 0.0
assert t._kren_k_gorizontu(100.0, None) == 20.0
print("ok")
