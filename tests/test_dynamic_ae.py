"""Экспозиция после старта остаётся динамической, баланс белого — нет
(camera/display commit).

Раньше main() после startup-settle делал AeEnable=False и фиксировал
ExposureTime/AnalogueGain тем, к чему камера пришла за CAM_SETTLE_MAX_S.
Смена освещения (тёмная комната <-> улица) после этого не отрабатывалась
без перезапуска. Новая архитектура:
  - AeEnable=True после старта, ЕСЛИ он есть в camera_controls этой
    камеры/сборки picamera2 (проверка, а не слепая установка);
  - ExposureTime/AnalogueGain в этой ветке НЕ фиксируются;
  - откат на прежнее статичное поведение (зафиксированные ExposureTime/
    AnalogueGain), если AeEnable недоступен — и ключ "AeEnable" в этой
    ветке НЕ ставится вовсе (ни True, ни False): раз его нет в
    camera_controls, любое его значение потенциально неподдерживаемый
    control, настоящий откат — просто не трогать его;
  - AWB — по старой логике в ОБЕИХ ветках: AwbEnable=False + сохранённые
    ColourGains (динамический AWB в этом коммите не включается);
  - FrameDurationLimits переустанавливается ПОСЛЕ if/else, в обеих ветках
    одинаково (проверяется отдельно и в test_cam_fps.py).

Отдельно (после стендового замечания на b1fb124): метаданные экспозиции
для диагностики (_read_cam_exposure_metadata) обязаны читаться из
request.get_metadata() — request уже передан в camera_callback, который
сам является picam2.pre_callback. picam2.capture_metadata() инициирует
отдельный запрос и ждёт его — вызов такого рода ИЗНУТРИ pre_callback не
рекомендован (риск дедлока/зависания камеры), поэтому этот путь отдельно
проверяется тестом на отсутствие capture_metadata() внутри диагностической
функции.

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

print("\n=== 3. Ветка отката: настоящий откат, AeEnable не трогается ===")
i_next_def_or_end = block.index("\n        if colour is not None:", i_else)
vetka_otkat = block[i_else:i_next_def_or_end]
assert '"AeEnable":' not in vetka_otkat, (
    "ветка отката всё равно ставит ключ AeEnable (True или False) в "
    "словарь ctrl — но раз его нет в camera_controls этой камеры, ЛЮБОЕ "
    "его значение потенциально неподдерживаемый control; настоящий "
    "откат — не трогать этот control вовсе")
assert '"ExposureTime": exp' in vetka_otkat, (
    "ветка отката не фиксирует ExposureTime — старое поведение должно "
    "быть сохранено ровно для случая, когда AE недоступен")
assert '"AnalogueGain":' in vetka_otkat, (
    "ветка отката не фиксирует AnalogueGain")
assert '"AwbEnable": False' in vetka_otkat, (
    "ветка отката не замораживает AWB")
print("    AeEnable не упоминается (настоящий откат), ExposureTime/"
      "AnalogueGain фиксируются (старое поведение), AwbEnable=False")

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

print("\n=== 7. Диагностика в pre_callback читает request.get_metadata(), "
      "не picam2.capture_metadata() ===")
# camera_callback САМ является picam2.pre_callback (назначается в main()) —
# то есть уже выполняется внутри camera event loop. capture_metadata()
# инициирует ОТДЕЛЬНЫЙ запрос к камере и ждёт его — такой вызов изнутри
# pre_callback официально не рекомендован (риск дедлока/зависания камеры).
# У request, уже переданного в callback, метаданные уже готовы — читаем их
# напрямую, без нового запроса и без нового потока.
i_fn = src.index("def _read_cam_exposure_metadata(request):")
i_fn_end = src.index("\ndef ", i_fn + 1)
fn_body = src[i_fn:i_fn_end]
assert "request.get_metadata()" in fn_body, (
    "_read_cam_exposure_metadata не читает request.get_metadata() — "
    "источник метаданных не тот, что нужно")
assert "capture_metadata" not in fn_body, (
    "_read_cam_exposure_metadata всё ещё вызывает picam2.capture_metadata() "
    "— опасный вызов capture-операции изнутри pre_callback, риск дедлока")
print("    _read_cam_exposure_metadata читает request.get_metadata(), "
      "без отдельного capture-запроса к камере")

i_cb = src.index("def camera_callback(request):")
i_cb_end = src.index("\ndef ", i_cb + 1)
cb_body = src[i_cb:i_cb_end]
assert "_read_cam_exposure_metadata(request)" in cb_body, (
    "camera_callback не передаёт свой request в "
    "_read_cam_exposure_metadata — без него функции неоткуда взять "
    "метаданные текущего кадра")
print("    camera_callback передаёт СВОЙ request в диагностическую функцию")

print("\nOK: экспозиция динамическая при доступном AeEnable (с честным "
      "откатом на статику, если недоступен, без псевдо-отключения "
      "неподдерживаемого control), AWB заморожен в обеих ветках, "
      "FrameDurationLimits переустанавливается после обеих, диагностика "
      "экспозиции не рискует дедлоком в pre_callback")
