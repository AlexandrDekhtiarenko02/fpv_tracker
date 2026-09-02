"""Проверка датчиков при запуске должна ловить МОЛЧАНИЕ, а не подмену.

Почему это важно. Опасен не отказ датчика сам по себе, а отказ, который
выглядит как исправность. В app_state у части полей есть значения по
умолчанию (например, rc_channels = 1500), и если судить о живости по одному
наличию значения, молчащий полётник отрапортует «всё в порядке», а кампания
налётов запишет ровные числа, взявшиеся из воздуха.

Отдельно проверяем, что надпись «всё хорошо» действительно исчезает: она
должна не мешать пилоту, а отказ — наоборот, висеть, пока его не исправят.
"""
import io
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402


# load_tracker отдаёт один и тот же модуль (import кэширует), поэтому
# состояние надо гасить руками — иначе следующий случай унаследует ответы
# предыдущего и проверка станет ложно-зелёной.
_POLYA = ("rc_link_ts", "fc_pitch_deg", "alt_seen", "imu_seen", "gps_lat")


def _proverit(sostoyanie):
    t = offline.load_tracker()
    t.flight_log.event = lambda *a, **k: None
    with t.state_lock:
        for k in _POLYA:
            t.app_state.pop(k, None)
        t.app_state.update(sostoyanie)
    t._startup_lines, t._startup_ok, t._startup_until = [], False, None
    t._startup_sensor_check()
    return t


def _zhivoy_polyotnik(**extra):
    d = {"rc_link_ts": time.monotonic(), "fc_pitch_deg": 0.0,
         "alt_seen": True, "imu_seen": True}
    d.update(extra)
    return d


print("=== 1. Молчат все — названы все ===")
t = _proverit({})
stroka = " ".join(t._startup_lines)
print("   ", t._startup_lines)
for datchik in ("RC", "ATTITUDE", "GYRO", "BARO"):
    assert datchik in stroka, (
        "молчащий датчик %s не назван — пилот не узнает, чего не хватает"
        % datchik)
assert not t._startup_ok, "полное молчание принято за исправность"

print("\n=== 2. RC судится по свежести связи, а не по значению по умолчанию ===")
t = _proverit({"fc_pitch_deg": 0.0, "alt_seen": True, "imu_seen": True})
print("   ", t._startup_lines)
assert "RC" in " ".join(t._startup_lines), (
    "RC признан живым по значению по умолчанию — молчащий полётник "
    "отрапортует исправность")

print("\n=== 3. Устаревший ответ RC — это тоже отказ ===")
t = _proverit(_zhivoy_polyotnik(rc_link_ts=time.monotonic() - 30.0))
print("   ", t._startup_lines)
assert "RC" in " ".join(t._startup_lines), "связь пропала 30 с назад, а её считают живой"

print("\n=== 4. Один молчащий датчик — назван именно он ===")
t = _proverit(_zhivoy_polyotnik(imu_seen=False))
print("   ", t._startup_lines)
assert t._startup_lines == ["NO RESPONSE: GYRO"], "назван не тот датчик"

print("\n=== 5. Всё ответило — сообщение исчезает через отведённый срок ===")
t = _proverit(_zhivoy_polyotnik())
print("   ", t._startup_lines)
assert t._startup_ok, "исправный борт объявлен неисправным"
assert t._startup_until is not None, "сообщение об исправности повиснет навсегда"
kadr = np.full((80, 640, 4), 40, np.uint8)
t.draw_startup_check(kadr)
vidno_srazu = int((kadr[:, :, :3] != 40).any(axis=2).sum())
t._startup_until = time.monotonic() - 0.01
kadr = np.full((80, 640, 4), 40, np.uint8)
t.draw_startup_check(kadr)
vidno_potom = int((kadr[:, :, :3] != 40).any(axis=2).sum())
print("    закрашено пикселей: сразу %d, после срока %d" % (vidno_srazu, vidno_potom))
assert vidno_srazu > 0, "сообщение об исправности не рисуется вовсе"
assert vidno_potom == 0, "сообщение не исчезает и загораживает пилоту цель"

print("\n=== 6. Отказ висит, пока его не исправят ===")
t = _proverit({})
assert t._startup_until is None, "сообщение об отказе само исчезнет с экрана"
kadr = np.full((80, 640, 4), 40, np.uint8)
t.draw_startup_check(kadr)
assert int((kadr[:, :, :3] != 40).any(axis=2).sum()) > 0, "отказ не показан"

print("\n=== 7. Надписи влезают в кадр и без кириллицы ===")
import cv2  # noqa: E402
for sluchay in ({}, _zhivoy_polyotnik(), _zhivoy_polyotnik(gps_lat=50.4)):
    t = _proverit(sluchay)
    for ln in t._startup_lines:
        assert all(ord(c) < 128 for c in ln), (
            "кириллица в оверлее рисуется как «?»: %r" % ln)
        (w, _), _ = cv2.getTextSize(ln, cv2.FONT_HERSHEY_PLAIN, 1.6, 2)
        assert w + 14 <= 640, "строка %r не влезает в кадр (%d px)" % (ln, w)
        print("    %4d px  %s" % (w, ln))

print("\n=== 8. Отсутствие GPS не считается отказом ===")
t = _proverit(_zhivoy_polyotnik())
assert t._startup_ok, "борт без GPS объявлен неисправным, а на боевом его и не будет"
assert "no GPS" in " ".join(t._startup_lines), "молчание GPS никак не отмечено"

print("\nOK: молчащий датчик не может притвориться исправным")
