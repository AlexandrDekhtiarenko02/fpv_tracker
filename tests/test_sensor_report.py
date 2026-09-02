"""Отчёт по датчикам: молчащий датчик не должен притвориться исправным.

Опасен не отказ сам по себе, а отказ, который выглядит как исправность. В
app_state у части полей есть значения по умолчанию (rc_channels = 1500), а
Betaflight отвечает на запрос GPS нулями даже когда модуля нет вовсе. Оба
раза наивная проверка «поле не пустое» рапортовала бы исправность, и целый
полётный день записался бы правдоподобной чепухой.

Отдельно следим, что отчёт ПОКАЗЫВАЕТ ПРОЦЕСС: датчики оживают вразнобой,
спутники подтягиваются десятками секунд, и «всё сразу или ничего» тут не
годится.
"""
import os
import sys
import time

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

# ВСЁ, что проверки кладут в состояние, обязано быть здесь: load_tracker
# отдаёт один и тот же модуль, и забытый ключ протекает в следующий случай.
# Так уже случалось: has_baro=False из проверки про выключенный барометр
# уехал дальше и завалил проверку, которая про него ничего не знает.
_POLYA = ("armed", "gps_fix", "gps_sats", "gps_lat", "gps_lon", "gps_sats_max",
          "sensor_mask", "rc_link_ts", "fc_pitch_deg", "gyro", "acc", "mag",
          "alt_seen", "has_baro")
# Что плата сообщает о себе: акселерометр, барометр, магнитометр, GPS, гироскоп.
MASK = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 5)


def _tr(srok=False, gps=True, **st):
    """Трекер с заданным состоянием. srok=True — отведённое время вышло."""
    t = offline.load_tracker()
    t.GPS_EXPECTED = gps
    t._startup_t0 = time.monotonic() - (20.0 if srok else 1.0)
    t._sensors_ok_since = None
    with t.state_lock:
        for k in _POLYA:
            t.app_state.pop(k, None)
        t.app_state.update(st)
    return t


def _vse_podnyalos(**extra):
    d = dict(sensor_mask=MASK, gyro=(1, 2, 3), acc=(0, 0, 4096), mag=(20, 5, 7),
             alt_seen=True, rc_link_ts=time.monotonic(), fc_pitch_deg=0.0,
             gps_fix=1, gps_sats=11, gps_lat=50.45, gps_lon=30.52)
    d.update(extra)
    return d


def _kak_slovar(t):
    return {imya: sost for imya, sost, _ in (t._sensor_report() or [])}


def _narisovano(t, kadr=None):
    f = np.full((480, 640, 4), 40, np.uint8) if kadr is None else kadr
    t.draw_sensor_report(f)
    return (f[:, :, :3] != 40).any(axis=2)


print("=== 1. Датчики появляются по мере готовности ===")
bylo = None
for opisanie, st in (
        ("ничего", dict(sensor_mask=MASK)),
        ("гироскоп", dict(sensor_mask=MASK, gyro=(1, 2, 3))),
        ("и акселерометр", dict(sensor_mask=MASK, gyro=(1, 2, 3),
                                acc=(0, 0, 4096))),
        ("и всё остальное", _vse_podnyalos())):
    d = _kak_slovar(_tr(**st))
    gotovo = sorted(k for k, v in d.items() if v.startswith("ok"))
    print("    %-16s ok: %s" % (opisanie, ", ".join(gotovo) or "нет"))
    assert gotovo != bylo, "отчёт не меняется — прогресс не виден"
    bylo = gotovo
assert _kak_slovar(_tr(**_vse_podnyalos()))["gyro"] == "ok"

print("\n=== 2. RC судится по свежести связи, а не по значению по умолчанию ===")
d = _kak_slovar(_tr(**_vse_podnyalos(rc_link_ts=None)))
print("    без связи      :", d["rc"])
assert not d["rc"].startswith("ok"), (
    "RC признан живым по значению по умолчанию — молчащий полётник "
    "отрапортует исправность")
d = _kak_slovar(_tr(**_vse_podnyalos(rc_link_ts=time.monotonic() - 30.0)))
print("    связь 30 с назад:", d["rc"])
assert not d["rc"].startswith("ok"), "устаревший ответ принят за живую связь"

