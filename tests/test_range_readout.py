"""Вывод дальности у рамки: рисуется и честно называет причину, когда нельзя.

Две вещи, на которых легко обжечься и которые проверяются здесь:
  * кириллица в шрифтах OpenCV превращается в знаки вопроса;
  * None у дальности означает «нечем измерить», а не «ноль метров», и на
    экране это должно быть видно как ПРИЧИНА, а не как прочерк.
"""
import io, os, re, threading, time
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "time": time, "state_lock": threading.RLock()}
for name in ("COLOR_GREEN", "COLOR_YELLOW", "COLOR_WHITE", "COLOR_BLACK",
             "RANGE_MIN_DEPRESSION_DEG", "RANGE_READOUT_ENABLED"):
    ns[name] = eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1))
ns["clamp"] = eval(
    "lambda v, lo, hi: lo if v < lo else (hi if v > hi else v)")
for fname in ("_range_readout_lines", "draw_range_readout"):
    body = re.search(r"^def %s\(.*?(?=\n\ndef )" % fname, src, re.S | re.M).group(0)
    exec(body, ns)

def setup(ctl, app):
    ns["_ctl_dbg"] = ctl
    ns["app_state"] = app

FRESH = {"alt_cm": 5000, "alt_ts": time.monotonic(), "fc_pitch_deg": -10.0,
         "alt_sigma_cm": 10.1}

print("=== 1. Дальность есть — показываем метры ===")
setup({"range_m": 34.2, "tau_s": 2.4, "size_px": 21.0, "growth": 0.013}, FRESH)
lines = ns["_range_readout_lines"]()
for t, _ in lines:
    print("   ", t)
assert lines[0][0] == "R 34m", lines[0][0]
assert lines[1][0] == "T 2.4s", lines[1][0]

print("\n=== 1б. Угол визирования виден всегда, и знак читается ===")
setup({"range_m": None, "tau_s": None, "size_px": 21.0, "growth": 0.0,
       "depression_deg": 14.0, "alt_min_m": 0.5}, FRESH)
got = [t for t, _ in ns["_range_readout_lines"]()]
print("   ", " | ".join(got))
assert any(t.startswith("dep +14") for t in got), got
# Причина не имеет права врать: угол 14 при пороге 4 не мал.
assert not any("ang 14" in t for t in got), "строка причины назвала неверную причину: %s" % got
setup({"range_m": None, "tau_s": None, "size_px": 21.0, "growth": 0.0,
       "depression_deg": -14.0, "alt_min_m": 0.5}, FRESH)
got = [t for t, _ in ns["_range_readout_lines"]()]
print("   ", " | ".join(got))
assert any(t.startswith("dep -14") and t.endswith("UP") for t in got), got
print("    нос вниз -> dep положительный; нос вверх -> отрицательный и пометка UP")

print("\n=== 2. Причина, а не прочерк ===")
cases = [
    ("нет высоты",        {"alt_cm": None, "alt_ts": 0.0, "fc_pitch_deg": -10.0}, {}, "no alt"),
    ("нет тангажа",       {"alt_cm": 5000, "alt_ts": time.monotonic(), "fc_pitch_deg": None}, {}, "no att"),
    ("высота протухла",   {"alt_cm": 5000, "alt_ts": time.monotonic() - 5, "fc_pitch_deg": -10.0}, {}, "alt old"),
    ("угол мал",          FRESH, {"depression_deg": 1.4}, "ang 1.4"),
    ("высота в шуме",     {"alt_cm": 15, "alt_ts": time.monotonic(), "fc_pitch_deg": -10.0,
                           "alt_sigma_cm": 10.1}, {"alt_min_m": 0.505}, "alt 15<50cm"),
]
for name, app, extra, expect in cases:
    ctl = {"range_m": None, "tau_s": None, "size_px": 21.0, "growth": 0.0}
    ctl.update(extra)
    setup(ctl, app)
    got = ns["_range_readout_lines"]()[0][0]
    print("   %-16s -> %s" % (name, got))
    assert expect in got, "ожидал %r, получил %r" % (expect, got)

print("\n=== 3. Не сближаемся — так и пишем, а не 'T 0.0s' ===")
setup({"range_m": None, "tau_s": None, "size_px": 21.0, "growth": 0.0}, FRESH)
t = ns["_range_readout_lines"]()[1][0]
print("   ", t)
assert "no close" in t and "0.0" not in t

print("\n=== 4. Текст реально попадает в кадр ===")
setup({"range_m": 34.2, "tau_s": 2.4, "size_px": 21.0, "growth": 0.013}, FRESH)
frame = np.zeros((480, 640, 4), np.uint8)
ns["draw_range_readout"](frame, (300, 200, 340, 240))
painted = int((frame[..., 1] > 0).sum())
print("    закрашено пикселей:", painted)
assert painted > 100, "текст не нарисовался"

print("\n=== 5. У правого края уходит влево, а не за кадр ===")
frame2 = np.zeros((480, 640, 4), np.uint8)
ns["draw_range_readout"](frame2, (600, 200, 636, 240))
cols = np.where(frame2[..., 1].any(axis=0))[0]
print("    текст занял столбцы %d..%d при ширине кадра 640" % (cols[0], cols[-1]))
assert cols[-1] < 640 and cols[0] < 600, "вылез за кадр или залез на рамку"

print("\n=== 6. Только латиница: кириллица стала бы знаками вопроса ===")
for text, _ in ns["_range_readout_lines"]():
    assert all(ord(ch) < 128 for ch in text), "нелатинские знаки в %r" % text
print("    все подписи латиницей")

print("\n=== 7. Планка годности высоты растёт вместе с шумом барометра ===")
# ЗАМЕРЕНО на столе: сигма барометра 10.1 см, показания от -2 до 40 см.
# Прежний порог 30 см — три сигмы, шум его переходил, и на экране появлялись
# уверенные «3.6 м», собранные целиком из дрожания датчика.
sigmas = eval(re.search(r"^RANGE_ALT_SIGMAS = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
floor = eval(re.search(r"^RANGE_MIN_ALT_FLOOR_M = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
for sigma_cm, alt_cm, must_show in ((10.1, 15, False), (10.1, 40, False),
                                    (10.1, 300, True), (2.0, 80, True)):
    need = max(floor, sigmas * sigma_cm / 100.0)
    ok = (alt_cm / 100.0) > need
    print("    шум %.1f см, высота %3d см -> планка %.0f см, дальность %s"
          % (sigma_cm, alt_cm, need * 100, "считается" if ok else "не считается"))
    assert ok == must_show, "высота %d см при шуме %.1f" % (alt_cm, sigma_cm)
print("    на столе (15-40 см при шуме 10 см) дальность не считается — верно")

print("\nOK: дальность выводится, причина называется, за кадр не вылезает")
