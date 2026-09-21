"""Стендовые правки, внесённые до вылета на 7a8065b (не требуют полёта).

Проверяются четыре вещи:
  1. Приём MSP_RC в кеш приёмника закрыт при возможной маскировке override —
     устаревший статус больше не пускает наши эхо-команды за стики пилота.
  2. Отправка на FC честно докладывает результат: last_sent_channels пишется
     только на успехе, есть переоткрытие порта.
  3. Состояние конкретного захвата чистится между заходами (_score_do_rosta,
     _tau_hold_*, _size_R_boost) — раньше протекало в следующий заход.
  4. Выдержки финала и сближения считаются по ВРЕМЕНИ, а не по числу кадров;
     разрыв между кадрами обнуляет выдержку.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
t = offline.load_tracker()


print("=== 1. Устаревший статус override НЕ пускает эхо за стики ===")
# Приём в кеш приёмника — только когда override точно не маскирует.
assert "override_maybe_masking = aux4_state and (fc_ovr or stale_ovr)" in src, (
    "признак возможной маскировки собран не по AUX4 и статусу")
assert "if OBSERVE_ONLY or not override_maybe_masking:" in src, (
    "приём MSP_RC в кеш приёмника не закрыт при возможной маскировке")
# Старая дырявая форма (stale_ovr как РАЗРЕШЕНИЕ на приём) не должна вернуться.
assert "or not fc_ovr or stale_ovr:" not in src, (
    "вернулась старая логика: устаревший статус снова ПУСКАЕТ приём MSP_RC")
print("    приём закрыт при поднятом AUX4 и (режим ИЛИ устаревший статус)")


print("\n=== 2. Отправка на FC докладывает результат ===")
i = src.index("def send_msp_set_raw_rc(")
telo = src[i:i + 1200]
assert "return True" in telo and "return False" in telo, (
    "send_msp_set_raw_rc не возвращает результат отправки")
assert "def _fc_reconnect(" in src, "нет переоткрытия порта после обрыва"
assert "_fc_reconnect()" in telo, "send не пытается переоткрыть порт при сбое"
# last_sent_channels помечается ТОЛЬКО на успешной отправке.
j = src.index("sent_ok = send_msp_set_raw_rc(channels)")
posle = src[j:j + 600]
assert "if sent_ok:" in posle and 'app_state["last_sent_channels"]' in posle, (
    "last_sent_channels пишется без проверки, что кадр реально ушёл")
assert posle.index("if sent_ok:") < posle.index('app_state["last_sent_channels"]'), (
    "last_sent_channels пишется до проверки успеха отправки")
print("    last_sent_channels только на успехе; порт переоткрывается")


print("\n=== 3. Состояние захвата чистится между заходами ===")
# Выставляем состояние «как в разгар захода». В реальном полёте
# _score_do_rosta/_tau_hold_*/_size_R_boost могут стать ненулевыми ТОЛЬКО
# пока controllable=True (их пишет код внутри ветки controllable) —
# поэтому prev_controllable_for_launch=True здесь не произвольная деталь
# теста, а честное отражение того единственного пути, которым это
# состояние вообще могло появиться.
t._score_do_rosta = 0.9
t._tau_hold_val = 3.0
t._tau_hold_t = t.time.monotonic()
t._size_R_boost = 2.5
t.prev_controllable_for_launch = True
# Теряем цель — уходим в ветку not controllable. Сброс geometry-history
# теперь edge-triggered (code review по 15ce5bf, п.3): срабатывает на
# ГРАНИЦЕ controllable->not-controllable, а не на каждом кадре вне
# управления — иначе долгий ACQ/HOLD/LOST плодил бы новую geometry_epoch
# и событие в лог почти на каждый кадр.
t.target_visible = False
t.target_controllable = False
t.target_box_main = None
t.update_control_from_target()
assert t._score_do_rosta is None, (
    "_score_do_rosta не сброшен: score прошлой цели запретит рост рамки новой")
assert t._tau_hold_val is None, "_tau_hold_val протёк в следующий заход"
assert t._size_R_boost == 1.0, "_size_R_boost протёк в следующий заход"
print("    _score_do_rosta, _tau_hold_val, _size_R_boost обнулены")


print("\n=== 4. Выдержка по ВРЕМЕНИ: держится, ломается, обнуляется разрывом ===")
# Помощник принимает время аргументом — проверяем без пауз, инъекцией времени.
# Шаг 0.05 c — как реальный кадр, заведомо ниже порога разрыва (0.2 c).
sost = {"t0": None, "tik": None}
assert t._vyderzhka_gotova(sost, True, 100.00, 0.25) is False, "старт не мгновенный"
for tt in (100.05, 100.10, 100.15, 100.20):
    assert t._vyderzhka_gotova(sost, True, tt, 0.25) is False, "%.2f c < 0.25 c" % (tt - 100.0)
assert t._vyderzhka_gotova(sost, True, 100.25, 0.25) is True, "0.25 c должно подтвердить"
# Нарушение условия обнуляет.
assert t._vyderzhka_gotova(sost, False, 100.30, 0.25) is False, "разрыв условия не сбросил"
assert t._vyderzhka_gotova(sost, True, 100.35, 0.25) is False, "после сброса не с нуля"

# Провал между кадрами (> CONFIRM_MAX_GAP_S = 0.2 c) перезапускает выдержку.
gap = {"t0": None, "tik": None}
assert t._vyderzhka_gotova(gap, True, 200.00, 0.25) is False           # старт
# разрыв 0.50 c > порога: даже при истинном условии выдержка начинается заново
assert t._vyderzhka_gotova(gap, True, 200.50, 0.25) is False, (
    "большой межкадровый разрыв засчитался как выдержка")
# от перезапуска (200.50) снова нужно набрать 0.25 c шагами по 0.05
for tt in (200.55, 200.60, 200.65, 200.70):
    assert t._vyderzhka_gotova(gap, True, tt, 0.25) is False
assert t._vyderzhka_gotova(gap, True, 200.75, 0.25) is True            # 0.25 c от перезапуска
print("    держится по времени; ломается при разрыве условия и при провале кадров")

print("\nOK: стендовые правки на месте")
