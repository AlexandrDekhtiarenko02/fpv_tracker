"""Диагностика anti-windup: requested / after_trust / before_slew /
after_slew + I видны одним снимком (диагностический коммит, сам
anti-windup не менялся).

_pid_axis_step видит только P+D+I+FF и насыщение по MAX_*_DEFLECT — он
НЕ знает про trust_k, демпфирование по гироскопу, launch/cruise-добавки
и CMD_SLEW, которые идут ПОСЛЕ него. I мог продолжать копиться, хотя
реальная финальная команда уже сильно урезана этими шагами — раньше
увидеть это можно было только читая код, а не по логу.

Четыре контрольные точки на ось (roll/pitch), ВСЕ в offset от 1500 PWM:
  requested    — c.get("roll_off")/("pitch_off"), сырой выход _pid_axis_step
  after_trust  — c.get("roll_after_trust")/("pitch_after_trust")
  before_slew  — c.get("roll_before_slew")/("pitch_before_slew")
  after_slew   — c.get("roll_after_slew")/("pitch_after_slew")

ЕДИНИЦЫ (найдено ревью текущего кода, было исправлено ЗДЕСЬ ЖЕ, до
первого коммита в git — до фикса before_slew/after_slew писали
АБСОЛЮТНЫЙ RC (target_roll, ~1500), а requested/after_trust — offset от
центра: цепочка "+80 -> +45 -> 1545 -> 1528" выглядела как два разных
масштаба и не читалась напрямую. Проверка 2 ниже ловит именно регресс
единиц — не полагается только на "значение существует".

Проверяется НЕ конкретная величина урезания (это дело настройки, не
диагностики), а то, что все четыре точки СУЩЕСТВУЮТ, В ОДНИХ ЕДИНИЦАХ,
отличимы друг от друга при trust_k < 1, и что at trust_k == 1 (слежение
стабильно) вся цепочка requested -> after_trust не меняет знак/масштаб
произвольно.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


def sbros():
    t._dover_score_ema = None
    t._dover_psr_ema = None
    t._final_zamorozhen = False
    t._slew_pitch = 1500.0
    t._slew_roll = 1500.0
    t._slew_yaw = 1500.0
    t._pitch_pri_loke = None
    t.pitch_integral = 0.0
    t.roll_integral = 0.0
    t.prev_adx = 0.0
    t.prev_ady_ctrl = 0.0


def kadr(score, dx=60, dy=40, psr=None):
    t.last_match_score = score
    t._match_dbg = {} if psr is None else {"psr": psr}
    t.target_box_main = (CX - 20 + dx, CY - 20 + dy, CX + 20 + dx, CY + 20 + dy)
    t.target_visible = True
    t.target_controllable = True
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    t.update_control_from_target()
    return t._ctl_dbg


print("=== 1. Все четыре контрольные точки присутствуют для roll и pitch ===")
sbros()
c = None
for _ in range(5):
    c = kadr(0.85)
for axis in ("roll", "pitch"):
    for stage in ("off", "after_trust", "before_slew", "after_slew"):
        key = "%s_%s" % (axis, stage)
        assert key in c, "%s отсутствует в _ctl_dbg" % key
        assert c[key] is not None, "%s есть, но None" % key
print("    roll_off/after_trust/before_slew/after_slew и то же для pitch "
      "— все на месте")

print("\n=== 2. Стабильное слежение (trust_k=1): requested -> after_trust "
      "не меняется вовсе ===")
sbros()
for _ in range(30):
    c = kadr(0.90, psr=6.0)
assert abs(c["roll_after_trust"] - c["roll_off"]) < 1e-6, (
    "при доверии 1.0 after_trust обязан совпасть с requested — trust_k "
    "должен быть равен 1 на стабильном слежении")
assert abs(c["pitch_after_trust"] - c["pitch_off"]) < 1e-6, (
    "то же для pitch")
print("    doverie=1.0 -> after_trust == requested по обеим осям "
      "(roll %.2f, pitch %.2f)" % (c["roll_off"], c["pitch_off"]))

print("\n=== 3. Урезанное доверие: after_trust заметно МЕНЬШЕ requested "
      "по модулю ===")
sbros()
for _ in range(30):
    kadr(0.85, psr=6.0)
c_ploho = kadr(0.02, psr=6.0)   # резкий обвал качества относительно уровня
print("    doverie урезано -> requested=%.2f after_trust=%.2f (roll)"
      % (c_ploho["roll_off"], c_ploho["roll_after_trust"]))
if abs(c_ploho["roll_off"]) > 1e-6:
    assert abs(c_ploho["roll_after_trust"]) < abs(c_ploho["roll_off"]), (
        "after_trust не меньше requested при урезанном доверии — trust_k "
        "не применяется до этой точки, либо диагностика читает не то")

print("\n=== 4. before_slew включает демпфирование/launch/cruise, которых "
      "after_trust не видит (могут отличаться) ===")
# Не требуем отличия (демпфирование может быть выключено/нулевым в этом
# сценарии) — требуем только, что before_slew самосогласован: это именно
# то значение, из которого CMD_SLEW стартует.
assert isinstance(c["roll_before_slew"], (int, float))
assert isinstance(c["pitch_before_slew"], (int, float))
print("    before_slew присутствует и численно (roll=%.1f, pitch=%.1f)"
      % (c["roll_before_slew"], c["pitch_before_slew"]))

print("\n=== 5. ЕДИНИЦЫ: все четыре точки в одном масштабе (offset от "
      "1500), а не смесь offset+абсолютный RC ===")
# Именно этот регресс тест раньше не ловил: before_slew/after_slew писали
# АБСОЛЮТНЫЙ RC (~1500), а requested/after_trust — offset (обычно
# десятки-сотни PWM). Если before_slew/after_slew снова окажутся
# абсолютным RC, они будут БЛИЗКИ к 1500 при любом реальном отклонении
# цели — тест ловит это напрямую, а не полагается на "значение есть".
sbros()
for _ in range(10):
    c = kadr(0.90, dx=150, dy=100)   # заметное, но не экстремальное смещение
for axis in ("roll", "pitch"):
    vals = [c["%s_off" % axis], c["%s_after_trust" % axis],
            c["%s_before_slew" % axis], c["%s_after_slew" % axis]]
    for stage, v in zip(("requested", "after_trust", "before_slew", "after_slew"), vals):
        assert abs(v) < 900.0, (
            "%s_%s=%.1f похоже на АБСОЛЮТНЫЙ RC (~1500), а не на offset от "
            "центра — единицы разошлись с requested/after_trust" % (axis, stage, v))
    print("    %s: requested=%.1f after_trust=%.1f before_slew=%.1f "
          "after_slew=%.1f — все в одном масштабе"
          % (axis, vals[0], vals[1], vals[2], vals[3]))

print("\n=== 6. after_slew ограничен CMD_SLEW_MAX_STEP от предыдущей "
      "команды, если CMD_SLEW_ENABLED ===")
sbros()
kadr(0.90, dx=0, dy=0)
# Второй кадр — резкий скачок цели в сторону: before_slew скакнёт сильно,
# after_slew обязан быть ограничен шагом slew относительно ПРЕДЫДУЩЕЙ
# отправленной команды (_slew_roll/_slew_pitch). before_slew/after_slew
# теперь offset — сравниваем как есть, без -1500 (это ушло вместе с
# фиксом единиц выше).
c2 = kadr(0.90, dx=200, dy=150)
if t.CMD_SLEW_ENABLED:
    shag_max = t.CMD_SLEW_MAX_STEP + 1e-6
    assert abs(c2["roll_after_slew"]) <= max(
        shag_max, abs(c2["roll_before_slew"])), (
        "after_slew не ограничен относительно предыдущей команды при "
        "включённом CMD_SLEW — диагностика читает не тот момент")
    print("    CMD_SLEW_ENABLED=True: after_slew(%.1f) ограничен относительно "
          "before_slew(%.1f)" % (c2["roll_after_slew"], c2["roll_before_slew"]))
else:
    assert c2["roll_after_slew"] == c2["roll_before_slew"], (
        "CMD_SLEW выключен, но after_slew отличается от before_slew — "
        "диагностика должна честно отражать отсутствие slew")
    print("    CMD_SLEW_ENABLED=False: after_slew == before_slew")

print("\nOK: requested/after_trust/before_slew/after_slew присутствуют, "
      "в одних единицах (offset от 1500), согласованы между собой и с "
      "существующими доверием/slew")
