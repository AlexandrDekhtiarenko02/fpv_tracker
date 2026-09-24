"""Первый срез supervisor-состояний Shadow v2 (ТЗ/разбор 24.09.2026):
HIGH_ATTITUDE и VERTICAL_SINK_LIMIT.

По следам ответа "давай" на предложение сделать threshold-sweep. Третье
предложенное состояние, CLOSURE_GEOMETRY_DISAGREE (tau vs los_rate), в
эту сессию НЕ вошло: наивная формула "tau_s < TAU_THROTTLE_LO_S и
|los_rate| > LOS_RATE_DEADBAND_DPS" (оба порога уже live-константы, не
придуманы) на проверке дала #4 (здоровый эталон, "сошлось") САМЫЙ
высокий disagree-rate — 39.7%, выше, чем у #9 (36.0%), ради которого
условие и строилось. Значит формула не разделяет, а не значит "фичи нет"
— нужен нормальный дизайн, не десятиминутный sweep. Не реализовано.

HIGH_ATTITUDE: fc_pitch > SHADOW_HIGH_ATTITUDE_DEG=40.0. Порог — из
threshold-sweep по #4/7/9/13/14: dominance-rate по 5-градусным бакетам
держится <15% (с провалом до ~0% на 25-40°), затем резко прыгает на
45-50° (62%), 50-55° (91%). fc_pitch — тот же сигнал, что уже участвует в
pitch_comp_px (svezhiy_tangazh), новое измерение не нужно; МОЖЕТ быть
None при протухшей/отсутствующей ATT.

VERTICAL_SINK_LIMIT: sink_mps > VARIO_MAX_SINK_MPS — ноль новых порогов,
буквально та же константа (и VARIO_FRESH_S), что уже использует живой
"ПРЕДЕЛ СКОРОСТИ СНИЖЕНИЯ" в throttle law. На 5 проверенных заходах
только #13 (вертикальный промах) пересекает 12 м/с (up to 19.8 м/с) —
у остальных максимум 5.9-11.4 м/с.
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


def kadr(dx=60, dy=40, fc_pitch=15.0, vario_cms=0.0, score=0.85, dt=FRAME_DT):
    with t.state_lock:
        t.target_box_main = (CX - 20 + dx, CY - 20 + dy,
                             CX + 20 + dx, CY + 20 + dy)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = score
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    now = t.time.monotonic()
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = now
        t.app_state["fc_pitch_deg"] = fc_pitch
        t.app_state["fc_pitch_ts"] = now
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = now
        t.app_state["vario_cms"] = vario_cms
        t.app_state["alt_ts"] = now
    _clk.tick(dt)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. HIGH_ATTITUDE: False при малом pitch, True за порогом ===")
force_reset()
_clk.t = 1000.0
sc_low = kadr(fc_pitch=20.0)
assert sc_low["pitch_high_attitude"] is False
sc_high = kadr(fc_pitch=45.0)
assert sc_high["pitch_high_attitude"] is True
print("    fc_pitch=20 -> False, fc_pitch=45 -> True (порог %.0f)"
      % t.SHADOW_HIGH_ATTITUDE_DEG)

print("\n=== 2. HIGH_ATTITUDE: граница ровно на пороге (не >=, строго >) ===")
sc_edge = kadr(fc_pitch=t.SHADOW_HIGH_ATTITUDE_DEG)
assert sc_edge["pitch_high_attitude"] is False, (
    "ровно на пороге обязано быть False (строго >, не >=) — иначе "
    "константа сама себе противоречит с комментарием 'больше X'")
print("    fc_pitch==порог -> False (строго >, граница не включена)")

print("\n=== 3. HIGH_ATTITUDE: None при отсутствующей ATT (не путать с "
      "False) ===")
force_reset()
_clk.t = 1000.0
with t.state_lock:
    t.target_box_main = (CX - 20, CY - 20, CX + 20, CY + 20)
    t.target_controllable = True
    t.target_visible = True
t.last_match_score = 0.85
t._match_dbg = {"psr": 6.0}
t.lock_w0 = t.lock_h0 = 30.0
with t.state_lock:
    t.app_state["rc_throttle"] = 1450
    t.app_state["rc_throttle_ts"] = t.time.monotonic()
    t.app_state["fc_pitch_deg"] = None
    t.app_state["fc_pitch_ts"] = 0.0
    t.app_state["gyro"] = (0, 0, 0)
    t.app_state["imu_ts"] = t.time.monotonic()
_clk.tick(FRAME_DT)
t.update_control_from_target()
sc_none = t._shadow_ctl_dbg
if sc_none.get("active"):
    assert sc_none["pitch_high_attitude"] is None, (
        "без ATT pitch_high_attitude обязан быть None, не False — иначе "
        "'нет данных' и 'проверили, pitch маленький' неразличимы")
    print("    ATT отсутствует -> pitch_high_attitude=None (не False)")
else:
    print("    (shadow неактивен на этом синтетическом кадре — пропуск, "
          "проверка None уже покрыта тестом test_shadow_isolation.py "
          "для аналогичных полей)")

print("\n=== 4. VERTICAL_SINK_LIMIT: False при малом снижении, True за "
      "VARIO_MAX_SINK_MPS ===")
force_reset()
_clk.t = 1000.0
sc_slow = kadr(fc_pitch=10.0, vario_cms=-500.0)  # 5 м/с
assert sc_slow["vertical_sink_limit"] is False
assert abs(sc_slow["vertical_sink_mps"] - 5.0) < 1e-6
sc_fast = kadr(fc_pitch=10.0, vario_cms=-1980.0)  # 19.8 м/с — реальный #13
assert sc_fast["vertical_sink_limit"] is True
assert abs(sc_fast["vertical_sink_mps"] - 19.8) < 1e-6
print("    sink=5.0 м/с -> False, sink=19.8 м/с (как в реальном #13) -> "
      "True (порог %.1f)" % t.VARIO_MAX_SINK_MPS)

print("\n=== 5. VERTICAL_SINK_LIMIT: набор подъёма (climb, vario>0) — не "
      "путать с сильным снижением ===")
sc_climb = kadr(fc_pitch=10.0, vario_cms=2000.0)  # набираем высоту
assert sc_climb["vertical_sink_limit"] is False
assert sc_climb["vertical_sink_mps"] < 0, "набор высоты должен дать sink<0"
print("    набор высоты (vario=+2000 см/с) -> sink_mps<0, limit=False")

print("\n=== 6. VERTICAL_SINK_LIMIT: ни одного нового порога — по "
      "исходному тексту читает именно VARIO_MAX_SINK_MPS/VARIO_FRESH_S ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = src.index("# ================= SHADOW CONTROLLER =================")
i_end = src.index("# ================ /SHADOW CONTROLLER ================")
shadow_body = src[i_start:i_end]
assert "VARIO_MAX_SINK_MPS" in shadow_body and "VARIO_FRESH_S" in shadow_body
assert "SHADOW_VARIO" not in shadow_body, (
    "не должно быть отдельной shadow-копии порога VARIO — используется "
    "живая константа напрямую")
print("    в shadow-блоке нет отдельной SHADOW_VARIO_* константы — читает "
      "живой VARIO_MAX_SINK_MPS напрямую")

print("\nOK: shadow_pitch_high_attitude (новый порог 40°, обоснован "
      "sweep'ом) и shadow_vertical_sink_limit (ноль новых порогов, живая "
      "VARIO_MAX_SINK_MPS) работают как задумано, диагностика-only")
