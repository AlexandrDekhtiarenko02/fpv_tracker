"""Потолок тангажа, ранний сброс газа, интегратор и упреждение крена.

Заходы 34–43: стик тангажа выше 1500 докручивал нос до 50–60°,
газ держал стик захвата, крен догонял рамку.
"""
import math
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

print("=== 6. Крен правит курс: рамка и бег, азимут молчит ===")
now = 1000.0
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
# Крестик в центре, азимут крутится — азимут крен не кладёт.
for _ in range(int(2 * t.NOMINAL_FPS)):
    now += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(2.0, now, 0.0, 0.0, now, 1.0)
assert abs(t._pn_bank_tek) < 1.0, t._pn_bank_tek
# Крестик почти в центре, но рамка бежит — курс мимо, крен нужен.
# |dx| держим в тихой зоне, а pred каждый кадр отстаёт — так оценка
# скорости остаётся выше мёртвой, как при уводе с носом на цели.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_rate = 0.0
now_b = now
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_b += 1.0 / t.NOMINAL_FPS
    t._pn_dx_pred = 3.0 - 50.0 / t.NOMINAL_FPS
    t._pn_dx_t = now_b - 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_b, 0.0, 3.0, now_b, 1.0)
assert t._pn_bank_tek > 2.0, t._pn_bank_tek
assert t._pn_bank_tek <= t.PN_ROLL_HOLD_FAST + 1e-6, t._pn_bank_tek
# Крестик в центре и бег тихий — крен в горизонт.
t._pn_bank_i = 0.0
t._pn_bank_tek = 8.0
t._pn_dx_pred = 1.0
t._pn_dx_t = now
t._pn_dx_rate = 0.0
now_q = now
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_q += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.1, now_q, 0.0, 1.0, now_q, 1.0)
assert abs(t._pn_bank_tek) < 1.0, t._pn_bank_tek
# Рамка справа — крен вправо, плавно, не поздний удар. Азимут влево молчит.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
now_d = now
shag = t.PN_ROLL_BANK_SLEW / t.NOMINAL_FPS + 1e-6
pred = 0.0
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_d += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(-4.0, now_d, 0.0, 30.0, now_d, 1.0)
    assert abs(t._pn_bank_tek - pred) <= shag + 1e-6
    pred = t._pn_bank_tek
assert 4.0 <= t._pn_bank_tek <= t.PN_ROLL_HOLD_MAX, t._pn_bank_tek
# Рамка слева — крен влево, даже если азимут смотрит вправо.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
now_l = now
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_l += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(4.0, now_l, 0.0, -30.0, now_l, 1.0)
assert t._pn_bank_tek < -4.0, t._pn_bank_tek
# Середина захода |dx| 5–15 не должна оставаться с креном 1–4°.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
now_m = now
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_m += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_m, 0.0, 12.0, now_m, 1.0)
assert t._pn_bank_tek > 4.0, t._pn_bank_tek
assert t._pn_bank_tek <= t.PN_ROLL_HOLD_MAX + 1e-6, t._pn_bank_tek
assert t._kren_na_kurs(4.0, now, None, 40.0, now, 1.0) == 0.0
assert t.FF_GAIN_ROLL == 0.0
assert t.ROLL_LEAD_S >= 0.4 - 1e-9
assert t.LEAD_FRAMES == 8

print("=== 6b. Бегущая рамка — крен больше, но не удар ===")
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
now_r = 2000.0
dx_r = 20.0
for _ in range(int(1.6 * t.NOMINAL_FPS)):
    now_r += 1.0 / t.NOMINAL_FPS
    dx_r += 80.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_r, 0.0, dx_r, now_r, 1.0)
# Положение одно уже упёрлось бы в 12°. Скорость поднимает потолок, не до 22°.
assert t.PN_ROLL_HOLD_MAX < t._pn_bank_tek <= t.PN_ROLL_HOLD_FAST + 1e-6, (
    t._pn_bank_tek)