print("\n=== 3. Ответ нулями — это отсутствие GPS, а не исправный GPS ===")
for opisanie, st in (
        ("полные нули", dict(gps_fix=0, gps_sats=0, gps_lat=0.0, gps_lon=0.0)),
        ("нет захвата", dict(gps_fix=0, gps_sats=9, gps_lat=50.4, gps_lon=30.5)),
        ("нет спутников", dict(gps_fix=1, gps_sats=0, gps_lat=50.4, gps_lon=30.5)),
        ("нулевые координаты", dict(gps_fix=1, gps_sats=9, gps_lat=0.0,
                                    gps_lon=0.0)),
        ("три спутника", dict(gps_fix=1, gps_sats=3, gps_lat=50.4,
                              gps_lon=30.5))):
    d = _kak_slovar(_tr(**_vse_podnyalos(**st)))
    print("    %-20s -> %s" % (opisanie, d["gps"]))
    assert not d["gps"].startswith("ok"), (
        "«%s» принято за годный GPS" % opisanie)
assert _kak_slovar(_tr(**_vse_podnyalos()))["gps"].startswith("ok")

print("\n=== 4. Акселерометр в нулях — это не исправный акселерометр ===")
d = _kak_slovar(_tr(**_vse_podnyalos(acc=(0, 0, 0))))
print("    все оси в нуле:", d["acc"])
assert not d["acc"].startswith("ok"), "неоткалиброванный датчик признан живым"

print("\n=== 5. Строка не исчезает НИКОГДА: отсутствие названо вслух ===")
# Прятать строку нельзя: список из четырёх пунктов вместо семи читается как
# «всё хорошо», и пропажа барометра выглядит ровно как его исправность.
ozhidaem = {"rc", "attitude", "gyro", "acc", "baro", "mag", "gps"}
for opisanie, srok, st in (
        ("плата без баро и магнитометра", True,
         dict(_vse_podnyalos(), sensor_mask=(1 << 0) | (1 << 3) | (1 << 5),
              alt_seen=False, mag=None)),
        ("перечня от полётника нет", True,
         {k: v for k, v in _vse_podnyalos().items() if k != "sensor_mask"}),
        ("ничего не поднялось", True, dict(sensor_mask=MASK)),
        ("всё в порядке", False, _vse_podnyalos())):
    d = _kak_slovar(_tr(srok=srok, **st))
    print("    %-30s %s" % (opisanie, sorted(d)))
    assert ozhidaem <= set(d), (
        "пропали строки %s — пилот не узнает, что датчика нет"
        % sorted(ozhidaem - set(d)))

print("\n=== 6. Барометр выключен в Betaflight — не «ok» ===")
# Случай со стенда: барометр выключили в настройках полётника, а отчёт писал
# baro - ok. Полётник отвечает на запрос высоты и без барометра — нулями,
# и признак «высотомер отвечает» поднимался. Слово платы должно быть сильнее
# любых пришедших данных.
t = _tr(srok=True, **dict(_vse_podnyalos(),
                          sensor_mask=MASK & ~(1 << 1),   # баро выключен
                          has_baro=False,
                          alt_seen=True))                 # но ответы идут
d = _kak_slovar(t)
print("    баро выключен, ответы идут:", d["baro"])
assert d["baro"] != "ok", (
    "выключенный барометр показан исправным — ровно тот случай, что нашёлся "
    "на стенде")
assert d["baro"] == "NOT ON BOARD"

print("\n=== 7. Нет на плате, но нужен — это ОТКАЗ, а не тишина ===")
d = _kak_slovar(_tr(srok=True, **dict(
    _vse_podnyalos(), sensor_mask=MASK & ~(1 << 1), alt_seen=False)))
print("    барометра нет на плате:", d["baro"])
assert d["baro"] == "NOT ON BOARD", (
    "отсутствие барометра не названо — а без него нет оценки дальности")

print("\n=== 8. Нет на плате и не нужен — это не отказ ===")
t = _tr(srok=True, gps=False, **dict(
    _vse_podnyalos(), sensor_mask=(1 << 0) | (1 << 1) | (1 << 5), mag=None,
    gps_fix=None, gps_sats=None, gps_lat=None, gps_lon=None))
