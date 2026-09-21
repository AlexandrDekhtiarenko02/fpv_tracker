"""Экспозиция после старта остаётся динамической, баланс белого — нет
(camera/display commit).

Раньше main() после startup-settle делал AeEnable=False и фиксировал
ExposureTime/AnalogueGain тем, к чему камера пришла за CAM_SETTLE_MAX_S.
Смена освещения (тёмная комната <-> улица) после этого не отрабатывалась
без перезапуска. Новая архитектура:
  - AeEnable=True после старта, ЕСЛИ он есть в camera_controls этой
    камеры/сборки picamera2 (проверка, а не слепая установка);
  - ExposureTime/AnalogueGain в этой ветке НЕ фиксируются;
  - откат на прежнее статичное поведение (AeEnable=False + зафиксированные
    ExposureTime/AnalogueGain), если AeEnable недоступен;
  - AWB — по старой логике в ОБЕИХ ветках: AwbEnable=False + сохранённые
    ColourGains (динамический AWB в этом коммите не включается);
  - FrameDurationLimits переустанавливается ПОСЛЕ if/else, в обеих ветках
    одинаково (проверяется отдельно и в test_cam_fps.py).

Проверяется по исходному тексту: блок находится внутри main(), запуск
реальной камеры недоступен в offline-прогоне (см. test_cam_fps.py/
test_camera_settle.py для того же класса проверок).
"""
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

i0 = src.index('_controls_dostupny = getattr(picam2, "camera_controls"')
i_end = src.index("except Exception:\n        pass", i0)
block = src[i0:i_end]

print("=== 1. AeEnable проверяется через camera_controls, не ставится "
      "вслепую ===")
assert '"AeEnable" in _controls_dostupny' in block, (
    "AeEnable ставится без проверки поддержки этой камерой/сборкой "
    "picamera2 — риск падения на неподдерживаемом control")
print("    есть проверка \"AeEnable\" in camera_controls")

print("\n=== 2. Ветка с доступным AE: динамическая экспозиция ===")
i_if = block.index("if _ae_dostupen:")
i_else = block.index("else:", i_if)
vetka_dinamik = block[i_if:i_else]
assert '"AeEnable": True' in vetka_dinamik, (
    "ветка с доступным AE не включает AeEnable=True")
assert "ExposureTime" not in vetka_dinamik, (
    "ветка с доступным AE всё ещё фиксирует ExposureTime — экспозиция "
    "не станет динамической")
assert "AnalogueGain" not in vetka_dinamik, (
    "ветка с доступным AE всё ещё фиксирует AnalogueGain — экспозиция "
    "не станет динамической")
assert '"AwbEnable": False' in vetka_dinamik, (
    "ветка с доступным AE не замораживает AWB — по спеку динамический "
    "AWB в этом коммите не включается")
print("    AeEnable=True, ExposureTime/AnalogueGain не фиксируются, "
      "AwbEnable=False")

print("\n=== 3. Ветка отката: прежнее статичное поведение сохранено ===")
i_next_def_or_end = block.index("\n        if colour is not None:", i_else)
vetka_otkat = block[i_else:i_next_def_or_end]
assert '"AeEnable": False' in vetka_otkat, (
    "ветка отката не выключает AE — но AeEnable недоступен в "
    "camera_controls, ставить его нельзя вовсе")
assert '"ExposureTime": exp' in vetka_otkat, (
    "ветка отката не фиксирует ExposureTime — старое поведение должно "
    "быть сохранено ровно для случая, когда AE недоступен")
assert '"AnalogueGain":' in vetka_otkat, (
    "ветка отката не фиксирует AnalogueGain")
assert '"AwbEnable": False' in vetka_otkat, (
    "ветка отката не замораживает AWB")
print("    AeEnable=False, ExposureTime/AnalogueGain фиксируются "
      "(старое поведение), AwbEnable=False")

print("\n=== 4. ColourGains применяется в обеих ветках (после if/else) ===")
i_colour = block.index("if colour is not None:")
assert i_colour > i_else, (
    "применение ColourGains найдено внутри if/else вместо общего кода "
    "после него — рискует примениться только в одной из веток")
assert 'ctrl["ColourGains"] = tuple(colour)' in block[i_colour:i_colour + 100]
print("    ColourGains применяется один раз, после обеих веток")

print("\n=== 5. FrameDurationLimits переустанавливается после обеих "
      "веток ===")
i_fd = block.index('ctrl["FrameDurationLimits"]')
assert i_fd > i_colour > i_else, (
    "FrameDurationLimits ставится раньше ColourGains/веток if-else — "
    "порядок в блоке нарушен")
print("    ctrl[\"FrameDurationLimits\"] ставится после ColourGains, "
      "общим кодом для обеих веток")

print("\n=== 6. Весь блок обёрнут в try/except: сбой не роняет запуск ===")
assert "try:" in src[max(0, i0 - 200):i0], (
    "блок настройки AE/AWB не обёрнут в try — исключение (например, "
    "camera_controls недоступен) уронит main() целиком")
print("    есть try перед блоком, except Exception: pass после него")

print("\nOK: экспозиция динамическая при доступном AeEnable (с честным "
      "откатом на статику, если недоступен), AWB заморожен в обеих "
      "ветках, FrameDurationLimits переустанавливается после обеих")