# Откат часов огромную скорость не рисует: оценка остаётся прежней.
rate_do = t._pn_dx_rate
t._kren_na_kurs(0.0, now_r - 0.01, 0.0, dx_r + 500.0, now_r - 0.01, 1.0)
assert t._pn_dx_rate == rate_do, t._pn_dx_rate
# Дырка больше полусекунды — не рывок, а новый отсчёт.
t._kren_na_kurs(0.0, now_r + 2.0, 0.0, dx_r + 500.0, now_r + 2.0, 1.0)
assert abs(t._pn_dx_rate) <= abs(rate_do) * 0.5 + 1.0, t._pn_dx_rate
# Постоянное смещение после всплеска кадра возвращается к обычному потолку.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
now_s = 3000.0
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_s += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_s, 0.0, 30.0, now_s, 1.0)
assert 4.0 <= t._pn_bank_tek <= t.PN_ROLL_HOLD_MAX + 1e-6, t._pn_bank_tek
# Один кадр далеко — не оставляем крен на быстром потолке.
now_s += 1.0 / t.NOMINAL_FPS
t._kren_na_kurs(0.0, now_s, 0.0, 230.0, now_s, 1.0)
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_s += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_s, 0.0, 30.0, now_s, 1.0)
assert t._pn_bank_tek <= t.PN_ROLL_HOLD_MAX + 1e-6, t._pn_bank_tek

print("=== 7. Крен возвращает горизонт, а не держит бок ===")
# 20 ШИМ слежения при банке +15°: уровень тянет назад на 80, не даёт лежать.
uroven = t._kren_k_gorizontu(100.0, 15.0)
assert uroven < 0.0, uroven
assert t._kren_k_gorizontu(0.0, 0.0) == 0.0
assert t._kren_k_gorizontu(100.0, None) == 20.0

print("=== 8. Тангаж: нос ниже цели, угол по высоте, не по скорости линии ===")
t._pn_nos_i = 0.0
t._pn_nos_tek = None
t._pn_nos_ugol = None
t._pn_nos_blizko = 0.0
# Цель в центре, линия не крутая: нос опускается ниже цели.
pwm_n = t._nos_na_kurs(0.0, now, 18.0, 18.0, 0.0, now, 1.0, 18.0)
assert pwm_n > 0.0, pwm_n
# Уже ниже цели, высота стоит: стик отпущен, угол держится.
dpp = t.CAMERA_VFOV_DEG / float(t.MAIN_H)
dy_nizhe = -5.0 / dpp
t._pn_nos_ugol = 23.0
t._pn_nos_tek = 23.0
derzh = t._nos_na_kurs(0.0, now, 23.0, 18.0, dy_nizhe, now, 1.0, 18.0, 0.0)
assert abs(derzh) < 8.0, derzh
# Крутая линия, цель в центре: запас не нужен, вниз не дожимаем.
t._pn_nos_ugol = None
t._pn_nos_tek = 32.0
krut = t._nos_na_kurs(0.0, now, 32.0, 18.0, 0.0, now, 1.0, 32.0, 0.0)
assert abs(krut) < 8.0, krut
# Всплеск скорости линии сразу после лока нос не клюёт.
# На cfe6537 +6 °/с клали ещё до +8° за три секунды.
t._pn_nos_ugol = 23.0
t._pn_nos_tek = 23.0
now_v = 4000.0
for _ in range(int(1.5 * t.NOMINAL_FPS)):
    now_v += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(6.0, now_v, 23.0, 18.0, dy_nizhe, now_v, 1.0, 18.0, 0.0)
