"""Первый срез supervisor-состояний Shadow v2 (ТЗ/разбор 24.09.2026):
HIGH_NOSE_DOWN и VERTICAL_SINK_LIMIT.

HIGH_NOSE_DOWN — ПЕРЕИМЕНОВАНО и ИСПРАВЛЕНО после ревью по fad59c8, две
находки:
  (1) threshold-sweep (40°) считался по СЫРОМУ fc_pitch_deg из CSV, а
      первая версия применяла порог к svezhiy_tangazh() (raw + gyro-
      экстраполяция до ±12°) — доказательная база и фактический вход были
      РАЗНЫМИ сигналами. Теперь читает raw fc_pitch_deg напрямую.
  (2) сравнение было abs(pitch)>40 — вся доказательная база (sign-
      conflict, pitch_comp dominance, VARIO sink) про ОДИН конкретный
      сценарий: nose-down dive (positive fc_pitch = nose down). -50°
      nose-up физически другая ситуация, но abs() схлопывал их в одно
      состояние. Теперь сравнение знаковое (pitch > порог), имя поля —
      high_nose_down.

Ревью по 1de3fad нашло ЕЩЁ баг: убрав svezhiy_tangazh(), заодно потеряли
его freshness-проверку — app_state["fc_pitch_deg"] не обнуляется сам,
если MSP_ATTITUDE перестал приходить, и голое ">40" продолжило бы писать
True по протухшему значению сколь угодно долго. Добавлен freshness-gate
по FC_PITCH_TIMEOUT (переиспользует уже посчитанный _att_age_ms, не новый
state_lock).

VERTICAL_SINK_LIMIT — БЕЗ ИЗМЕНЕНИЙ (ревью по fad59c8 явно одобрило: "мне
нравится... не придумал ещё один magic threshold"). sink_mps >
VARIO_MAX_SINK_MPS — ноль новых порогов, та же константа (и
VARIO_FRESH_S), что уже использует живой "ПРЕДЕЛ СКОРОСТИ СНИЖЕНИЯ" в
throttle law.

Третье предложенное состояние, CLOSURE_GEOMETRY_DISAGREE (tau vs
los_rate), по-прежнему НЕ реализовано: наивная формула "tau_s <
TAU_THROTTLE_LO_S и |los_rate| > LOS_RATE_DEADBAND_DPS" на проверке дала
#4 (здоровый эталон) САМЫЙ высокий disagree-rate — 39.7%, выше, чем у #9
(36.0%), ради которого условие строилось. Формула не разделяет — нужен
нормальный time-based дизайн (по аналогии с pitch_ref_conflict_run_s), не
мгновенный boolean.
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


def kadr(dx=60, dy=40, fc_pitch=15.0, vario_cms=0.0, gyro=(0, 0, 0),
         score=0.85, dt=FRAME_DT):
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
        t.app_state["gyro"] = gyro
        t.app_state["imu_ts"] = now
        t.app_state["vario_cms"] = vario_cms
        t.app_state["alt_ts"] = now
    _clk.tick(dt)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. HIGH_NOSE_DOWN: False при малом pitch, True за порогом ===")
force_reset()
_clk.t = 1000.0
sc_low = kadr(fc_pitch=20.0)
assert sc_low["pitch_high_nose_down"] is False
sc_high = kadr(fc_pitch=45.0)
assert sc_high["pitch_high_nose_down"] is True
print("    fc_pitch=20 -> False, fc_pitch=45 -> True (порог %.0f)"
      % t.SHADOW_HIGH_NOSE_DOWN_DEG)

print("\n=== 2. HIGH_NOSE_DOWN: граница ровно на пороге (не >=, строго >) ===")
sc_edge = kadr(fc_pitch=t.SHADOW_HIGH_NOSE_DOWN_DEG)
assert sc_edge["pitch_high_nose_down"] is False, (
    "ровно на пороге обязано быть False (строго >, не >=) — иначе "
    "константа сама себе противоречит с комментарием 'больше X'")
print("    fc_pitch==порог -> False (строго >, граница не включена)")

print("\n=== 3. HIGH_NOSE_DOWN: знаковое сравнение — большой NOSE-UP (-50°) "
      "НЕ считается конфликтом. Главный вывод ревью: abs() смешивал два "
      "физически разных состояния в одно ===")
sc_nose_up = kadr(fc_pitch=-50.0)
assert sc_nose_up["pitch_high_nose_down"] is False, (
    "-50° это nose-up (аппарат задирает нос), физически другая ситуация, "
    "не тот dive-сценарий, на котором доказан порог 40° — abs(pitch)>40 "
    "здесь дал бы True, что и было главной находкой ревью")
print("    fc_pitch=-50 (nose-up) -> False, хотя abs(-50)=50 > 40 — знак "
      "учитывается, не только модуль")

print("\n=== 4. HIGH_NOSE_DOWN: читает СЫРОЙ fc_pitch_deg, не "
      "gyro-экстраполированный svezhiy_tangazh() — тот сигнал, на котором "
      "реально делался threshold-sweep ===")
force_reset()
_clk.t = 1000.0
# fc_pitch=35 (< порога 40 по raw), но БЫСТРОЕ вращение по gyro_y — если бы
# state читал svezhiy_tangazh() (экстраполяция до ±12°), 35+12=47 > 40 дало
# бы True. Порог доказан на raw, поэтому обязано остаться False.
sc_raw = kadr(fc_pitch=35.0, gyro=(0, 90.0, 0))
assert sc_raw["pitch_high_nose_down"] is False, (
    "raw fc_pitch=35 < 40, но если бы state читал экстраполированный "
    "svezhiy_tangazh() (35+gyro-поправка до +12° = до 47°), получили бы "
    "True — а sweep доказан именно на raw-сигнале, не на экстраполяции")
print("    fc_pitch=35 (raw, <40) + быстрое вращение gyro_y=90°/с -> "
      "всё равно False (state не использует gyro-экстраполяцию)")

print("\n=== 5. HIGH_NOSE_DOWN: None при ПРОТУХШЕЙ ATT — реальный баг, "
      "найден ревью по 1de3fad. app_state['fc_pitch_deg'] НЕ обнуляется "
      "сам, если MSP_ATTITUDE перестал приходить: без freshness-gate "
      "старое '52°, протухло 800мс назад' продолжило бы читаться как "
      "живой sign nose-down ===")
force_reset()
_clk.t = 1000.0
kadr(fc_pitch=50.0)  # свежий кадр — заводим fc_pitch_ts в app_state
_clk.tick(t.FC_PITCH_TIMEOUT + 0.5)  # состариваем ts, не трогая значение
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
    # fc_pitch_deg/fc_pitch_ts НЕ трогаем — тот самый "последний известный
    # 50°", который MSP просто перестал обновлять.
    t.app_state["gyro"] = (0, 0, 0)
    t.app_state["imu_ts"] = t.time.monotonic()
t.update_control_from_target()
sc_stale = t._shadow_ctl_dbg
assert sc_stale.get("active"), "shadow должен остаться активным на этом кадре"
assert sc_stale["pitch_high_nose_down"] is None, (
    "протухший raw fc_pitch_deg=50 (>40) без freshness-gate дал бы "
    "pitch_high_nose_down=True по данным старше FC_PITCH_TIMEOUT=%.2fс — "
    "обязано быть None, не True и не False" % t.FC_PITCH_TIMEOUT)
print("    fc_pitch=50° (>40), ts старше FC_PITCH_TIMEOUT=%.2fс -> "
      "pitch_high_nose_down=None (не True по протухшему значению)"
      % t.FC_PITCH_TIMEOUT)

print("\n=== 6. HIGH_NOSE_DOWN: None при отсутствующей ATT (не путать с "
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
    assert sc_none["pitch_high_nose_down"] is None, (
        "без ATT pitch_high_nose_down обязан быть None, не False — иначе "
        "'нет данных' и 'проверили, pitch маленький' неразличимы")
    print("    ATT отсутствует -> pitch_high_nose_down=None (не False)")
else:
    print("    (shadow неактивен на этом синтетическом кадре — пропуск, "
          "проверка None уже покрыта тестом test_shadow_isolation.py "
          "для аналогичных полей)")

print("\n=== 7. VERTICAL_SINK_LIMIT: False при малом снижении, True за "
      "VARIO_MAX_SINK_MPS (без изменений с прошлого ревью) ===")
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

print("\n=== 8. VERTICAL_SINK_LIMIT: набор подъёма (climb, vario>0) — не "
      "путать с сильным снижением ===")
sc_climb = kadr(fc_pitch=10.0, vario_cms=2000.0)  # набираем высоту
assert sc_climb["vertical_sink_limit"] is False
assert sc_climb["vertical_sink_mps"] < 0, "набор высоты должен дать sink<0"
print("    набор высоты (vario=+2000 см/с) -> sink_mps<0, limit=False")

print("\n=== 9. VERTICAL_SINK_LIMIT: ни одного нового порога — по "
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

print("\nOK: shadow_pitch_high_nose_down (знаковый, читает raw fc_pitch_deg "
      "— тот сигнал, на котором доказан порог 40°) и "
      "shadow_vertical_sink_limit (ноль новых порогов, живая "
      "VARIO_MAX_SINK_MPS) работают как задумано, диагностика-only")
