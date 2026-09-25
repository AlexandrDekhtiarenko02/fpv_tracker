"""Задержка control на время ручной коррекции рамки (прямая просьба
оператора: "мало отвожу стик - рамка не реагирует, сильно отвожу - резко
перелетает", нужна пауза, пока рамка ещё двигается, чтобы было удобно
целиться).

ПОДТВЕРЖДЕНО ЯВНО (не угадано): пока рамка активно двигается стиком — и
ещё MANUAL_NUDGE_CONTROL_SETTLE_S секунд после отпускания — control
продолжает лететь по СТАРОЙ (до-nudge) позиции цели, как будто оператор
её не трогал. target_box_main (и, значит, оверлей на экране) при этом
двигается ЖИВО вместе со стиком — без этого оператору нечем целиться.
Как только окно устоя истекло без нового движения стика — control одним
шагом переходит на финальную (уже поправленную) позицию.

Проверяется: заморозка включается на первом же активном кадре nudge и
держится всю правку; target_box_main живой всё это время; отпускание
стика НЕ снимает заморозку сразу, запускает окно устоя; по истечении
окна — переход на финальную позицию одним шагом; повторный nudge ВНУТРИ
окна устоя не сбрасывает снимок на промежуточную позицию (снимок — от
самого начала серии правок, не от последнего micro-отпускания);
ENABLED=False — поведение как до этой правки (заморозки никогда нет);
reset_tracking()/смена lock_sequence снимают заморозку (не переживают
чужой лок); существующая ветка "not controllable" остаётся последней
линией защиты независимо от того, какой box выбран.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

AUX2_IDX = t.MANUAL_NUDGE_ROLL_AUX_IDX
AUX3_IDX = t.MANUAL_NUDGE_PITCH_AUX_IDX


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


def make_scene(seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()


def set_stick(roll_us, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick(0)
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


ROLL_US = 300.0   # заметно за пределами MANUAL_NUDGE_DEADBAND_US=60
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = True
t.MANUAL_NUDGE_CONTROL_SETTLE_S = 1.0

print("=== 1. Покой: заморозки нет ===")
capture()
assert t._nudge_frozen_box is None
assert t._match_dbg.get("nudge_control_frozen") in (0, None)
print("    _nudge_frozen_box=None, nudge_control_frozen=0")

print("\n=== 2. Первый активный кадр nudge: заморозка включается СРАЗУ, "
      "снимок = box ДО этого кадра ===")
with t.state_lock:
    _box_before = t.target_box_main
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1
assert t._nudge_frozen_box == _box_before, (
    "снимок заморозки обязан совпадать с box ДО начала правки")
assert t._match_dbg.get("nudge_control_frozen") == 1, (
    "control обязан считаться замороженным уже на первом активном кадре nudge")
print("    nudge_control_frozen=1, _nudge_frozen_box == box до правки")

print("\n=== 3. Серия кадров активного nudge: заморозка держится, снимок "
      "НЕ меняется, а target_box_main (видимая рамка) едет живо ===")
_frozen_snapshot = t._nudge_frozen_box
_boxes_seen = []
for _ in range(5):
    tick()
    assert t._match_dbg.get("nudge_control_frozen") == 1
    assert t._nudge_frozen_box == _frozen_snapshot, (
        "снимок заморозки сдвинулся ВО ВРЕМЯ активного nudge — обязан "
        "оставаться от самого начала правки")
    with t.state_lock:
        _boxes_seen.append(t.target_box_main)
assert len(set(_boxes_seen)) > 1, (
    "target_box_main не менялся ВО ВРЕМЯ nudge — оператору нечем "
    "целиться на экране, живая визуальная обратная связь потеряна"
)
assert _boxes_seen[-1] != _frozen_snapshot, (
    "target_box_main (живой) совпал с замороженным control-box — "
    "разделение между visual и control не работает")
print("    заморозка держится %d кадров, target_box_main реально уехал "
      "от замороженного control-box (живая визуальная обратная связь есть)"
      % 5)

print("\n=== 4. Отпускание стика: заморозка НЕ снимается сразу — "
      "запускается окно устоя ===")
set_stick(0)
tick()
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
assert t._nudge_frozen_box is not None, (
    "заморозка снялась СРАЗУ на отпускании — окно устоя не запустилось")
assert t._match_dbg.get("nudge_control_frozen") == 1, (
    "control обязан оставаться замороженным сразу после отпускания — "
    "окно устоя ещё не прошло")
_remaining0 = t._match_dbg.get("nudge_settle_remaining_ms")
assert _remaining0 is not None and _remaining0 > 0, (
    "nudge_settle_remaining_ms обязан быть положительным сразу после "
    "отпускания, получили %r" % _remaining0)
print("    сразу после отпускания: nudge_control_frozen=1, "
      "nudge_settle_remaining_ms=%.0f (~%.0f ожидали)"
      % (_remaining0, t.MANUAL_NUDGE_CONTROL_SETTLE_S * 1000.0))

print("\n=== 5. Окно устоя отсчитывает время (не кадры) — по кадру "
      "остаётся меньше, чем на прошлом ===")
tick()
_remaining1 = t._match_dbg.get("nudge_settle_remaining_ms")
assert _remaining1 is not None and _remaining1 < _remaining0, (
    "nudge_settle_remaining_ms не уменьшился между кадрами (%.0f -> %.0f)"
    % (_remaining0, _remaining1))
print("    remaining_ms: %.0f -> %.0f" % (_remaining0, _remaining1))

print("\n=== 6. По истечении окна устоя — переход на финальную позицию "
      "одним шагом, control_frozen=0 ===")
with t.state_lock:
    _final_box = t.target_box_main
while t._match_dbg.get("nudge_control_frozen") == 1:
    tick()
assert t._nudge_frozen_box is None
assert t._match_dbg.get("nudge_settle_remaining_ms") is None
with t.state_lock:
    _box_now = t.target_box_main
assert _box_now == _final_box, (
    "target_box_main продолжил меняться уже ПОСЛЕ отпускания стика — не "
    "должен, коррекция закончилась на re-anchor")
print("    окно устоя истекло: nudge_control_frozen=0, "
      "_nudge_frozen_box=None, control перешёл на финальную позицию")

print("\n=== 7. Повторный nudge ВНУТРИ окна устоя: снимок НЕ сбрасывается "
      "на промежуточную позицию — остаётся от начала НОВОЙ серии правок, "
      "а таймер устоя отменяется ===")
with t.state_lock:
    _box_a = t.target_box_main
set_stick(ROLL_US)
tick()   # начало новой серии
assert t._nudge_frozen_box == _box_a
for _ in range(3):
    tick()
set_stick(0)
tick()   # отпустили -> окно устоя пошло
_mid_frozen = t._nudge_frozen_box
assert t._match_dbg.get("nudge_settle_remaining_ms") is not None
tick()   # ещё один кадр внутри окна устоя (не отпустили полностью долго)
set_stick(ROLL_US)
tick()   # повторный nudge ДО истечения окна устоя
assert t._match_dbg.get("manual_nudge") == 1
assert t._nudge_frozen_box == _mid_frozen, (
    "повторный nudge внутри окна устоя обязан сохранить ИСХОДНЫЙ снимок "
    "серии правок, а не взять текущую (промежуточную) позицию — иначе "
    "control частично 'утекает' за каждым micro-отпусканием"
)
assert t._match_dbg.get("nudge_settle_remaining_ms") is None, (
    "окно устоя обязано отмениться при возобновлении nudge"
)
print("    снимок серии правок не сбросился на промежуточную позицию, "
      "окно устоя корректно отменилось")
set_stick(0)
while t._match_dbg.get("nudge_control_frozen") == 1:
    tick()

print("\n=== 8. MANUAL_NUDGE_CONTROL_DELAY_ENABLED=False: заморозки нет "
      "никогда, поведение как до этой правки ===")
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = False
set_stick(ROLL_US)
for _ in range(5):
    tick()
    assert t._nudge_frozen_box is None
    assert t._match_dbg.get("nudge_control_frozen") in (0, None)
set_stick(0)
tick()
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = True
print("    5 активных кадров nudge при ENABLED=False -> заморозка не "
      "включилась ни разу")

print("\n=== 9. reset_tracking() снимает заморозку — следующий лок не "
      "наследует чужой box ===")
set_stick(ROLL_US)
tick()
assert t._nudge_frozen_box is not None, "тест сам по себе негоден: заморозка не включилась"
t.reset_tracking(to_acq=False)
assert t._nudge_frozen_box is None, (
    "_nudge_frozen_box пережил reset_tracking() — следующий лок мог бы "
    "унаследовать box совсем другого захода")
assert t._nudge_settle_until_t is None
set_stick(0)
capture()
print("    reset_tracking() очищает _nudge_frozen_box/_nudge_settle_until_t")

print("\n=== 10. Смена lock_sequence снимает заморозку внутри "
      "_update_control_from_target_impl (защита от чужого лока без "
      "полного reset_tracking) ===")
set_stick(ROLL_US)
tick()
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
t.lock_sequence += 1   # имитация быстрого reacq без полного reset
t.update_control_from_target()
assert t._nudge_frozen_box is None, (
    "смена lock_sequence не сбросила заморозку — control мог бы лететь "
    "по box уже потерянного лока")
set_stick(0)
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
tick()

print("\n=== 11. По исходному тексту: заморозка читается ДО ветки "
      "'not controllable or box is None' — существующая защита не "
      "обходится ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def _update_control_from_target_impl():")
i_freeze = src.index("if _nudge_frozen_box is not None:", i_fn)
i_safety = src.index("if not controllable or box is None:", i_fn)
assert i_freeze < i_safety, (
    "чтение заморозки должно идти ДО проверки 'not controllable or box "
    "is None' — иначе порядок исполнения неочевиден из кода")
print("    заморозка читается раньше safety-ветки 'not controllable'")

print("\n=== 12. CSV: nudge_control_frozen/nudge_settle_remaining_ms на "
      "месте ===")
assert "nudge_control_frozen,nudge_settle_remaining_ms," in src
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
assert '_match_dbg.get("nudge_control_frozen")' in row_body
assert '_match_dbg.get("nudge_settle_remaining_ms")' in row_body
print("    колонки на месте и читаются в _capture_flight_row")

print("\nOK: пока рамка активно двигается стиком (и ещё "
      "MANUAL_NUDGE_CONTROL_SETTLE_S=%.1fс после отпускания) control "
      "продолжает лететь по СТАРОЙ позиции цели, рамка на экране едет "
      "живо; повторный nudge внутри окна устоя не 'утекает' на "
      "промежуточную позицию; ENABLED=False возвращает поведение к "
      "прежнему; reset_tracking/смена лока снимают заморозку; "
      "существующая защита 'not controllable' не обойдена"
      % t.MANUAL_NUDGE_CONTROL_SETTLE_S)
