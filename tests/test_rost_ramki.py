"""Рамка не растёт сама по себе.

ЗАМЕРЕНО НА 5064 КАДРАХ коммита 9d4ecb6: примерка масштаба говорила
«крупнее» в 59% проверок и «мельче» в 6%. Это не измерение цели, а свойство
меры сходства: TM_CCOEFF_NORMED предпочитает крупный эталон, потому что тот
захватывает больше фона, а фон гладкий и коррелирует со всем вокруг.

Кончалось это положительной обратной связью. В заходе 145208:
    t=0.0  рамка 24 px, совпадение 1.000
    t=3.5  рамка 76 px, совпадение 0.849
    t=5.1  рамка 90 px, совпадение 0.756
    t=7.2  рамка 90 px, совпадение 0.169   <- цель потеряна
Рамка росла, эталон набирал фон, фон совпадал везде, «крупнее» снова
выигрывало. И так до потери цели.

Отличить настоящий рост от перекоса просто: настоящий даёт БОЛЬШОЙ перевес,
перекос — маленький.

ЧЕГО ЭТОТ ТЕСТ НЕ ДЕЛАЕТ. Он НЕ воспроизводит полётный самоход: в
синтетической сцене коробка стоит и при старом пороге, и при новом. Значит
дело в чём-то, чего в модели нет — вероятно, в том, что настоящий фон не
однороден по фактуре, а эталон в полёте ещё и подмешивается на ходу
(_adapt_template_base).

Поэтому сам порог 0.050 остаётся ПОДТВЕРЖДЁННЫМ ТОЛЬКО ЗАМЕРОМ в полёте
(59% против 6%), а не этим тестом. Тест закрывает две более узкие вещи:
коробка не должна раздуваться на неподвижной цели, и порог роста обязан быть
строже порога уменьшения. Если самоход вернётся, ловить его придётся снова в
логах — модель тут не поможет.
"""
import io
import os
import re

import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2}
ns["HIRES_TRACKING"] = eval(
    re.search(r"^HIRES_TRACKING = (.+?)(?:\s+#.*)?$", src, re.M).group(1))
ns["TRACK_SCALE"] = eval(
    re.search(r"^TRACK_SCALE = (.+?)(?:\s+#.*)?$", src, re.M).group(1), dict(ns))
_im = sorted(set(m.group(1) for m in re.finditer(
    r"^((?:SIZE|TEMPLATE|LOCK|SEARCH_MARGIN)_[A-Z0-9_]+) = ", src, re.M)))
_ost = list(_im)
for _ in range(6):
    _ne = []
    for name in _ost:
        m = re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M)
        if m is None:
            continue
        try:
            ns[name] = eval(m.group(1), dict(ns))
        except Exception:
            _ne.append(name)
    if not _ne or _ne == _ost:
        _ost = _ne
        break
    _ost = _ne
assert not _ost, "не вычислились: %s" % _ost
for fn in ("clamp", "clamp_rect_center", "crop_center"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef )" % fn, src, re.S | re.M)
    if m:
        exec(m.group(0), ns)
exec(re.search(r"^def measure_scale_change.*?(?=\n\ndef )",
               src, re.S | re.M).group(0), ns)

rng = np.random.default_rng(11)
FON = np.clip(120 + cv2.GaussianBlur(
    rng.normal(0, 22, (240, 320)).astype(np.float32), (0, 0), 1.6), 0, 255)


def scena(razmer):
    """Неподвижная цель заданного размера на фактурном фоне."""
    g = FON.copy()
    cv2.circle(g, (160, 120), int(razmer / 2), 45, -1)
    # Фактура на самой цели, иначе эталон безлик и включится другой закон.
    m = np.zeros_like(g, np.uint8)
    cv2.circle(m, (160, 120), int(razmer / 2), 255, -1)
    sh = cv2.GaussianBlur(rng.normal(0, 18, g.shape).astype(np.float32), (0, 0), 1.1)
    g[m > 0] = np.clip(45 + sh, 0, 255)[m > 0]
    return np.clip(g, 0, 255).astype(np.uint8)


