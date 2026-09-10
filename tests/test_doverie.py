"""Усиление урезается при УХУДШЕНИИ слежения, а не по абсолютному порогу.

До сих пор мёртвый захват и еле держащийся обрабатывались одинаково, с полным
усилением. Когда эталон совпадает плохо, коробка шумит — и этот шум шёл в
команду как настоящая ошибка прицела.

Главное требование — ОТСУТСТВИЕ РЕГРЕССА: пока слежение стабильно, неважно
на каком уровне, поведение обязано быть в точности прежним. Абсолютный порог
этого не даёт: на одном заходе 0.4 — норма, на другом беда, и порог по одному
вылету срезал бы усиление на ровном месте.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


def kadr(score, psr=None, dy=40):
    t.last_match_score = score
    t._match_dbg = {} if psr is None else {"psr": psr}
    t.target_box_main = (CX - 20, CY - 20 + dy, CX + 20, CY + 20 + dy)
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
    return t.global_pitch_cmd


def sbros():
    t._dover_score_ema = None
    t._dover_psr_ema = None
    t._final_zamorozhen = False
    t._slew_pitch = 1500.0
    t._slew_roll = 1500.0
    t._slew_yaw = 1500.0
    t._pitch_pri_loke = None
    t.pitch_integral = 0.0
    t.prev_ady_ctrl = 0.0


print("=== 1. Стабильное слежение — поведение прежнее на ЛЮБОМ уровне ===")
for uroven in (0.85, 0.45, 0.25):
    sbros()
    for _ in range(30):
        kom = kadr(uroven)
    print("    качество держится на %.2f -> тангаж %d" % (uroven, kom))
    assert abs(kom - 1500) > 1, "команда исчезла при стабильном слежении"
sbros()
for _ in range(30):
    horosho = kadr(0.85)
sbros()
for _ in range(30):
    ploho_no_rovno = kadr(0.25)
print("    разница между уровнями 0.85 и 0.25: %d PWM"
      % abs(horosho - ploho_no_rovno))
assert horosho == ploho_no_rovno, (
    "стабильное слежение на разном уровне даёт разную команду — значит "
    "решает абсолютный порог, и он врежет усиление на ровном месте")

print("\n=== 2. Ухудшение урезает усиление ===")
sbros()
for _ in range(30):
    do = kadr(0.80)
posle = kadr(0.30)          # резкое падение относительно своего уровня
print("    было %.2f, стало 0.30 -> тангаж %d вместо %d" % (0.80, posle, do))
assert abs(posle - 1500) < abs(do - 1500), (
    "падение качества не урезало команду")

print("\n=== 3. Двойник урезает даже при хорошем совпадении ===")
sbros()
for _ in range(30):
    bez = kadr(0.80, psr=6.0)
s_dvoynikom = kadr(0.80, psr=1.5)   # совпадение то же, но рядом есть похожее
print("    psr 6.0 -> %d;  psr 1.5 при том же совпадении -> %d"
      % (bez, s_dvoynikom))
assert abs(s_dvoynikom - 1500) < abs(bez - 1500), (
    "psr не учитывается: коробка вот-вот перескочит на двойника, а контур "
    "об этом не знает")

print("\n=== 4. Урезание не до нуля ===")
sbros()
for _ in range(30):
    kadr(0.90)
for _ in range(10):
    hudshee = kadr(0.01)
print("    при полном развале качества тангаж %d" % hudshee)
assert abs(hudshee - 1500) > 0.5, (
    "контур оглох полностью: в финале это хуже, чем вялая реакция")
assert t.TRUST_MIN > 0.0

print("\n=== 5. Уровень сбрасывается между заходами ===")
sbros()
for _ in range(30):
    kadr(0.80)
assert t._dover_score_ema is not None
t.target_visible = False
t.target_controllable = False
t.target_box_main = None
t.update_control_from_target()
assert t._dover_score_ema is None, (
    "уровень прошлой цели перешёл на новую: на ней он выглядел бы как резкое "
    "ухудшение или, наоборот, скрыл бы настоящее")
print("    после потери цели уровень сброшен")

print("\nOK: стабильность не трогаем, ухудшение урезаем, двойника ловим")