assert abs(t._pn_nos_ugol - 23.0) < 0.3, t._pn_nos_ugol
# Подымается — носа мало, угол растёт.
t._pn_nos_ugol = 23.0
t._pn_nos_tek = 23.0
now_c = 5000.0
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_c += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(0.0, now_c, 23.0, 18.0, dy_nizhe, now_c, 1.0, 18.0, 80.0)
assert t._pn_nos_ugol > 24.5, t._pn_nos_ugol
# Снижается при крутом носе — нос лишний, угол убывает.
dy_kruto = -16.0 / dpp
t._pn_nos_ugol = 34.0
t._pn_nos_tek = 34.0
now_s = 6000.0
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_s += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(0.0, now_s, 34.0, 18.0, dy_kruto, now_s, 1.0, 18.0, -80.0)
assert t._pn_nos_ugol < 32.5, t._pn_nos_ugol
# Снижается при некрутом носе — нос не трогаем, это газ.
t._pn_nos_ugol = 23.0
t._pn_nos_tek = 23.0
now_m = 6500.0
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_m += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(0.0, now_m, 23.0, 18.0, dy_nizhe, now_m, 1.0, 18.0, -80.0)
assert abs(t._pn_nos_ugol - 23.0) < 0.3, t._pn_nos_ugol
# Потолок 45° не поднимается.
t._pn_nos_ugol = 44.5
t._pn_nos_tek = 44.5
now_p = 7000.0
for _ in range(int(2.0 * t.NOMINAL_FPS)):
    now_p += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(0.0, now_p, 44.5, 18.0, 0.0, now_p, 1.0, 18.0, 200.0)
assert t._pn_nos_ugol <= t.PN_PITCH_ABS_MAX + 1e-6, t._pn_nos_ugol

print("=== 8b. Газ компенсирует высоту, опора — стик входа ===")
# Высота стоит — газ на стике входа.
rovno = t._gaz_po_linii(1350, 0.0)
assert rovno == 1350.0, rovno
# Снижаемся — газ выше, но не дальше потолка прибавки.
proval = t._gaz_po_linii(1350, -600.0)
assert 1350.0 < proval <= 1350.0 + t.GAZ_VYSHE, proval
# Подымаемся — это нос: газ на опоре, вверх не идёт и вниз не режет.
nabor = t._gaz_po_linii(1350, 200.0)
assert nabor == 1350.0, nabor
# Перелёт в первую секунду по скорости линии газ не режет.
rannee = t._gaz_po_linii(1350, 0.0, aim_below=5.0, los_rate=6.0, age_s=0.5)
assert rannee == 1350.0, rannee
# Перелёт после двух секунд — можно убавить, если не снижаемся.
pozze = t._gaz_po_linii(1350, 0.0, aim_below=5.0, los_rate=2.0, age_s=3.0)
assert pozze == 1350.0 - t.GAZ_PERELET_PWM, pozze
# Снижаемся и перелетаем — газ не режем, снижение просит прибавку.
vmeste = t._gaz_po_linii(1350, -200.0, aim_below=-5.0, los_rate=2.0, age_s=3.0)
assert vmeste > 1350.0, vmeste
# Нет вариометра или стика — ручку не выдумываем.
assert t._gaz_po_linii(1350, None) is None
assert t._gaz_po_linii(None, -100.0) is None
# Стик входа ниже ховера — пол ховер, иначе заход снижается.
nizhe = t._gaz_po_linii(1250, 0.0, hover=1400)
assert nizhe == 1400.0, nizhe
# Стик входа выше ховера — ховер пол не поднимает.
vyshe = t._gaz_po_linii(1450, 0.0, hover=1400)
assert vyshe == 1450.0, vyshe
# Путевая в газ не входит: подпись больше не принимает скорость земли.

