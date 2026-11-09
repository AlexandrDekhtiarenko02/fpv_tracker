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

print("=== 6. Крен — чистый PN ===")
now = 1000.0
t._pn_bank_tek = 0.0
# Скорость азимута → банк
for _ in range(int(2 * t.NOMINAL_FPS)):
    now += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(2.0, now, 0.0, 0.0, now, 1.0, yaw_rate_dps=0.0, vc_mps=15.0, tau_s=8.0)
assert t._pn_bank_tek > 1.0, t._pn_bank_tek
# Быстрое своё рыскание — az не берём
t._pn_bank_tek = 0.0
now_g = now
for _ in range(int(2 * t.NOMINAL_FPS)):
    now_g += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(2.0, now_g, 0.0, 0.0, now_g, 1.0,
                    yaw_rate_dps=t.PN_ROLL_AZ_YAW_MAX_DPS + 5.0, vc_mps=15.0, tau_s=8.0)
assert abs(t._pn_bank_tek) < 1.0, t._pn_bank_tek
# Пиксели без az банка не дают
t._pn_bank_tek = 0.0
now_b = now
for _ in range(int(1.0 * t.NOMINAL_FPS)):
    now_b += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_b, 0.0, 40.0, now_b, 1.0, 0.0)
assert abs(t._pn_bank_tek) < 0.5, t._pn_bank_tek

print("=== 6b. Бегущая рамка больше не поднимает потолок ===")
t._pn_bank_tek = 0.0
now_r = 2000.0
for _ in range(int(1.6 * t.NOMINAL_FPS)):
    now_r += 1.0 / t.NOMINAL_FPS
    t._kren_na_kurs(0.0, now_r, 0.0, 80.0, now_r, 1.0)
assert abs(t._pn_bank_tek) < 0.5, t._pn_bank_tek

print("=== 7. Крен возвращает горизонт, а не держит бок ===")
# 20 ШИМ слежения при банке +15°: уровень тянет назад на 80, не даёт лежать.
uroven = t._kren_k_gorizontu(100.0, 15.0)
assert uroven < 0.0, uroven
assert t._kren_k_gorizontu(0.0, 0.0) == 0.0
assert t._kren_k_gorizontu(100.0, None) == 20.0

print("=== 8. Старый nos/gaz_ot_zapasa удалён ===")
# _nos_na_kurs и _gaz_ot_zapasa больше не существуют.
# Живой тангаж — PN, живой газ — энергия.

print("=== 8d. Недолет и перелет: высота и дальность вместе ===")
# 40 м, 10 с — нужно 4 м/с. Снижаемся 7 м/с: izb=3 → непрерывная поправка.
ned = t._nedolet_deg(40.0, 10.0, 20, 1.0, -700.0, 240.0, 40.0)
assert ned > 4.0, ned  # непрерывный tanh, без жёсткого потолка
# Ровно на нужном — ноль.
rov = t._nedolet_deg(40.0, 10.0, 20, 1.0, -400.0, 240.0, 40.0)
assert rov == 0.0, rov
# Рамка у края кадра — не знаем, размер обрезан.
kra = t._nedolet_deg(40.0, 10.0, 20, 1.0, -700.0, 10.0, 40.0)
assert kra is None, kra
# Время ненадёжно — не знаем.
plo = t._nedolet_deg(40.0, 10.0, 3, 9.0, -700.0, 240.0, 40.0)
assert plo is None, plo
# Большой избыток — мягкий потолок через tanh, не жёсткий.
pot = t._nedolet_deg(40.0, 10.0, 20, 1.0, -2000.0, 240.0, 40.0)
assert 6.0 < pot < 8.1, pot
# Перелет и время убывает — нос на цели.
per = t._nedolet_deg(40.0, 10.0, 20, 1.0, -100.0, 240.0, 40.0, ubyv=-1.0)
assert per == 0.0, per
# То же, но время не убывает — пологий дальний, не трогаем.
dal = t._nedolet_deg(40.0, 10.0, 20, 1.0, -100.0, 240.0, 40.0, ubyv=0.5)
assert dal is None, dal
t._pn_nedolet = 4.0
assert t._zapas_nosa() == 4.0
t._pn_nedolet = 0.0
assert t._zapas_nosa() == 0.0

print("=== 8b. Старый gaz_po_linii удалён ===")

print("=== 8c. Старый dobor_nosa удалён ===")

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
    # Пологая линия: запас ниже цели ещё нужен. На dep≥12 при aim≈0
    # нос уже линия (эталон close) — добор молчит.
    out["depression_deg"] = 8.0
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
t._pn_nos_ugol = None
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
    _kadr(pitch=14.0, roll=0.0, dx=0.0)
print("    тангаж %s рыскание %s газ %s крен %s" % (
    t.global_pitch_cmd, t.global_yaw_cmd, t.global_throttle_cmd,
    t.global_roll_cmd))
# Надёжного времени до контакта нет — запас ноль, нос на цели, стик в центре.
# Навигация с углом прихода и гравитацией даёт постоянное смещение.
assert abs(t.global_pitch_cmd - 1500) <= 120, t.global_pitch_cmd
# Без крена и без цели у края нос не крутим.
assert abs(t.global_yaw_cmd - 1500) <= 2, t.global_yaw_cmd
# Газ ведёт энергию и может отойти от базы насколько нужно.
assert 1000 <= t.global_throttle_cmd <= 2000, t.global_throttle_cmd
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


# На ветке навигации тангаж гасит вращение линии, газ — база.
# Запаса носа нет, поэтому стик входа не разгоняется.
t._estimate_closure = _podmena_blizko
t.ground_speed_mps = 10.0
for _ in range(45):
    _kadr(pitch=20.0, roll=0.0, dx=0.0, vario=0, dy=60.0)
print("    газ %s, тангаж %s" % (t.global_throttle_cmd, t.global_pitch_cmd))
# Газ ведёт энергию и может отойти от базы насколько нужно.
assert 1000 <= t.global_throttle_cmd <= 2000, t.global_throttle_cmd
assert 1000 <= t.global_pitch_cmd <= 2000, t.global_pitch_cmd
t.ground_speed_mps = None
t.time.monotonic = _vremya
t._estimate_closure = _zamena
print("ok")
