"""Задержка control на время ручной коррекции рамки (прямая просьба
оператора: "мало отвожу стик - рамка не реагирует, сильно отвожу - резко
перелетает", нужна пауза, пока рамка ещё двигается, чтобы было удобно
целиться) — ВТОРАЯ версия, после ревью по c6fb464.

ПЕРВАЯ ВЕРСИЯ держала заморозку ЕЩЁ 1с ПОСЛЕ отпускания стика (окно
устоя). Ревью нашло это архитектурно неверным: к моменту отпускания
reanchor_tracker_at_current_box() уже отработал — новая geometry_epoch,
template пересобран, flow/match снова считаются по новой позиции, а
control ещё секунду продолжал бы читать СТАРЫЙ box. Смесь двух временных
состояний, а "резкий скачок reference" не устранялся, а просто
переносился на секунду позже.

ЭТА ВЕРСИЯ. Заморозка живёт СТРОГО пока стик реально отклонён — снимается
В ТОТ ЖЕ МОМЕНТ, что и сам reanchor, без отдельного таймера. Переход на
новую позицию доверен уже существующему, уже проверенному механизму
reanchor_tracker_at_current_box() -> _reset_geometry_history(): тот
обнуляет tau/LOS-rate/prev_box_cx,cy/target_vx,vy_smoothed — то есть
ровно то, что не даёт скачку позиции превратиться в ложную скорость.
_slew_roll/pitch/yaw НЕ сбрасываются и сглаживают сам шаг команды.

ВТОРОЙ ФИКС ЭТОГО РЕВЬЮ (безопасность): "стик в дедбенде" и "AUX/RC-
сигнал пропал ПОСРЕДИ активной правки" раньше вели к одному и тому же
коду — оба подтверждались reanchor'ом как "оператор закончил". Устаревший
RC теперь — авария (MANUAL_NUDGE abort), не подтверждение: без reanchor,
без заморозки, просто падение в обычную live-логику этого же кадра.

ТРЕТИЙ ФИКС: nudge_control_frozen в CSV пишется ПОСЛЕ проверки смены
lock_sequence, не до — иначе кадр самой смены мог соврать в логе.
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
    "целиться на экране, живая визуальная обратная связь потеряна")
assert _boxes_seen[-1] != _frozen_snapshot, (
    "target_box_main (живой) совпал с замороженным control-box")
print("    заморозка держится %d кадров без изменения снимка, "
      "target_box_main реально уехал от него (визуальная обратная связь "
      "есть)" % 5)

print("\n=== 4. НАЙДЕНО ревью (п.1): отпускание стика снимает заморозку "
      "В ТОТ ЖЕ КАДР, что и reanchor — не секундой позже. control сразу "
      "использует НОВЫЙ (только что переустановленный) target_box_main ===")
_epoch_before_release = t.geometry_epoch
set_stick(0)
tick()
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
assert t.geometry_epoch == _epoch_before_release + 1, (
    "reanchor не сработал на кадре отпускания")
assert t._nudge_frozen_box is None, (
    "заморозка пережила reanchor — control ещё кадр читал бы старый box "
    "одновременно с уже новой geometry/template/flow (ровно то, что "
    "нашло ревью)")
assert t._match_dbg.get("nudge_control_frozen") == 0, (
    "nudge_control_frozen обязан стать 0 РОВНО на кадре reanchor, не позже")
print("    geometry_epoch %d -> %d (reanchor) И nudge_control_frozen=0 "
      "ОДНИМ И ТЕМ ЖЕ кадром — заморозка не переживает reanchor ни на "
      "кадр" % (_epoch_before_release, t.geometry_epoch))

print("\n=== 5. target_vx/vy_smoothed реально 0.0 сразу после reanchor — "
      "скачок box НЕ прочитан регулятором как мгновенная скорость (та "
      "самая защита от 'ложной скорости', на которую опирается фикс "
      "п.1). prev_box_cx/cy НЕ проверяем на None: _update_control_from_"
      "target_impl() в ЭТОМ ЖЕ кадре законно переустанавливает их под "
      "НОВЫЙ box — это база для СЛЕДУЮЩЕГО сравнения, не утечка старого ===")
assert t.target_vx_smoothed == 0.0 and t.target_vy_smoothed == 0.0, (
    "target_vx/vy_smoothed не обнулены после reanchor — скачок box мог "
    "быть прочитан как реальная скорость цели")
print("    target_vx/vy_smoothed=0.0 сразу после reanchor — "
      "_reset_geometry_history() внутри reanchor реально это делает, а "
      "не только по комментарию")

print("\n=== 6. НАЙДЕНО ревью (п.3, SAFETY): устаревший RC ПОСРЕДИ "
      "активной правки — авария, НЕ подтверждение. Без reanchor, без "
      "заморозки, падение в обычную live-логику этого же кадра ===")
with t.state_lock:
    _box_before_6 = t.target_box_main
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1, "тест сам по себе негоден"
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
_epoch_before_stale = t.geometry_epoch
_events = []
t.flight_log.event = _events.append
# RC не обновляется — rc_link_ts стареет естественно с мокнутыми часами.
# МЕЛКИМИ шагами (FRAME_DT каждый), не одним прыжком: один большой прыжок
# сам пересёк бы НЕСВЯЗАННЫЙ порог frame_gap (FLOW_RASSH_SVEZH_S=0.20с в
# _update_control_from_target_impl) и вызвал бы _reset_geometry_history
# по СОВСЕМ ДРУГОЙ причине — тест бы путал два разных источника bump'а
# geometry_epoch (тот же класс аккуратности, что уже потребовался для
# cam_jump_dt_ms в Camera Jump Shadow).
_elapsed = 0.0
while _elapsed <= t.MANUAL_NUDGE_RC_FRESH_S:
    _clk.tick(FRAME_DT)
    _elapsed += FRAME_DT
    t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0
assert t.geometry_epoch == _epoch_before_stale, (
    "geometry_epoch вырос на кадре stale-RC — значит reanchor всё-таки "
    "сработал, подтвердив промежуточную (возможно случайную) позицию, "
    "именно то, что ревью просило НЕ делать")
assert t._nudge_frozen_box is None, (
    "заморозка не снялась на stale-RC abort")
_abort_events = [e for e in _events if e.startswith("MANUAL_NUDGE abort")]
assert len(_abort_events) == 1, (
    "ожидали ровно 1 событие MANUAL_NUDGE abort, получили %d: %s"
    % (len(_abort_events), _abort_events))
_reanchor_events = [e for e in _events if e.startswith("REANCHOR")]
assert not _reanchor_events, (
    "REANCHOR всё-таки случился на stale-RC кадре: %s" % _reanchor_events)
print("    событие: %s; geometry_epoch не изменился (%d), заморозка "
      "снята, REANCHOR не вызывался" % (_abort_events[0], t.geometry_epoch))
set_stick(0)
tick()   # вернуть RC в норму для дальнейших секций

print("\n=== 7. НАЙДЕНО ревью (п.4): nudge_control_frozen в CSV не "
      "врёт на кадре смены lock_sequence — пишется ПОСЛЕ safety-сброса ===")
set_stick(ROLL_US)
tick()
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
t.lock_sequence += 1   # имитация быстрого reacq
t.update_control_from_target()
assert t._nudge_frozen_box is None, (
    "смена lock_sequence не сбросила заморозку")
assert t._match_dbg.get("nudge_control_frozen") == 0, (
    "nudge_control_frozen соврал '1' на кадре, где заморозка уже снята "
    "сменой lock_sequence — диагностика писалась ДО safety-сброса")
print("    nudge_control_frozen=0 корректно на кадре смены lock_sequence "
      "(диагностика пишется после safety-сброса)")
set_stick(0)
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
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
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
t.reset_tracking(to_acq=False)
assert t._nudge_frozen_box is None, (
    "_nudge_frozen_box пережил reset_tracking()")
set_stick(0)
capture()
print("    reset_tracking() очищает _nudge_frozen_box")

print("\n=== 10. По исходному тексту: заморозка читается ДО ветки "
      "'not controllable or box is None' — существующая защита не "
      "обходится ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def _update_control_from_target_impl():")
i_freeze = src.index(
    "box = _nudge_frozen_box if _nudge_frozen_box is not None else _live_box",
    i_fn)
i_safety = src.index("if not controllable or box is None:", i_fn)
assert i_freeze < i_safety
print("    заморозка читается раньше safety-ветки 'not controllable'")

print("\n=== 11. CSV: nudge_control_frozen на месте (nudge_settle_"
      "remaining_ms убран вместе с окном устоя) ===")
assert "nudge_control_frozen," in src
assert "nudge_settle_remaining_ms" not in src, (
    "убранное окно устоя оставило след в CSV/коде — nudge_settle_"
    "remaining_ms всё ещё где-то упоминается")
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
assert '_match_dbg.get("nudge_control_frozen")' in row_body
print("    колонка на месте, окно устоя нигде не осталось")

print("\nOK: заморозка control на время ручной коррекции живёт строго "
      "пока стик отклонён, снимается ОДНИМ кадром с reanchor (не смешивая "
      "старый box с уже новой geometry-историей), переход сглажен уже "
      "существующим _reset_geometry_history()+slew; устаревший RC "
      "посреди правки — авария (без reanchor/заморозки), не "
      "подтверждение; диагностика в CSV не врёт на кадре смены лока")