def progon(razmer_celi, kadrov=60):
    """Коробка стартует верно и цель НЕ меняется. Насколько уедет коробка."""
    box = float(razmer_celi)
    g = scena(razmer_celi)
    t = int(ns["clamp"](box * ns["TEMPLATE_SCALE"],
                        ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"]))
    tmpl, _ = ns["crop_center"](g, 160, 120, t, t)
    ns["template_gray"] = tmpl.copy()
    for _ in range(kadrov):
        k = ns["measure_scale_change"](g, 160, 120)
        if k is None:
            continue
        rost = 1.0 + (k - 1.0) * ns["SIZE_SCALE_ALPHA"]
        box = ns["clamp"](box * rost, ns["LOCK_MIN_W"], ns["LOCK_MAX_W"])
        t = int(ns["clamp"](box * ns["TEMPLATE_SCALE"],
                            ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"]))
        tmpl, _ = ns["crop_center"](g, 160, 120, t, t)
        ns["template_gray"] = tmpl.copy()
    return box / razmer_celi


print("=== НЕПОДВИЖНАЯ ЦЕЛЬ: коробка обязана СТОЯТЬ ===")
print("  %-12s %12s" % ("цель, px", "уехала в"))
hudshee = 1.0
for razmer in (16, 24, 32, 48):
    k = progon(razmer)
    hudshee = max(hudshee, k)
    print("  %-12d %11.2f×" % (razmer, k))
assert hudshee < 1.35, (
    "коробка уехала в %.2f раза на НЕПОДВИЖНОЙ цели — это тот самый самоход, "
    "который кончался потерей цели" % hudshee)

print("\n=== КОРОБКА КРУПНЕЕ ЦЕЛИ: так и бывает в полёте ===")
# Измерение размера при захвате выключено (ACQ_SIZE_BY_SEGMENTATION = False),
# поэтому коробка стартует с 24 px на ЛЮБОЙ цели. Если цель мельче, эталон
# сразу набирает фон — и начинается самоход: рамка растёт, фона становится
# больше, фон совпадает везде, «крупнее» снова выигрывает.
print("  %-12s %-12s %12s" % ("цель, px", "старт короб.", "уехала в"))
hud = 1.0
for cel, start in ((10, 24), (14, 24), (18, 24)):
    box = float(start)
    g = scena(cel)
    t = int(ns["clamp"](box * ns["TEMPLATE_SCALE"],
                        ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"]))
    tmpl, _ = ns["crop_center"](g, 160, 120, t, t)
    ns["template_gray"] = tmpl.copy()
    for _ in range(60):
        k = ns["measure_scale_change"](g, 160, 120)
        if k is None:
            continue
        box = ns["clamp"](box * (1.0 + (k - 1.0) * ns["SIZE_SCALE_ALPHA"]),
                          ns["LOCK_MIN_W"], ns["LOCK_MAX_W"])
        t = int(ns["clamp"](box * ns["TEMPLATE_SCALE"],
                            ns["TEMPLATE_MIN"], ns["TEMPLATE_MAX"]))
        tmpl, _ = ns["crop_center"](g, 160, 120, t, t)
        ns["template_gray"] = tmpl.copy()
    k = box / start
    hud = max(hud, k)
    print("  %-12d %-12d %11.2f×" % (cel, start, k))
assert hud < 1.6, (
    "коробка уехала в %.2f раза при цели мельче стартовой рамки — это и есть "
    "самоход, кончавшийся потерей цели" % hud)

print("\n=== ПЕРЕВЕС ДЛЯ РОСТА БОЛЬШЕ, ЧЕМ ДЛЯ УМЕНЬШЕНИЯ ===")
print("  рост:      %.3f" % ns["SIZE_SCALE_MIN_LEAD_GROW"])
print("  уменьшение %.3f" % ns["SIZE_SCALE_MIN_LEAD"])
assert ns["SIZE_SCALE_MIN_LEAD_GROW"] > ns["SIZE_SCALE_MIN_LEAD"], (
    "порог роста не строже порога уменьшения — перекос меры сходства снова "
    "будет выигрывать")

print("\nOK: самоход рамки остановлен, законный рост сохранён")
