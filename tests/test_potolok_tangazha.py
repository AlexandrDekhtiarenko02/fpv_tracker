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

print("=== 6. Крен держит рамку, а не скорость азимута ===")
now = 1000.0
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
# Азимут сам по себе крен не кладёт.
pwm = t._kren_na_kurs(4.0, now, 0.0, 0.0, now, 1.0)
assert abs(pwm) < 1.0, pwm
# Рамка справа — крен вправо, плавно, не поздний удар.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
now_d = now
shag = t.PN_ROLL_BANK_SLEW / t.NOMINAL_FPS + 1e-6
pred = 0.0
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_d += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(4.0, now_d, 0.0, 30.0, now_d, 1.0)
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
# У центра крен не растёт.
t._pn_bank_i = 0.0
t._pn_bank_tek = 0.0
now_n = now
for _ in range(int(4 * t.NOMINAL_FPS)):
    now_n += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.8, now_n, 0.0, 3.0, now_n, 1.0)
assert abs(t._pn_bank_tek) < 1.0, t._pn_bank_tek
assert t._kren_na_kurs(4.0, now, None, 40.0, now, 1.0) == 0.0
assert t.FF_GAIN_ROLL == 0.0
assert t.ROLL_LEAD_S >= 0.4 - 1e-9
assert t.LEAD_FRAMES == 8

print("=== 7. Крен возвращает горизонт, а не держит бок ===")
# 20 ШИМ слежения при банке +15°: уровень тянет назад на 80, не даёт лежать.
uroven = t._kren_k_gorizontu(100.0, 15.0)
assert uroven < 0.0, uroven
assert t._kren_k_gorizontu(0.0, 0.0) == 0.0
assert t._kren_k_gorizontu(100.0, None) == 20.0

print("=== 8. Тангаж держит прямую, не угол захвата ===")
t._pn_nos_i = 0.0
t._pn_nos_tek = None
# Пологая линия, цель в центре: нос надо опустить ниже линии, стик вниз.
pwm_n = t._nos_na_kurs(0.0, now, 18.0, 18.0, 0.0, now, 1.0, 18.0)
assert pwm_n > 0.0, pwm_n
# Уже на запасе «ниже линии», скорость линии ноль: стик отпущен.
dpp = t.CAMERA_VFOV_DEG / float(t.MAIN_H)
lead = t._nizhe_linii(18.0)
dy_lead = -lead / dpp
t._pn_nos_i = 0.0
t._pn_nos_tek = 18.0 + lead
derzh_n = t._nos_na_kurs(0.0, now, 18.0 + lead, 18.0, dy_lead, now, 1.0, 18.0)
assert abs(derzh_n) < 8.0, derzh_n
# Крутая линия, цель в центре: вниз не дожимаем.
t._pn_nos_i = 0.0
t._pn_nos_tek = 32.0
krut = t._nos_na_kurs(0.0, now, 32.0, 18.0, 0.0, now, 1.0, 32.0)
assert abs(krut) < 8.0, krut
# Нос далеко ниже захвата, линия спокойная и цель уже где надо:
# назад к захвату не тянем.
t._pn_nos_i = 0.0
t._pn_nos_tek = 34.0
nazad = t._nos_na_kurs(0.0, now, 34.0, 16.0, dy_lead, now, 1.0, 18.0)
assert nazad > -8.0, nazad
# Линия растёт, но цель уже на запасе: скорость линии нос не дожимает.
# Иначе собственный клевок доезжает до упора.
t._pn_nos_i = 0.0
t._pn_nos_tek = 30.0
rost = t._nos_na_kurs(2.5, now, 30.0, 16.0, dy_lead, now, 1.0, 18.0)
assert abs(rost) < 8.0, rost
# Линия убывает, а цель ещё не выше центра на запас: нос вверх не тянем.
t._pn_nos_i = 0.0
t._pn_nos_tek = 20.0
ne = t._nos_na_kurs(-2.5, now, 20.0, 16.0, 0.0, now, 1.0, 18.0)
assert ne > 0.0, ne
# Цель в центре на обычной линии — нос вниз, даже если скорость линии ноль.
t._pn_nos_i = 0.0
t._pn_nos_tek = 30.0
nizhe = t._nos_na_kurs(0.0, now, 30.0, 16.0, 0.0, now, 1.0, 18.0)
assert nizhe > 0.0, nizhe

print("=== 8b. Газ держит стик входа, путь его не двигает ===")
rovno_g = t._gaz_po_linii(
    1350, 20.0, 8.0, -8.0 * math.tan(math.radians(20.0)) * 100.0)
assert rovno_g == 1350.0, rovno_g
proval = t._gaz_po_linii(1350, 20.0, 6.0, -600.0)
assert proval == 1350.0, proval
duga = t._gaz_po_linii(1350, 25.0, 10.0, -100.0)
assert duga == 1350.0, duga
assert t._gaz_po_linii(1350, 20.0, None, -300.0) == 1350.0
assert t._gaz_po_linii(None, 20.0, 8.0, -100.0) is None
assert t._nizhe_linii(34.0) == 0.0
assert t._nizhe_linii(10.0) == t.PN_AIM_LEAD_MAX
assert t._nizhe_linii(24.0) > 0.0

print("=== 9. Рыскание молчит без крена и цели у края ===")
assert t._ryaskan_na_kurs(0.0, 10.0) == 0.0
assert t._ryaskan_na_kurs(1.0, 20.0) == 0.0
v_kren = t._ryaskan_na_kurs(10.0, 0.0)
assert 0 < v_kren <= t.YAW_BANK_PWM_MAX, v_kren
assert t._ryaskan_na_kurs(-10.0, 0.0) == -v_kren
# Край кадра добавляет немного, не разворот.
kray = t._ryaskan_na_kurs(0.0, 120.0)
assert 0 < kray <= t.YAW_LEASH_MAX, kray

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
t._pn_nos_i = 0.0
t._pn_nos_tek = None


def _kadr(pitch=20.0, roll=0.0, dx=0.0):
    t.target_box_main = (CX - 20 + dx, CY - 20, CX + 20 + dx, CY + 20)
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
        t.app_state["vario_cms"] = 0
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
assert t.global_yaw_cmd > 1510, t.global_yaw_cmd
t.time.monotonic = _vremya
t._estimate_closure = _zamena
print("ok")
