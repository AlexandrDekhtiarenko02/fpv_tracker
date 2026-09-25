"""CAMERA JUMP SHADOW — диагностика-only детектор скачков экспозиции/
яркости/пересвета (разбор 24 заходов 25.09.2026, ревью после Auto
Template Refresh).

ПОВОД. pitch<->ExposureTime корреляция +0.867, pitch<->mean_gray +0.824.
При pitch 40-50° выдержка вырастает ~в 5 раз против pitch<10° (604 ->
3147 мкс), mean_gray 115 -> 177. Чистые эпизоды (заход #16: 1691 -> 2618
мкс и +36 mean_gray практически за один 1 Гц диагностический тик) прямо
показывают "накренился -> картинка резко побелела" — то, что оператор
видит глазами. AeEnable=True/AwbEnable=True (динамическая экспозиция/
баланс белого) — намеренный, уже проверенный выбор (см. test_dynamic_
ae.py, стенд показал, что статика хуже), эта диагностика его НЕ трогает
и НЕ ограничивает — только измеряет.

ЗАЧЕМ ДИАГНОСТИКА, А НЕ СРАЗУ LIVE. Тот же принцип, что провёл Tracking
Shadow -> Auto Template Refresh: сначала измерить на стенде, какие
пороги/связь со сбоями реальны, потом уже решать про ограничение AE или
про снижение доверия трекингу. Здесь проверяется именно диагностический
слой: чистая функция сравнения соседних 1 Гц замеров, изоляция от
camera_callback (try/except, свой словарь, никаких live-записей).

Реальная камера недоступна в offline-прогоне — интеграция в
camera_callback проверяется по исходному тексту (тот же подход, что
test_dynamic_ae.py), сама арифметика скачка — прямым вызовом чистой
функции.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Нет предыдущего замера -> все дельты None, jump=False ===")
r = t._camera_jump_check(2000.0, None, 130.0, None, 120.0)
assert r["exp_ratio"] is None and r["gray_delta"] is None
assert r["jump"] is False
print("    exp_ratio=None gray_delta=None jump=False")

print("\n=== 2. Маленькое изменение -> jump=False ===")
r = t._camera_jump_check(2100.0, 2000.0, 132.0, 130.0, 120.0)
assert abs(r["exp_ratio"] - 1.05) < 1e-6
assert abs(r["gray_delta"] - 2.0) < 1e-6
assert r["top_saturated"] is False
assert r["jump"] is False, "5%% рост экспозиции и +2 gray не должны считаться скачком"
print("    exp_ratio=1.05 gray_delta=2.0 -> jump=False")

print("\n=== 3. Скачок экспозиции ВВЕРХ (пример стенда: 604 -> 3147, "
      "~5.2x) -> jump=True по exp_ratio ===")
r = t._camera_jump_check(3147.0, 604.0, 140.0, 138.0, 120.0)
assert r["exp_ratio"] > t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert r["jump"] is True
print("    exp_ratio=%.2f (>%.1f порог) -> jump=True"
      % (r["exp_ratio"], t.CAM_JUMP_EXP_RATIO_THRESHOLD))

print("\n=== 4. Скачок экспозиции ВНИЗ (резкое падение, не только рост) "
      "-> jump=True ===")
r = t._camera_jump_check(600.0, 3000.0, 130.0, 132.0, 120.0)
assert r["exp_ratio"] < 1.0 / t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert r["jump"] is True
print("    exp_ratio=%.3f (резкое падение) -> jump=True тоже" % r["exp_ratio"])

print("\n=== 5. Скачок mean_gray САМ ПО СЕБЕ (пример стенда: +36) — "
      "jump=True даже при стабильной экспозиции ===")
r = t._camera_jump_check(2000.0, 1980.0, 166.0, 130.0, 120.0)
assert r["exp_ratio"] < t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert abs(r["gray_delta"] - 36.0) < 1e-6
assert r["jump"] is True, "скачок mean_gray на 36 обязан засчитаться сам по себе"
print("    exp стабильна, gray_delta=36.0 (>%.1f порог) -> jump=True"
      % t.CAM_JUMP_GRAY_DELTA_THRESHOLD)

print("\n=== 6. Пересвет верхней строки САМ ПО СЕБЕ — jump=True даже при "
      "стабильных exp/gray ===")
r = t._camera_jump_check(2000.0, 1990.0, 130.0, 129.0, 252.0)
assert r["exp_ratio"] < t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert abs(r["gray_delta"]) < t.CAM_JUMP_GRAY_DELTA_THRESHOLD
assert r["top_saturated"] is True
assert r["jump"] is True, "top_row_mean=252 (>=250 порог) обязан засчитаться сам по себе"
print("    top_row_mean=252.0 (>=%.0f порог) -> top_saturated=True, jump=True"
      % t.CAM_JUMP_TOPROW_SATURATED_THRESHOLD)

print("\n=== 7. Недостаточно данных (metadata недоступна) -> None, не False ===")
r = t._camera_jump_check(None, 2000.0, None, 130.0, None)
assert r["exp_ratio"] is None and r["gray_delta"] is None
assert r["top_saturated"] is False   # top_row_mean=None -> честно "не пересвечено", не жди
assert r["jump"] is False
print("    exp_us=None/mean_gray=None -> дельты None, jump=False (не путаем "
      "\"нет данных\" с \"скачка не было\", кроме top_saturated, у которого "
      "нет отдельного None-состояния по своей природе — порог bool)")

print("\n=== 8. По исходному тексту: обе точки вызова в camera_callback "
      "(idle и tracked ветки) гейтятся CAM_JUMP_SHADOW_ENABLED и "
      "обёрнуты в try/except ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_cb = src.index("def camera_callback(request):")
i_cb_end = src.index("\ndef ", i_cb + 1)
cb_body = src[i_cb:i_cb_end]
_n_calls = cb_body.count("_camera_jump_check(")
assert _n_calls == 2, (
    "ожидали ровно 2 вызова _camera_jump_check в camera_callback (idle-"
    "ветка + tracked-ветка), нашли %d" % _n_calls)
_n_gates = cb_body.count("if CAM_JUMP_SHADOW_ENABLED:")
assert _n_gates == 2, (
    "ожидали 2 гейта CAM_JUMP_SHADOW_ENABLED (по одному на ветку), "
    "нашли %d" % _n_gates)
print("    2 вызова _camera_jump_check, оба под CAM_JUMP_SHADOW_ENABLED")

print("\n=== 9. По исходному тексту: camera_callback не пишет ни в одну "
      "control/tracking-переменную ИЗ-ЗА camera jump shadow — блок вокруг "
      "каждого вызова трогает только _cam_shadow_*/событие в лог ===")
_LIVE_FORBIDDEN = ("global_roll_cmd", "global_pitch_cmd", "global_yaw_cmd",
                   "lock_cx", "lock_cy", "template_gray", "track_state",
                   "target_controllable", "target_box_main")
_idx = 0
_checked_blocks = 0
while True:
    _idx = cb_body.find("if CAM_JUMP_SHADOW_ENABLED:", _idx)
    if _idx < 0:
        break
    _block_end = cb_body.index("_etap(\"diagnostika\"", _idx)
    _block = cb_body[_idx:_block_end]
    assert "try:" in _block and "except Exception:" in _block, (
        "блок CAM_JUMP_SHADOW_ENABLED не обёрнут в try/except — сбой "
        "диагностики уронит camera_callback целиком")
    for name in _LIVE_FORBIDDEN:
        assert (name + " =") not in _block, (
            "camera jump shadow присваивает запрещённому имени %s" % name)
    _checked_blocks += 1
    _idx = _block_end
assert _checked_blocks == 2, "ожидали проверить оба блока, проверили %d" % _checked_blocks
print("    оба блока: try/except есть, ни одно из %d запрещённых имён не "
      "присваивается" % len(_LIVE_FORBIDDEN))

print("\n=== 10. CSV: cam_jump_* колонки присутствуют, читаются через "
      "_cam_shadow_dbg.get() в _capture_flight_row ===")
assert "cam_jump_exp_ratio,cam_jump_gray_delta,cam_jump_top_saturated," in src
assert "cam_jump_detected," in src
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
for field in ("exp_ratio", "gray_delta", "top_saturated", "jump"):
    assert ('_cam_shadow_dbg.get("%s")' % field) in row_body, (
        "_capture_flight_row не читает _cam_shadow_dbg.get(%r)" % field)
print("    все 4 поля читаются через _cam_shadow_dbg.get() в строке лога")

print("\nOK: Camera Jump Shadow — чистая функция сравнения соседних 1 Гц "
      "замеров (exp_ratio/gray_delta/top_saturated, любое поодиночке "
      "может дать jump=True), обе точки интеграции в camera_callback "
      "гейтятся флагом, обёрнуты в try/except и не пишут ни в одну "
      "control/tracking-переменную, CSV-колонки на месте")
