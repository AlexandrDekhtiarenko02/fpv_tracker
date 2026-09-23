"""Final-hold delivered-MODEL gap — найдено разбором 14 заходов 22.09.2026
(не по коду), терминология поправлена ревью по d2e63f4.

ТРИ РАЗНЫХ УРОВНЯ — этот тест держит их разделёнными, чтобы через неделю
никто не сравнил не то с не тем:
  1. live computed (до hold)  = pitch_after_slew (существующая live-колонка,
     без префикса shadow_)
  2. live actually commanded  = cmd_pitch/sent_p (существующая live-колонка
     — единственный источник правды про то, что реально ушло на FC)
  3. shadow hypothetical      = shadow_pitch_after_slew (уже был) и
     shadow_pitch_delivered_model (этот тест)

В реальном zahvat07 (flight_20260922_165053) РАЗРЫВ УРОВНЕЙ 1 И 2 —
pitch_after_slew ушёл 15->85 PWM за время hold, пока cmd_pitch реально
стоял замороженным на 1514 (70 PWM) — это была исходная находка, целиком
из существующих live-колонок, БЕЗ всякого shadow.

shadow_*_delivered_model — НЕ уровень 2, не попытка угадать реально
доставленную live-команду. Это СВОЯ shadow-заморозка (не копия live
_final_komandy, та усредняет по своему окну _final_okno, у shadow такого
окна нет): держит СВОЙ after_slew на момент False->True перехода. Вне hold
delivered_model==after_slew, hold_restriction=0. Диагностика-only: этот
gap пока НИКАК не входит в _shadow_windup_step/push_further — поведение
shadow (не говоря о live) не меняется, только видимость.
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


print("=== 1. Hold неактивен: delivered_model==after_slew, hold_restriction=0 ===")
force_reset()
_clk.t = 1000.0
for _ in range(5):
    sc = kadr(dx=60, dy=40)
assert sc["pitch_delivered_model"] == sc["pitch_after_slew"]
assert sc["pitch_hold_restriction"] == 0.0
assert sc["roll_delivered_model"] == sc["roll_after_slew"]
assert sc["yaw_delivered_model"] == sc["yaw_after_slew"]
print("    pitch_delivered_model=%.2f == pitch_after_slew=%.2f, restriction=0"
      % (sc["pitch_delivered_model"], sc["pitch_after_slew"]))

print("\n=== 2. Момент включения hold: delivered_model захватывает after_slew "
      "ЭТОГО же кадра ===")
t._final_zamorozhen = True
t._final_komandy = (1500, 1500, 1500)  # live-заморозка — своя, shadow её не копирует
sc_engage = kadr(dx=60, dy=110)  # резкий скачок dy - after_slew сдвинется
assert sc_engage["pitch_delivered_model"] == sc_engage["pitch_after_slew"], (
    "на кадре включения hold delivered_model обязан совпасть со СВОИМ же "
    "after_slew этого кадра (захват происходит сейчас, не раньше)")
assert sc_engage["pitch_hold_restriction"] == 0.0
_frozen_at = sc_engage["pitch_delivered_model"]
print("    delivered_model захвачен на %.2f (== after_slew этого кадра)" % _frozen_at)

print("\n=== 3. Hold продолжается: delivered_model держит ЗАМОРОЖЕННОЕ значение, "
      "after_slew продолжает жить своей жизнью, gap растёт ===")
seen_nonzero_gap = False
for i in range(6):
    sc_hold = kadr(dx=60, dy=110 + 15 * (i + 1))  # dy продолжает расти
    assert sc_hold["pitch_delivered_model"] == _frozen_at, (
        "delivered_model обязан оставаться замороженным, пока _final_zamorozhen=True "
        "(кадр %d: delivered_model=%.2f, ожидали %.2f)"
        % (i, sc_hold["pitch_delivered_model"], _frozen_at))
    expected_gap = sc_hold["pitch_after_slew"] - sc_hold["pitch_delivered_model"]
    assert abs(sc_hold["pitch_hold_restriction"] - expected_gap) < 1e-9
    if abs(sc_hold["pitch_hold_restriction"]) > 1e-6:
        seen_nonzero_gap = True
assert seen_nonzero_gap, (
    "растущий dy должен был сдвинуть after_slew достаточно, чтобы gap стал "
    "заметно ненулевым — как в реальном zahvat07 (15->85 PWM за время hold)")
print("    delivered_model неподвижен на %.2f все %d кадров, after_slew ушёл до "
      "%.2f, hold_restriction=%.2f (реальный аналог: 70 PWM в zahvat07)"
      % (_frozen_at, 6, sc_hold["pitch_after_slew"], sc_hold["pitch_hold_restriction"]))

print("\n=== 4. Hold отпущен: delivered_model снова тут же следует за after_slew ===")
t._final_zamorozhen = False
sc_release = kadr(dx=60, dy=40)
assert sc_release["pitch_delivered_model"] == sc_release["pitch_after_slew"]
assert sc_release["pitch_hold_restriction"] == 0.0
print("    delivered_model=%.2f == after_slew=%.2f сразу после отпускания"
      % (sc_release["pitch_delivered_model"], sc_release["pitch_after_slew"]))

print("\n=== 5. force_reset() чистит shadow-заморозку — новый заход не "
      "наследует чужую frozen-точку ===")
t._final_zamorozhen = True
t._final_komandy = (1500, 1600, 1500)
kadr(dx=60, dy=200)
assert t._shadow_pitch_hold_value is not None, "hold должен был захватиться"
force_reset()
assert t._shadow_pitch_hold_value is None, (
    "force_reset (полный сброс не-controllable) обязан сбросить "
    "_shadow_*_hold_value — иначе новый заход стартует с чужой заморозкой")
_clk.t = 1000.0
sc_new = kadr(dx=60, dy=40)
assert sc_new["pitch_delivered_model"] == sc_new["pitch_after_slew"], (
    "после force_reset новый заход должен сразу трекать after_slew, "
    "не тащить frozen-точку прошлого захода")
print("    _shadow_pitch_hold_value сброшен в None, новый заход стартует чисто")

print("\n=== 6. Диагностика-only: gap НЕ участвует в push_further/_shadow_windup_step "
      "— по исходному тексту final_hold-блок стоит ПОСЛЕ anti-windup ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_windup = src.index("_shadow_windup_step(\n            adx,")
i_hold = src.index("Final-hold delivered-MODEL gap: нашли по 14")
assert i_hold > i_windup, (
    "final-hold блок обязан идти ПОСЛЕ вызовов _shadow_windup_step — "
    "иначе gap рисковал бы попасть во push_further этого же кадра, "
    "а ТЗ явно требует диагностику-only на этом шаге")
print("    final-hold блок физически после anti-windup — gap не мог "
      "повлиять на push_further в этом кадре")

print("\nOK: shadow видит final-hold delivered-MODEL gap (найденный по 14 "
      "реальным заходам как разрыв УРОВНЕЙ 1/2 — pitch_after_slew vs "
      "cmd_pitch, без всякого shadow), держит СВОЮ, отдельно поименованную "
      "гипотетическую заморозку независимо от live _final_komandy, "
      "корректно сбрасывается и не участвует в anti-windup — чистая "
      "диагностика, а не попытка угадать реально доставленную команду")