d = _kak_slovar(t)
print("    магнитометр: %s, GPS: %s" % (d["mag"], d["gps"]))
assert d["mag"] == "none" and d["gps"] == "none", (
    "борт без магнитометра и GPS вечно висел бы с ошибкой по железке, "
    "которой нет по замыслу")
assert t._sensors_ok_since is not None, (
    "необязательная железка не должна мешать отчёту уйти с экрана")

print("\n=== 9. Полётник не прислал перечень — об этом сказано отдельно ===")
d = _kak_slovar(_tr(srok=True, **{k: v for k, v in _vse_podnyalos().items()
                                  if k != "sensor_mask"}))
print("    fc list:", d.get("fc list"))
assert d.get("fc list") == "NO RESPONSE", (
    "без перечня «нет данных» не отличить от «нет датчика», и весь отчёт "
    "становится догадкой — молчать об этом нельзя")

print("\n=== 10. Не ответил в срок — назван поимённо ===")
t = _tr(srok=True, sensor_mask=MASK, gyro=(1, 2, 3),
        rc_link_ts=time.monotonic(), fc_pitch_deg=0.0)
d = _kak_slovar(t)
print("   ", d)
assert d["acc"] == "NO RESPONSE" and d["baro"] == "NO RESPONSE"
assert d["gyro"] == "ok", "исправный датчик записан в отказавшие"
assert _narisovano(t).sum() > 0, "отказ не показан"

print("\n=== 11. Всё поднялось — отчёт уходит; отказ остаётся ===")
t = _tr(**_vse_podnyalos())
assert _narisovano(t).sum() > 0, "отчёт не рисуется вовсе"
t._sensors_ok_since = time.monotonic() - t.STARTUP_OK_SHOW_S - 0.1
assert t._sensor_report() is None, "отчёт висит и загораживает цель"
assert _narisovano(t).sum() == 0

t = _tr(srok=True, sensor_mask=MASK, rc_link_ts=time.monotonic())
t._sensors_ok_since = time.monotonic() - 600.0
assert t._sensor_report() is not None, "отказ сам уехал с экрана"

print("\n=== 12. После арма отчёта нет ===")
t = _tr(armed=True, **_vse_podnyalos())
assert t._sensor_report() is None, "на заходе картинку загораживать нечем"
assert _narisovano(t).sum() == 0

print("\n=== 13. Отчёт целиком в кадре, ниже лупы и без кириллицы ===")
niz_lupy = 6 + 170          # MAG_MARGIN + MAG_SIZE
# Состояния строим ЛЕНИВО: load_tracker отдаёт один и тот же модуль, и
# заготовленные заранее трекеры оказались бы одним объектом — проверка
# схлопнулась бы в последний случай и молча ослабла.
sluchai = (("всё ок", False, _vse_podnyalos()),
           ("всё молчит", True, dict(sensor_mask=MASK)),
           ("ищет спутники", False, _vse_podnyalos(
               gps_fix=0, gps_sats=3, gps_lat=0.0, gps_lon=0.0)))
shirochayshiy = False
for opisanie, srok, st in sluchai:
    t = _tr(srok=srok, **st)
    for imya, sost, _c in t._sensor_report():
        assert all(ord(c) < 128 for c in imya + sost), (
            "кириллица в оверлее рисуется как «?»: %r" % (imya + sost))
        # Самый широкий случай — «NO RESPONSE» по всем строкам; именно его и
        # нельзя обрезать, иначе датчик останется неназванным.
        shirochayshiy = shirochayshiy or sost == "NO RESPONSE"
    est = _narisovano(t)
    stolbcy = np.flatnonzero(est.any(axis=0))
    stroki = np.flatnonzero(est.any(axis=1))
    print("    %-14s x %d..%d, y %d..%d"
          % (opisanie, stolbcy[0], stolbcy[-1], stroki[0], stroki[-1]))
    assert stolbcy[0] >= 2 and stolbcy[-1] <= 637, (
        "отчёт обрезан по краю кадра — датчик останется неназванным")
    assert stroki[0] > niz_lupy, "отчёт налезает на лупу"
    assert stroki[-1] < 480, "отчёт не влезает по высоте"

assert shirochayshiy, "самый широкий случай не проверен — проверка ни о чём"

print("\nOK: отказ виден поимённо, а исправность нельзя подделать умолчанием")
