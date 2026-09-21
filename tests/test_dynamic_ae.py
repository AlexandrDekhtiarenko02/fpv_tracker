"""Экспозиция и баланс белого после старта остаются динамическими
(camera/display commit + стендовая правка после b1fb124/0759d88).

Раньше main() после startup-settle делал AeEnable=False и фиксировал
ExposureTime/AnalogueGain тем, к чему камера пришла за CAM_SETTLE_MAX_S.
Смена освещения (тёмная комната <-> улица) после этого не отрабатывалась
без перезапуска. Первая версия этого коммита включила AeEnable=True, но
оставила AwbEnable=False (ColourGains фиксировались) — стенд показал, что
это тоже неверно: старт в тёмном помещении с тёплым светом даёт ColourGains
под этот свет, а после выноса на улицу AE верно уменьшает выдержку, но
ЦВЕТ остаётся «комнатным» — картинка идёт сине-фиолетовым оттенком.

Текущая архитектура (обе ветки строят ctrl присваиванием ключей, а не
двумя параллельными словарными литералами):
  - AeEnable=True после старта, ЕСЛИ он есть в camera_controls (проверка,
    а не слепая установка); ExposureTime/AnalogueGain в этой ветке НЕ
    фиксируются;
  - AwbEnable=True после старта, ЕСЛИ он есть в camera_controls;
    ColourGains в этой ветке НЕ фиксируются;
  - откат на прежнее статичное поведение для КАЖДОГО control'а отдельно
    (ExposureTime/AnalogueGain фиксируются, если AeEnable недоступен;
    ColourGains фиксируются, если AwbEnable недоступен) — и в откате
    сам недоступный ключ (AeEnable/AwbEnable) НЕ ставится вовсе (ни True,
    ни False): раз его нет в camera_controls, любое его значение
    потенциально неподдерживаемый control;
  - FrameDurationLimits переустанавливается ПОСЛЕ обеих проверок
    (проверяется отдельно и в test_cam_fps.py).

Отдельно (стендовое замечание на b1fb124): метаданные экспозиции для
диагностики (_read_cam_exposure_metadata) обязаны читаться из
request.get_metadata() — request уже передан в camera_callback, который
сам является picam2.pre_callback. picam2.capture_metadata() инициирует
отдельный запрос и ждёт его — вызов такого рода ИЗНУТРИ pre_callback не
рекомендован (риск дедлока/зависания камеры).

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

print("=== 1. AeEnable/AwbEnable проверяются через camera_controls, не "
      "ставятся вслепую ===")
assert '"AeEnable" in _controls_dostupny' in block, (
    "AeEnable ставится без проверки поддержки этой камерой/сборкой "
    "picamera2 — риск падения на неподдерживаемом control")
assert '"AwbEnable" in _controls_dostupny' in block, (
    "AwbEnable ставится без проверки поддержки этой камерой/сборкой "
    "picamera2 — риск падения на неподдерживаемом control")
print("    есть проверки \"AeEnable\"/\"AwbEnable\" in camera_controls")

print("\n=== 2. Ветка с доступным AE: динамическая экспозиция ===")
i_if_ae = block.index("if _ae_dostupen:")
i_else_ae = block.index("else:", i_if_ae)
i_if_awb = block.index("if _awb_dostupen:")
vetka_ae_dinamik = block[i_if_ae:i_else_ae]
assert 'ctrl["AeEnable"] = True' in vetka_ae_dinamik, (
    "ветка с доступным AE не включает AeEnable=True")
assert "ExposureTime" not in vetka_ae_dinamik, (
    "ветка с доступным AE всё ещё фиксирует ExposureTime — экспозиция "
    "не станет динамической")
assert "AnalogueGain" not in vetka_ae_dinamik, (
    "ветка с доступным AE всё ещё фиксирует AnalogueGain — экспозиция "
    "не станет динамической")
print("    AeEnable=True, ExposureTime/AnalogueGain не фиксируются")

print("\n=== 3. Ветка отката AE: настоящий откат, AeEnable не трогается ===")
vetka_ae_otkat = block[i_else_ae:i_if_awb]
assert 'ctrl["AeEnable"]' not in vetka_ae_otkat, (
    "ветка отката AE всё равно ставит ключ AeEnable в словарь ctrl — но "
    "раз его нет в camera_controls этой камеры, ЛЮБОЕ его значение "
    "потенциально неподдерживаемый control; настоящий откат — не "
    "трогать этот control вовсе")
assert 'ctrl["ExposureTime"] = exp' in vetka_ae_otkat, (
    "ветка отката AE не фиксирует ExposureTime — старое поведение должно "
    "быть сохранено ровно для случая, когда AE недоступен")
assert 'ctrl["AnalogueGain"]' in vetka_ae_otkat, (
    "ветка отката AE не фиксирует AnalogueGain")
print("    AeEnable не упоминается (настоящий откат), ExposureTime/"
      "AnalogueGain фиксируются (старое поведение)")

print("\n=== 4. Ветка с доступным AWB: динамический баланс белого ===")
i_elif_awb = block.index("elif colour is not None:", i_if_awb)
vetka_awb_dinamik = block[i_if_awb:i_elif_awb]
assert 'ctrl["AwbEnable"] = True' in vetka_awb_dinamik, (
    "ветка с доступным AWB не включает AwbEnable=True — стендовый бенч "
    "прямо показал, что фиксированный AWB даёт сине-фиолетовый оттенок "
    "после смены освещения")
assert "ColourGains" not in vetka_awb_dinamik, (
    "ветка с доступным AWB всё ещё фиксирует ColourGains — баланс белого "
    "не станет динамическим")
print("    AwbEnable=True, ColourGains в этой ветке не фиксируются")

print("\n=== 5. Ветка отката AWB: настоящий откат, AwbEnable не "
      "трогается ===")
i_fd = block.index('ctrl["FrameDurationLimits"]')
vetka_awb_otkat = block[i_elif_awb:i_fd]
assert 'ctrl["AwbEnable"]' not in vetka_awb_otkat, (
    "ветка отката AWB всё равно ставит ключ AwbEnable — раз его нет в "
    "camera_controls, любое его значение потенциально неподдерживаемое")
assert 'ctrl["ColourGains"] = tuple(colour)' in vetka_awb_otkat, (
    "ветка отката AWB не фиксирует ColourGains — старое поведение должно "
    "быть сохранено ровно для случая, когда AWB недоступен")
print("    AwbEnable не упоминается (настоящий откат), ColourGains "
      "фиксируются (старое поведение)")

print("\n=== 6. FrameDurationLimits переустанавливается после обеих "
      "проверок ===")
assert i_fd > i_elif_awb > i_if_awb > i_else_ae > i_if_ae, (
    "порядок блоков нарушен — FrameDurationLimits должен идти строго "
    "после обеих пар веток (AE, затем AWB)")
print("    ctrl[\"FrameDurationLimits\"] ставится после AE и AWB веток, "
      "общим кодом")

print("\n=== 7. Весь блок обёрнут в try/except: сбой не роняет запуск ===")
assert "try:" in src[max(0, i0 - 200):i0], (
    "блок настройки AE/AWB не обёрнут в try — исключение (например, "
    "camera_controls недоступен) уронит main() целиком")
print("    есть try перед блоком, except Exception: pass после него")

print("\n=== 8. Диагностика в pre_callback читает request.get_metadata(), "
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

print("\nOK: экспозиция и баланс белого динамические при доступных "
      "AeEnable/AwbEnable (с честным откатом на статику по каждому "
      "control'у отдельно, без псевдо-отключения неподдерживаемого "
      "control), FrameDurationLimits переустанавливается после обеих "
      "проверок, диагностика экспозиции не рискует дедлоком в pre_callback")