print("=== 8c. Близко сброс газа — нос чуть ниже, потолок тот же ===")
assert t._dobor_nosa_u_sbrosa(1400, 1320, 90.0) == 80.0 / 40.0
assert t._dobor_nosa_u_sbrosa(1400, 1200, 90.0) == t.PN_PITCH_BLIZKO_MAX
assert t._dobor_nosa_u_sbrosa(1400, 1390, 90.0) == 0.0
assert t._dobor_nosa_u_sbrosa(1400, 1300, 200.0) == 0.0
assert t._dobor_nosa_u_sbrosa(1400, 1300, None) == 0.0
t._pn_nos_blizko = 0.0
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    t._primenit_nos_u_sbrosa(1400, 1280, 80.0, 1.0)
assert abs(t._pn_nos_blizko - t.PN_PITCH_BLIZKO_MAX) < 1e-6, t._pn_nos_blizko
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    t._primenit_nos_u_sbrosa(1400, 1280, 200.0, 1.0)
assert t._pn_nos_blizko < 0.3, t._pn_nos_blizko
t._pn_nos_blizko = 3.0
t._pn_nos_ugol = 30.0
t._pn_nos_tek = 33.0
now_b = 7000.0
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_b += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(0.0, now_b, 30.0, 16.0, dy_nizhe, now_b, 1.0, 18.0, 0.0)
assert abs(t._pn_nos_tek - 33.0) < 0.5, t._pn_nos_tek
t._pn_nos_blizko = 3.0
t._pn_nos_ugol = 44.0
t._pn_nos_tek = 44.0
now_c = 8000.0
for _ in range(10):
    now_c += 1.0 / t.NOMINAL_FPS
    t._nos_na_kurs(2.0, now_c, 44.0, 16.0, dy_nizhe, now_c, 1.0, 18.0, 0.0)
assert t._pn_nos_tek <= t.PN_PITCH_ABS_MAX + 1e-6, t._pn_nos_tek
t._pn_nos_blizko = 0.0

print("=== 9. Рыскание — триммер крена, рамку не тянет ===")
assert t._ryaskan_na_kurs(0.0, 10.0) == 0.0
assert t._ryaskan_na_kurs(1.0, 20.0) == 0.0
assert t._ryaskan_na_kurs(0.0, 120.0) == 0.0
v_kren = t._ryaskan_na_kurs(8.0, 0.0)
assert 0 < v_kren <= 5.0, v_kren
assert t._ryaskan_na_kurs(-8.0, 0.0) == -v_kren

print("=== 10. Газ снижает так, чтобы высота кончилась вместе с дальностью ===")
# 40 м / 10 с = 4 м/с. Вариометра нет — газ ниже висения, не в пол.
g = t._gaz_po_vysote(1500, 40.0, 10.0, None)
assert g is not None and 1500 - t.GAZ_NIZHE_MAX <= g < 1500, g
# Уже снижаемся как надо — газ на висении.
rovno = t._gaz_po_vysote(1500, 40.0, 10.0, -400.0)
assert abs(rovno - 1500) < 1.0, rovno
# Снижаемся быстрее, чем нужно — не выше висения.
vyshe = t._gaz_po_vysote(1500, 40.0, 10.0, -900.0)
assert vyshe <= 1500.0, vyshe
# Мусорное время закон не включает.
assert t._gaz_po_vysote(1500, 40.0, 0.2, 0.0) is None
assert t._gaz_po_vysote(1500, 40.0, None, 0.0) is None

print("=== 11. Живой контур отдаёт закон, а не старый потолок ===")
# Высоко и круто старый код зажимал тангаж в 1500 и добавлял чужой наклон.
# Здесь нос ещё на угле захвата, угол визирования растёт на 2.5 °/с,
# высота 40 м, время 10 с, снижения нет.


class _Chasy:
    def __init__(self):
        self.t = 8000.0

    def monotonic(self):
        return self.t

    def tick(self):
        self.t += 1.0 / 30.0


_clk = _Chasy()
_vremya = t.time.monotonic
_zamena = t._estimate_closure
t.time.monotonic = _clk.monotonic
_dep = {"v": 18.0}


def _podmena(*a, **kw):
    out = dict(_zamena(*a, **kw))
    out["tau_s"] = 10.0
    out["depression_deg"] = 18.0
    out["range_m"] = 200.0
    out["tau_sigma_s"] = 0.2
    return out


t._estimate_closure = _podmena
CX, CY = t.CENTER_X, t.CENTER_Y
with t.state_lock:
    t.target_controllable = False
    t.target_box_main = None
