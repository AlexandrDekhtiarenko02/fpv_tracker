"""Дорогой автопоиск пятна на борту выключен.

Замер 09.10: trial slot 0 в окне +80 px стоит ~39 мс и роняет
каждый пятый кадр. Дешёвая проверка пятна этим флагом не управляется.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
assert t.BLOB_ROLL_AUTOSNAP_ENABLED is False
assert t.BLOB_VERIFY_ENABLED is True
print("OK: автопоиск пятна выключен, дешёвая проверка на месте")
