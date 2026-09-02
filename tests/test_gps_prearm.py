"""GPS проверяется ДО АРМА постоянно, а не один раз на старте.

Почему одной проверки мало. Спутники подтягиваются десятками секунд, и на
восьмой секунде после запуска захвата не бывает никогда. Проверка готовности
всегда написала бы «GPS нет», а пилот не увидел бы момента, когда захват
появился, и не понял бы, ждать ему ещё или лететь так.

Отдельно следим, чтобы на боевом борту, где GPS нет по замыслу, вечная
надпись про его отсутствие не висела поверх картинки.
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

_POLYA = ("armed", "gps_fix", "gps_sats", "gps_lat", "gps_lon", "gps_sats_max")


def _sostoyanie(zhdyom=True, **st):
    t = offline.load_tracker()
    t.GPS_EXPECTED = zhdyom
    with t.state_lock:
        for k in _POLYA:
            t.app_state.pop(k, None)
        t.app_state.update(st)
    return t


def _est_li_nadpis(t):
    kadr = np.full((480, 640, 4), 40, np.uint8)
    t.draw_gps_prearm(kadr)
    return int((kadr[:, :, :3] != 40).any(axis=2).sum())


print("=== 1. Появление спутников видно вживую ===")
prezhde = None
for sats, fix in ((0, 0), (2, 0), (5, 1), (9, 1)):
    t = _sostoyanie(gps_fix=fix, gps_sats=sats,
                    gps_lat=50.45 if fix else 0.0,
                    gps_lon=30.52 if fix else 0.0)
    stroka, cvet = t._gps_prearm_stroka()
    print("    спутников %d, захват %d -> %s" % (sats, fix, stroka))
    assert stroka != prezhde, "надпись не меняется — прогресс не виден"
    prezhde = stroka
    if sats:
        assert str(sats) in stroka, "число спутников не показано"

print("\n=== 2. Слабый захват отличается от годного ===")
t = _sostoyanie(gps_fix=1, gps_sats=3, gps_lat=50.45, gps_lon=30.52)
slabo, _ = t._gps_prearm_stroka()
t = _sostoyanie(gps_fix=1, gps_sats=9, gps_lat=50.45, gps_lon=30.52)
horosho, cvet_ok = t._gps_prearm_stroka()
print("    3 спутника : %s" % slabo)
print("    9 спутников: %s" % horosho)
assert slabo != horosho, "трёх спутников хватило бы, а этого мало для сверки"
assert cvet_ok == (90, 230, 90), "годный захват должен быть зелёным"

print("\n=== 3. После арма надпись убирается ===")
t = _sostoyanie(armed=True, gps_fix=1, gps_sats=9, gps_lat=50.45, gps_lon=30.52)
assert t._gps_prearm_stroka() is None, "надпись загораживает картинку на заходе"
assert _est_li_nadpis(t) == 0, "надпись всё равно нарисовалась"

print("\n=== 4. На борту без GPS надпись не висит ===")
t = _sostoyanie(zhdyom=False, gps_fix=0, gps_sats=0, gps_lat=0.0, gps_lon=0.0)
assert t._gps_prearm_stroka() is None, (
    "на боевом борту GPS нет по замыслу — вечная надпись только мешает")
assert _est_li_nadpis(t) == 0

print("\n=== 5. Но если спутники хоть раз были — модуль есть, и мы следим ===")
t = _sostoyanie(zhdyom=False, gps_fix=0, gps_sats=0,
                gps_lat=0.0, gps_lon=0.0, gps_sats_max=7)
got = t._gps_prearm_stroka()
print("   ", got)
assert got is not None, (
    "спутники уже появлялись, значит модуль на борту — потеря захвата "
    "обязана быть видна")
assert _est_li_nadpis(t) > 0

print("\n=== 6. Молчание GPS отличается от поиска спутников ===")
t = _sostoyanie(gps_sats=0)          # ни одного ответа не приходило
molchit, _ = t._gps_prearm_stroka()
t = _sostoyanie(gps_fix=0, gps_sats=0, gps_lat=0.0, gps_lon=0.0)
ishchet, _ = t._gps_prearm_stroka()
print("    нет ответа : %s" % molchit)
print("    ищет       : %s" % ishchet)
assert molchit != ishchet, (
    "неотвечающий модуль и ищущий спутники — разные неисправности")

print("\n=== 7. Надпись в видимой части кадра и без кириллицы ===")
for st in ({"gps_sats": 0},
           {"gps_fix": 0, "gps_sats": 11, "gps_lat": 0.0, "gps_lon": 0.0},
           {"gps_fix": 1, "gps_sats": 3, "gps_lat": 50.45, "gps_lon": 30.52},
           {"gps_fix": 1, "gps_sats": 12, "gps_lat": 50.45, "gps_lon": 30.52}):
    t = _sostoyanie(**st)
    stroka, _ = t._gps_prearm_stroka()
    assert all(ord(c) < 128 for c in stroka), (
        "кириллица в оверлее рисуется как «?»: %r" % stroka)
    kadr = np.full((480, 640, 4), 40, np.uint8)
    t.draw_gps_prearm(kadr)
    est = (kadr[:, :, :3] != 40).any(axis=2)
    stolbcy = np.flatnonzero(est.any(axis=0))
    kray = 640 * (1.0 - t.STARTUP_SAFE_FRAC) / 2.0
    assert stolbcy[0] >= kray - 1 and stolbcy[-1] <= 640 - kray + 1, (
        "надпись %r вылезает за видимую часть кадра" % stroka)
    print("    x %d..%d  %s" % (stolbcy[0], stolbcy[-1], stroka))

print("\n=== 8. Не загораживает опрос датчиков ===")
t = _sostoyanie(gps_fix=1, gps_sats=9, gps_lat=50.45, gps_lon=30.52)
t.flight_log.event = lambda *a, **k: None
with t.state_lock:
    t.app_state.update({"rc_link_ts": time.monotonic(), "fc_pitch_deg": 0.0,
                        "alt_seen": True, "imu_seen": True})
t._startup_lines, t._startup_ok, t._startup_until = [], False, None
t._startup_sensor_check()
verh = np.full((480, 640, 4), 40, np.uint8)
t.draw_startup_check(verh)
niz = np.full((480, 640, 4), 40, np.uint8)
t.draw_gps_prearm(niz)
sv = np.flatnonzero((verh[:, :, :3] != 40).any(axis=2).any(axis=1))
sn = np.flatnonzero((niz[:, :, :3] != 40).any(axis=2).any(axis=1))
print("    опрос y %d..%d, GPS y %d..%d" % (sv[0], sv[-1], sn[0], sn[-1]))
assert sn[0] > sv[-1], "строки налезают друг на друга"

print("\nOK: за появлением спутников можно следить до самого арма")