t.update_control_from_target()
t._dep_hist.clear()
t._hover_pwm = 1500.0
t._thr_geom_out = None
t._gyro_y_sgl = 0.0
t._az_skorost = 0.0
t._az_skorost_ts = 0.0
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
t._pn_dx_pred = None
t._pn_dx_t = 0.0
t._pn_dx_rate = 0.0
t._pn_nos_i = 0.0
t._pn_nos_tek = None
t._pn_nos_blizko = 0.0


def _kadr(pitch=20.0, roll=0.0, dx=0.0, vario=0, dy=0.0):
    t.target_box_main = (CX - 20 + dx, CY - 20 + dy, CX + 20 + dx, CY + 20 + dy)
    t.target_controllable = True
    t.target_visible = True
    t.last_match_score = 0.9
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 40.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1500
        t.app_state["rc_throttle_ts"] = _clk.t
        t.app_state["fc_pitch_deg"] = pitch
        t.app_state["fc_pitch_ts"] = _clk.t
        t.app_state["fc_roll_deg"] = roll
        t.app_state["alt_cm"] = 4000
        t.app_state["alt_ts"] = _clk.t
        t.app_state["vario_cms"] = vario
        t.app_state["gyro"] = (0.0, 0.0, 0.0)
        t.app_state["imu_ts"] = _clk.t
        t.app_state["armed"] = False
    _clk.tick()
    t.update_control_from_target()


for _ in range(90):
    _kadr(pitch=20.0, roll=0.0, dx=0.0)
print("    тангаж %s рыскание %s газ %s крен %s" % (
    t.global_pitch_cmd, t.global_yaw_cmd, t.global_throttle_cmd,
    t.global_roll_cmd))
# Обычная линия, цель в центре: стик опускает нос на запас выше центра.
assert t.global_pitch_cmd >= 1520, t.global_pitch_cmd
# Без крена и без цели у края нос не крутим.
assert abs(t.global_yaw_cmd - 1500) <= 2, t.global_yaw_cmd
# Без путевой газ остаётся на стике входа. Убавка «линия растёт» снята:
# на 4910596 она только сажала газ и перелёт не убирала.
assert abs(t.global_throttle_cmd - 1500) <= 40, t.global_throttle_cmd
for _ in range(6):
    _kadr(pitch=20.0, roll=12.0, dx=0.0)
print("    крен %s при банке +12, рыскание %s" % (
    t.global_roll_cmd, t.global_yaw_cmd))
# Крен выравнивает крыло. Рыскание идёт за банком, не против него.
assert t.global_roll_cmd < 1480, t.global_roll_cmd
assert 1500 < t.global_yaw_cmd <= 1508, t.global_yaw_cmd


def _podmena_blizko(*a, **kw):
    out = dict(_zamena(*a, **kw))
    out["tau_s"] = 10.0
    out["depression_deg"] = 18.0
    out["range_m"] = 80.0
    out["tau_sigma_s"] = 0.2
    return out


# Близко и перелёт (цель ниже центра), высота стоит: газ можно убавить,
# и нос чуть помогает, чтобы не вышло дуги. Путевую не слушаем.
t._estimate_closure = _podmena_blizko
t.ground_speed_mps = 10.0
tek_do = t._pn_nos_tek
for _ in range(45):
    _kadr(pitch=20.0, roll=0.0, dx=0.0, vario=0, dy=60.0)
print("    близко нос %.2f (было %.2f), добавка %.2f, газ %s" % (
    t._pn_nos_tek, tek_do, t._pn_nos_blizko, t.global_throttle_cmd))
assert t._pn_nos_blizko > 0.5, t._pn_nos_blizko
assert t._pn_nos_tek <= t.PN_PITCH_ABS_MAX
assert t.global_throttle_cmd < 1500, t.global_throttle_cmd
t.ground_speed_mps = None
t.time.monotonic = _vremya
t._estimate_closure = _zamena
print("ok")
