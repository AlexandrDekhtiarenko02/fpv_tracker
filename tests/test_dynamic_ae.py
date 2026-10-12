"""Экспозиция и баланс белого ЗАМОРАЖИВАЮТСЯ после startup-settle
(пересмотрено оператором после реальных стендовых логов ab76eda:
динамика AE во время полёта давала pitch<->ExposureTime корреляцию
0.867, скачки выдержки 652 -> 4612 мкс за секунду наклона, реальные
CAM_TOP_SATURATED и ошибочные CAM_JUMP -> VISUAL_UNSTABLE). Динамическое
AE больше не пробуем — прямое требование оператора: "вернуть
статическую экспозицию, если не можешь починить динамическую".

Что нового относительно старой статики (до "оба динамические"):
фиксация СТРОГО ПОСЛЕ реального settle (см. блок с _settle_s выше),
а не первым значением, к которому камера пришла за первые 0.5с — без
этого старая версия могла заморозить выдержку "для тёмной" на светлой
сцене.

Диагностика ExposureTime/AnalogueGain/ColourGains в pre_callback
по-прежнему обязана читать request.get_metadata() (не
picam2.capture_metadata()) — риск дедлока изнутри pre_callback остаётся
независимо от того, статические AE/AWB или динамические.
"""
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

i0 = src.index('_controls_dostupny = getattr(picam2, "camera_controls"')
i_end = src.index("except Exception:\n        pass", i0)
block = src[i0:i_end]

print("=== 1. AeEnable/AwbEnable проверяются через camera_controls (не "
      "ставятся вслепую) — это касается ЛЮБОЙ политики, статической или "
      "динамической ===")
assert '"AeEnable" in _controls_dostupny' in block
assert '"AwbEnable" in _controls_dostupny' in block
print("    есть проверки \"AeEnable\"/\"AwbEnable\" in camera_controls")

print("\n=== 2. Если AeEnable поддерживается — он ставится в False "
      "(статическая экспозиция) ===")
assert 'ctrl["AeEnable"] = False' in block, (
    "AeEnable не ставится в False — динамика AE запрещена (см. докстроку "
    "файла), возвращаться к True в этой правке не должно")
assert 'ctrl["AeEnable"] = True' not in block, (
    "остался ctrl['AeEnable'] = True — динамика AE была явно отменена")

print("\n=== 3. ExposureTime и AnalogueGain ФИКСИРУЮТСЯ ИЗМЕРЕННЫМИ "
      "значениями (не первым значением с ходу — оператор явно указал "
      "читать после settle) ===")
assert 'ctrl["ExposureTime"]' in block, (
    "ExposureTime не задаётся в ctrl — экспозиция не будет "
    "зафиксирована, вернётся динамика по умолчанию")
assert 'ctrl["AnalogueGain"]' in block, (
    "AnalogueGain не задаётся — AE не будет полноценно зафиксирован")
# Значения должны браться из измеренного snapshot после settle.
assert "exp_measured" in block, (
    "ExposureTime не читается из snapshot после settle — риск замораживания "
    "'вслепую' стартового значения")
assert "gain_measured" in block, (
    "AnalogueGain не читается из snapshot после settle")
print("    ExposureTime/AnalogueGain снимаются из md после settle и "
      "устанавливаются явными значениями")

print("\n=== 4. Если AwbEnable поддерживается — он тоже в False "
      "(статический баланс белого) ===")
assert 'ctrl["AwbEnable"] = False' in block, (
    "AwbEnable не ставится в False — статический AWB тоже часть "
    "требования оператора, вернуть к True в этой правке нельзя")
assert 'ctrl["AwbEnable"] = True' not in block

print("\n=== 5. ColourGains фиксируются измеренными значениями (снимок "
      "ПОСЛЕ settle) ===")
assert 'ctrl["ColourGains"]' in block
assert "colour_measured" in block, (
    "ColourGains не читаются из snapshot после settle")
print("    ColourGains снимаются из md после settle")

print("\n=== 6. FrameDurationLimits переустанавливается ПОСЛЕ AE/AWB "
      "— порядок сохранён ===")
i_fd = block.index('ctrl["FrameDurationLimits"]')
i_ae_off = block.index('ctrl["AeEnable"] = False')
i_awb_off = block.index('ctrl["AwbEnable"] = False')
assert i_fd > i_ae_off, ("FrameDurationLimits должен идти после AE")
assert i_fd > i_awb_off, ("FrameDurationLimits должен идти после AWB")
print("    порядок: AE -> AWB -> FrameDurationLimits")

print("\n=== 7. Весь блок обёрнут в try/except: сбой не роняет запуск ===")
assert "try:" in src[max(0, i0 - 400):i0], (
    "блок настройки AE/AWB не обёрнут в try")
print("    есть try перед блоком, except Exception: pass после него")

print("\n=== 8. Диагностика в pre_callback читает request.get_metadata(), "
      "не picam2.capture_metadata() (риск дедлока — не зависит от "
      "AE/AWB политики) ===")
i_fn = src.index("def _read_cam_exposure_metadata(request):")
i_fn_end = src.index("\ndef ", i_fn + 1)
fn_body = src[i_fn:i_fn_end]
assert "request.get_metadata()" in fn_body
assert "capture_metadata" not in fn_body
print("    _read_cam_exposure_metadata читает request.get_metadata()")

i_cb = src.index("def camera_callback(request):")
i_cb_end = src.index("\ndef ", i_cb + 1)
cb_body = src[i_cb:i_cb_end]
assert "_read_cam_exposure_metadata(request)" in cb_body
print("    camera_callback передаёт СВОЙ request в диагностическую функцию")

print("\nOK: AE/AWB СТАТИЧЕСКИЕ после startup-settle (требование "
      "оператора после логов ab76eda), измеренные snapshot-значения "
      "применяются к ExposureTime/AnalogueGain/ColourGains, "
      "FrameDurationLimits восстанавливается после, диагностика "
      "экспозиции не рискует дедлоком в pre_callback")
