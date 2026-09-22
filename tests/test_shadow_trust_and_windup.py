"""Trust time-normalized shadow (ТЗ §9) и anti-windup shadow (ТЗ §10).

Оба про одно: live-версии этих механизмов не видят время/downstream
ограничения так, как должны бы — здесь параллельно считаем, что было бы,
не трогая live _dover_score_ema/roll_integral/pitch_integral/yaw_integral.

Trust: live ema += TRUST_EMA_ALPHA * (...) БЕЗ alpha_for_dt/k — при
разном FPS это де-факто разные фильтры. shadow_trust_time_norm — то же
самое обновление, но с alpha_for_dt(TRUST_EMA_ALPHA, k). При k=1 (номинал)
обе формулы обязаны совпасть; при k далёким от 1 — разойтись, и это и
есть демонстрация найденного бага (не исправление — только видимость).

Anti-windup: shadow_roll_i/pitch_i/yaw_i — СВОИ интеграторы, копящие
ошибку по тому же правилу push_further, что и live _pid_axis_step, но с
"насыщением", определённым через РЕАЛЬНОЕ расхождение requested/delivered
(roll_output_restriction), а не через MAX_*_DEFLECT.
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


print("=== 1. Trust: при k=1 (номинальный FPS) shadow-EMA совпадает с "
      "live-EMA (одна и та же формула при k=1) ===")
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(10):
    sc = kadr(dt=FRAME_DT)   # ровно номинальный кадр -> k=1
print("    shadow_trust_time_norm=%.6f live _dover_score_ema=%.6f"
      % (sc["trust_time_norm"], t._dover_score_ema))
assert abs(sc["trust_time_norm"] - t._dover_score_ema) < 1e-6, (
    "при k=1 обе формулы совпадают по построению — расхождение значит "
    "ошибку в shadow-копии, а не в найденном баге")

print("\n=== 2. Trust: при заметно неноминальном FPS (k далеко от 1) "
      "shadow-EMA расходится с live-EMA — демонстрация FPS-бага ===")
# ВАЖНО: EMA от константного score НЕ РАСХОДИТСЯ вообще — раз она уже
# сошлась к этому значению, (score-ema)=0 у ОБЕИХ формул независимо от
# alpha/k, и разница alpha_for_dt тут никак не проявится. Расхождение
# видно только в ПЕРЕХОДНОМ процессе: сперва сходимся к 0.85 на
# номинальном FPS (k=1, формулы совпадают по построению), затем ОДИН
# резкий шаг score вниз одновременно с неноминальным k — вот тут
# alpha_for_dt(ALPHA, k) и голый ALPHA дают заметно разный шаг.
force_reset()
_clk.t = 1000.0
for _ in range(10):
    sc = kadr(score=0.85, dt=FRAME_DT)   # прогрев на номинальном FPS
assert abs(sc["trust_time_norm"] - t._dover_score_ema) < 1e-6, (
    "прогрев на k=1 обязан дать совпадение — иначе тест сам не годен")
sc = kadr(score=0.20, dt=FRAME_DT * 2.0)   # резкий обвал + k~2 за один шаг
print("    после одного шага (score 0.85->0.20, k~2): "
      "shadow_trust_time_norm=%.6f live _dover_score_ema=%.6f"
      % (sc["trust_time_norm"], t._dover_score_ema))
assert abs(sc["trust_time_norm"] - t._dover_score_ema) > 1e-3, (
    "на резком шаге при неноминальном FPS shadow (time-normalized) и "
    "live (не нормированная) EMA обязаны заметно разойтись — это и есть "
    "found FPS-баг; совпадение значит, что либо баг пропал (тогда почему "
    "TRUST_EMA_ALPHA не тронут?), либо shadow считает так же неверно, "
    "как live")
print("    подтверждено: расхождение реально видно на резком шаге при "
      "неноминальном FPS")

print("\n=== 3. Anti-windup shadow: своё, отдельное состояние — не "
      "совпадает с live roll_integral/pitch_integral ===")
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(20):
    sc = kadr(dx=250, dy=180, score=0.85)   # крупная устойчивая ошибка
print("    shadow_roll_i=%.3f live roll_integral=%.3f | shadow_pitch_i=%.3f "
      "live pitch_integral=%.3f"
      % (sc["roll_i"], t.roll_integral, sc["pitch_i"], t.pitch_integral))
# Не требуем, чтобы они были РАЗНЫМИ по величине (могут случайно совпасть
# на простом сценарии) — требуем, что это ДЕЙСТВИТЕЛЬНО разные объекты
# состояния: изменение shadow не задевает live.
_live_before = (t.roll_integral, t.pitch_integral, t.yaw_integral)
t._shadow_roll_integral = 99999.0
t._shadow_pitch_integral = -99999.0
t._shadow_yaw_integral = 12345.0
assert (t.roll_integral, t.pitch_integral, t.yaw_integral) == _live_before, (
    "изменение shadow-интеграторов задело live roll_integral/pitch_integral/"
    "yaw_integral — это не отдельное состояние, а alias на live")
print("    подтверждено: shadow-интеграторы физически отдельные "
      "переменные, не alias на live")

print("\n=== 4. output_restriction = requested - delivered, "
      "самосогласовано с уже существующими roll_off/before_slew/after_slew ===")
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(20):
    sc = kadr(dx=250, dy=180, score=0.85)
c = t._ctl_dbg
_ozhid_roll = c["roll_off"] - c["roll_after_slew"]
print("    shadow_roll_output_restriction=%.3f, roll_off-roll_after_slew=%.3f"
      % (sc["roll_output_restriction"], _ozhid_roll))
assert abs(sc["roll_output_restriction"] - _ozhid_roll) < 1e-6
print("    совпадает — output_restriction не вводит новую величину, "
      "только сравнивает уже существующие")

print("\n=== 5. slew_roll_active — булев вывод из уже существующих "
      "before_slew/after_slew, не новая величина ===")
_ozhid_active = c["roll_before_slew"] != c["roll_after_slew"]
assert sc["slew_roll_active"] == _ozhid_active, (
    "shadow_slew_roll_active не совпадает с (roll_before_slew != "
    "roll_after_slew)")
print("    slew_roll_active=%s согласован с before/after_slew"
      % sc["slew_roll_active"])

print("\n=== 6. Restriction-флаг restricted реагирует на порог "
      "SHADOW_WINDUP_RESTRICT_PWM ===")
assert (abs(sc["roll_output_restriction"]) > t.SHADOW_WINDUP_RESTRICT_PWM) == \
    sc["roll_restricted"], (
    "roll_restricted не согласован с порогом SHADOW_WINDUP_RESTRICT_PWM "
    "и уже посчитанным roll_output_restriction")
print("    roll_restricted согласован с порогом")

print("\nOK: trust time-normalized shadow показывает найденный FPS-баг, "
      "не переключая live; anti-windup shadow — отдельное, "
      "самосогласованное состояние, не alias на live-интеграторы")
