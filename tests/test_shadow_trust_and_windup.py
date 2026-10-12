"""Score-EMA time-normalized shadow (ТЗ §9) и anti-windup shadow (ТЗ
§10) — ПЕРЕДЕЛАНО после 3-го ревью (нашло: shadow anti-windup потерял
собственное насыщение PID по MAX_*_DEFLECT, проверяя только downstream
trust/slew; requested и i в одной строке CSV относились к разным
состояниям интегратора — сдвиг на один control-step относительно live;
score EMA не сбрасывалась в _reset_geometry_history(), хотя live
_dover_score_ema там сбрасывается).

Trust: live ema += TRUST_EMA_ALPHA * (...) БЕЗ alpha_for_dt/k — при
разном FPS это де-факто разные фильтры. shadow_score_ema_time_norm — то
же обновление, но с alpha_for_dt(TRUST_EMA_ALPHA, k). ЭТО НЕ shadow
trust_k (тот ещё берёт худшее из psr/flow_gap) — только EMA-компонент,
где найден FPS-баг.

Anti-windup: requested строится из СВОЕГО _shadow_*_integral, порядок
вычислений теперь точно как в live _pid_axis_step (integral обновляется
ПЕРВЫМ — по старому integral и по внутреннему насыщению MAX_*_DEFLECT
как live, ПЛЮС downstream-restriction предыдущего кадра, — а requested/
after_trust/before_slew/after_slew в ЭТОЙ строке CSV считаются УЖЕ из
нового integral, тем же порядком, что live's out после integral += ...).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()


def kadr(dx=60, dy=40, score=0.85, dt=FRAME_DT):
    with t.state_lock:
        t.target_box_main = (CX - 20 + dx, CY - 20 + dy,
                             CX + 20 + dx, CY + 20 + dy)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = score
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(dt)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. score_ema_time_norm: при k=1 совпадает с live "
      "_dover_score_ema (одна формула при k=1) ===")
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(10):
    sc = kadr(dt=FRAME_DT)
print("    shadow=%.6f live=%.6f" % (sc["score_ema_time_norm"], t._dover_score_ema))
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) < 1e-6

print("\n=== 2. score_ema_time_norm: резкий шаг при неноминальном FPS "
      "расходится с live — демонстрация FPS-бага ===")
force_reset()
_clk.t = 1000.0
for _ in range(10):
    sc = kadr(score=0.85, dt=FRAME_DT)
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) < 1e-6, (
    "прогрев на k=1 обязан дать совпадение")
sc = kadr(score=0.20, dt=FRAME_DT * 2.0)
print("    после шага (0.85->0.20, k~2): shadow=%.6f live=%.6f"
      % (sc["score_ema_time_norm"], t._dover_score_ema))
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) > 1e-3
print("    подтверждено")

print("\n=== 3. НАЙДЕНО 3-м ревью, ИСПРАВЛЕНО: shadow-I БОЛЬШЕ НЕ "
      "копится, когда PID сам упёрся в MAX_ROLL_DEFLECT (downstream "
      "restriction~0 роли не играет) ===")
# Раньше (2-я версия) в этом же сценарии shadow_roll_i дорастал до ~39
# за 30 кадров, хотя shadow_roll_requested уже был наглухо зажат
# потолком — "исправленный" anti-windup вёл бы себя ХУЖЕ live. Теперь
# внутреннее насыщение (как в live _pid_axis_step) блокирует накопление
# точно так же, как блокирует live.
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(30):
    sc = kadr(dx=250, dy=180, score=0.95)   # score=0.95 -> trust~1, restriction~0
c = t._ctl_dbg
print("    после 30 кадров: live roll_off=%.1f live roll_integral=%.1f | "
      "shadow_roll_requested=%.1f shadow_roll_i=%.4f"
      % (c["roll_off"], t.roll_integral, sc["roll_requested"], sc["roll_i"]))
assert abs(sc["roll_requested"]) <= t.MAX_ROLL_DEFLECT + 1e-6, (
    "shadow_roll_requested обязан быть зажат по MAX_ROLL_DEFLECT")
assert abs(sc["roll_i"]) < 5.0, (
    "shadow_roll_i не должен был заметно вырасти — PID сам упёрся в "
    "потолок, внутреннее насыщение обязано это увидеть, как и live "
    "(это регрессия ИМЕННО того парадокса, который нашло 3-е ревью)")
print("    подтверждено: внутреннее насыщение MAX_ROLL_DEFLECT блокирует "
      "shadow-I так же, как live")

print("\n=== 4. НАЙДЕНО 3-м ревью, ИСПРАВЛЕНО: requested и i в ОДНОЙ "
      "строке CSV — из ОДНОГО и того же (нового) состояния интегратора ===")
# Самосогласованность: пересчитать ROLL_SIGN*(P+D+FF+shadow_roll_i),
# зажать по MAX_ROLL_DEFLECT — должно ТОЧНО совпасть с shadow_roll_
# requested из ТОЙ ЖЕ строки (а не с requested, посчитанным по СТАРОМУ i,
# как было раньше — это и была несогласованность, которую нашло ревью).
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(8):
    sc = kadr(dx=45, dy=30, score=0.85)
c = t._ctl_dbg
_pereschitan = max(-t.MAX_ROLL_DEFLECT, min(t.MAX_ROLL_DEFLECT,
    t.ROLL_SIGN * (c["roll_p"] + c["roll_d"] + c["roll_ff"] + sc["roll_i"])))
print("    shadow_roll_requested=%.6f, пересчитано из (P+D+FF+roll_i "
      "ЭТОЙ строки)=%.6f" % (sc["roll_requested"], _pereschitan))
assert abs(sc["roll_requested"] - _pereschitan) < 1e-9, (
    "requested в этой строке CSV обязан получаться из i ЭТОЙ ЖЕ строки "
    "— несогласованность (requested от старого i) была найдена ревью")
print("    подтверждено: requested и i самосогласованы в одной строке")

print("\n=== 5. Restriction по-прежнему разложен: trust_restriction "
      "чистый, slew_restriction теперь честно документирован как "
      "restriction ОБЪЕДИНЁННОЙ команды (не изолированного PID) ===")
force_reset()
_clk.t = 1000.0
for _ in range(15):
    sc = kadr(dx=15, dy=10, score=0.95)
print("    roll_trust_restriction=%.3f roll_slew_restriction=%.3f"
      % (sc["roll_trust_restriction"], sc["roll_slew_restriction"]))
assert abs(sc["roll_trust_restriction"]) < 5.0, (
    "доверие ~1.0 -> trust_restriction обязан быть близок к нулю")

print("\n=== 6. КЛЮЧЕВОЙ ФИКС (2-е ревью, регрессия проверяется и "
      "здесь): push_further зависит от sign оси ===")
ERR_F = 10.0
RESTRICTION = 20.0
I_GAIN, I_MAX, I_DECAY, K = 0.1, 1000.0, 1.0, 1.0
# requested_raw=0.0, max_deflect=1000 (заведомо далеко) -> внутреннее
# насыщение не участвует, проверяем ИЗОЛИРОВАННО downstream-часть.
new_i_plus, restricted_plus, pushed_plus = t._shadow_windup_step(
    ERR_F, 0.0, 1000.0, RESTRICTION, 0.0, I_GAIN, I_MAX, I_DECAY, 1, K)
new_i_minus, restricted_minus, pushed_minus = t._shadow_windup_step(
    ERR_F, 0.0, 1000.0, RESTRICTION, 0.0, I_GAIN, I_MAX, I_DECAY, -1, K)
print("    sign=+1: integral %.4f pushed=%s | sign=-1: integral %.4f "
      "pushed=%s" % (new_i_plus, pushed_plus, new_i_minus, pushed_minus))
assert restricted_plus and restricted_minus
assert abs(new_i_plus - 0.0) < 1e-9, (
    "sign=+1: push_further должен был заблокировать накопление")
assert abs(new_i_minus - 1.0) < 1e-6, (
    "sign=-1: push_further должен был пропустить накопление "
    "(err_f*i_gain*k=1.0)")
assert new_i_plus != new_i_minus, (
    "РЕГРЕССИЯ: смена sign обязана менять классификацию push_further")
print("    sign=+1 и sign=-1 дают разный результат — фикс держится")

print("\n=== 7. НОВОЕ (3-е ревью): внутреннее насыщение MAX_*_DEFLECT "
      "блокирует push_further ДАЖЕ ПРИ НУЛЕВОЙ downstream-restriction "
      "===")
# requested_raw ВЫШЕ max_deflect, той же стороны, что err_f -> внутреннее
# насыщение обязано заблокировать накопление, даже если restriction=0
# (то есть даже если trust/slew ничего не резали в этом кадре вовсе).
new_i_sat, restricted_sat, pushed_sat = t._shadow_windup_step(
    ERR_F, 250.0, 200.0, 0.0, 0.0, I_GAIN, I_MAX, I_DECAY, 1, K)
print("    requested_raw=250 > max_deflect=200, restriction=0: "
      "integral %.4f pushed=%s restricted=%s"
      % (new_i_sat, pushed_sat, restricted_sat))
assert pushed_sat, (
    "внутреннее насыщение (requested_raw > max_deflect, тот же знак, "
    "что err_f) обязано заблокировать push_further=True ДАЖЕ когда "
    "downstream ничего не урезал — это и есть фикс парадокса из п.3")
assert abs(new_i_sat - 0.0) < 1e-9, (
    "интеграл не должен был вырасти — заблокирован внутренним "
    "насыщением, а не downstream")
assert not restricted_sat, (
    "restricted (флаг downstream) обязан остаться False — сам он не "
    "менялся, заблокировало именно внутреннее насыщение, не downstream"
)
print("    подтверждено: внутреннее насыщение работает независимо от "
      "downstream restriction")

print("\n=== 8. НАЙДЕНО 3-м ревью, ИСПРАВЛЕНО: score_ema_time_norm "
      "сбрасывается в _reset_geometry_history() вместе с live "
      "_dover_score_ema ===")
force_reset()
_clk.t = 1000.0
for _ in range(10):
    sc = kadr(score=0.85, dt=FRAME_DT)
assert sc["score_ema_time_norm"] is not None
assert t._dover_score_ema is not None
t._reset_geometry_history("тест: имитация frame gap/re-anchor")
assert t._dover_score_ema is None, (
    "тест сам не годен: live _dover_score_ema должен сбрасываться в "
    "_reset_geometry_history — если нет, изменилось поведение live"
)
assert t._shadow_trust_ema is None, (
    "shadow_trust_ema (баз для score_ema_time_norm) НЕ сбросился вместе "
    "с live _dover_score_ema в _reset_geometry_history — после frame "
    "gap/re-anchor shadow продолжал бы старую эпоху истории, и "
    "сравнение live vs shadow выглядело бы как FPS-эффект, а было бы "
    "просто разной историей")
print("    оба сброшены в None одним вызовом _reset_geometry_history()")

print("\n=== 9. НАЙДЕНО 4-м ревью, ИСПРАВЛЕНО: _shadow_*_prev_restriction "
      "тоже сбрасывается в _reset_geometry_history() — иначе первая "
      "I-реакция после разрыва блокировалась бы restriction'ом из уже "
      "неактуальной эпохи ===")
# Прямое воспроизведение сценария из ревью: до разрыва — крупная
# restriction. После _reset_geometry_history() "предыдущего кадра" в
# обычном смысле больше нет — prev_restriction обязан читаться как
# "неизвестно", не как старое число из прошлой эпохи.
#
# ДОВЕРИЕ УРЕЗАЕТСЯ ТОЛЬКО УХУДШЕНИЕМ (самокалибровка относительно
# собственного недавнего уровня, см. TRUST_ENABLED) — КОНСТАНТНЫЙ низкий
# score сходится сам с собой и даёт trust_k=1.0, restriction=0. Нужен
# переходный процесс: прогрев на хорошем score, затем резкий обвал —
# тот же приём, что уже в проверке 2 для score_ema.
force_reset()
_clk.t = 1000.0
for _ in range(15):
    kadr(dx=100, dy=70, score=0.90)   # прогрев: доверие сходится к 1.0
for _ in range(3):
    sc = kadr(dx=100, dy=70, score=0.05)   # резкий обвал -> trust_k << 1
_do_razryva = sc["roll_trust_restriction"]
print("    до разрыва: roll_trust_restriction=%.1f (_shadow_roll_prev_"
      "restriction=%.1f)" % (_do_razryva, t._shadow_roll_prev_restriction))
assert abs(t._shadow_roll_prev_restriction) > t.SHADOW_WINDUP_RESTRICT_PWM, (
    "тест сам не годен: до разрыва должна была накопиться заметная "
    "restriction, иначе сценарий ничего не проверяет")
t._reset_geometry_history("тест: имитация frame gap/re-anchor")
print("    после _reset_geometry_history(): _shadow_roll_prev_restriction=%.1f"
      % t._shadow_roll_prev_restriction)
assert t._shadow_roll_prev_restriction == 0.0, (
    "_shadow_roll_prev_restriction не сбросился в _reset_geometry_history "
    "— первая I-реакция shadow после разрыва (frame gap/re-anchor) была "
    "бы ошибочно заблокирована restriction'ом из уже неактуальной эпохи "
    "геометрии")
assert t._shadow_pitch_prev_restriction == 0.0
assert t._shadow_yaw_prev_restriction == 0.0
print("    все три _shadow_*_prev_restriction сброшены в 0.0")

print("\n=== 10. А _shadow_slew_* НЕ сбрасывается в "
      "_reset_geometry_history() — сознательно, как и live _slew_* ===")
# geometry_epoch — про temporal derivative-историю (tau/LOS-rate/EMA), не
# про command state; live _slew_roll/_slew_pitch/_slew_yaw тоже
# переживают короткий разрыв без обнуления. Проверяем, что это НЕ
# случайно забытое — код должен явно НЕ трогать _shadow_slew_* здесь.
force_reset()
_clk.t = 1000.0
for _ in range(10):
    kadr(dx=100, dy=60, score=0.85)
_slew_do = (t._shadow_slew_roll, t._shadow_slew_pitch, t._shadow_slew_yaw)
t._reset_geometry_history("тест: короткий разрыв, не потеря лока")
_slew_posle = (t._shadow_slew_roll, t._shadow_slew_pitch, t._shadow_slew_yaw)
assert _slew_do == _slew_posle, (
    "_shadow_slew_* изменился в _reset_geometry_history() — это не "
    "должно происходить: slew-состояние (как и у live) переживает "
    "короткий разрыв геометрии без обнуления, сбрасывается только в "
    "полном not-controllable-сбросе")
print("    _shadow_slew_roll/pitch/yaw не тронуты — как и задумано")

print("\nOK: anti-windup shadow видит и внутреннее (MAX_*_DEFLECT), и "
      "downstream (trust/slew) насыщение; requested/i самосогласованы "
      "внутри одной строки; score EMA и prev_restriction живут с той же "
      "границей эпохи, что live; slew-состояние границу эпохи "
      "сознательно переживает")
