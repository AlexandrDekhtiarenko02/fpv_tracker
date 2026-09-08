"""Длительность разгона задана в секундах, а не в кадрах.

Счётчики фаз идут по кадрам, поэтому числа кадров, вписанные руками, молча
привязывают манёвр к частоте камеры. Так уже вышло: константы стояли под
30 к/с, камеру перевели на 24 — и тот же разгон стал на четверть длиннее,
причём заметить это можно только секундомером.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

znach = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        imya = getattr(node.targets[0], "id", None)
        if imya:
            try:
                znach[imya] = ast.literal_eval(node.value)
            except Exception:
                znach.setdefault(imya, None)

for imya in ("LAUNCH_RAMP_UP_S", "LAUNCH_HOLD_S", "LAUNCH_RAMP_DOWN_S"):
    assert imya in znach, "нет %s: длительность снова задана в кадрах" % imya

for imya in ("LAUNCH_RAMP_UP_FRAMES", "LAUNCH_HOLD_FRAMES",
             "LAUNCH_RAMP_DOWN_FRAMES"):
    assert znach.get(imya) is None, (
        "%s задано числом — при смене CAM_FPS манёвр сменит длину" % imya)

fps = znach["CAM_FPS"]
sek = (znach["LAUNCH_RAMP_UP_S"], znach["LAUNCH_HOLD_S"],
       znach["LAUNCH_RAMP_DOWN_S"])
print("CAM_FPS=%g, фазы разгона: %.2f + %.2f + %.2f = %.2f с"
      % ((fps,) + sek + (sum(sek),)))
assert 2.0 <= sum(sek) <= 6.0, "разгон вышел за разумные пределы"

# Пересчёт обязан повториться ПОСЛЕ local_settings: борт может переопределить
# и CAM_FPS, и длительности, а кадры, взятые до этого, останутся от прежних.
posle = src.split("import local_settings")[-1]
for imya in ("LAUNCH_RAMP_UP_FRAMES", "LAUNCH_HOLD_FRAMES",
             "LAUNCH_RAMP_DOWN_FRAMES"):
    assert imya in posle, (
        "%s не пересчитывается после local_settings" % imya)
print("длительности в секундах, пересчёт после настроек борта — на месте")
