"""CAMERA JUMP SHADOW — диагностика-only детектор скачков экспозиции/
яркости/пересвета (разбор 24 заходов 25.09.2026, ревью после Auto
Template Refresh + ревью по 66b7f4b).

ПОВОД. pitch<->ExposureTime корреляция +0.867, pitch<->mean_gray +0.824.
При pitch 40-50° выдержка вырастает ~в 5 раз против pitch<10° (604 ->
3147 мкс), mean_gray 115 -> 177. Чистые эпизоды (заход #16: 1691 -> 2618
мкс и +36 mean_gray практически за один 1 Гц диагностический тик) прямо
показывают "накренился -> картинка резко побелела". AeEnable=True/
AwbEnable=True (динамическая экспозиция/баланс белого) — намеренный, уже
проверенный выбор (см. test_dynamic_ae.py), эта диагностика его НЕ
трогает и НЕ ограничивает — только измеряет.

ПРАВКИ ПОСЛЕ РЕВЬЮ ПЕРВОГО ПРОГОНА (66b7f4b):
1. _cam_shadow_dbg живёт ~1с в CSV, но пишется каждый кадр — без метки
   момента замера одна вспышка выглядела бы как "20 кадров подряд
   jump=True". cam_jump_sample_seq + cam_jump_sample_age_ms решают это.
2. top_saturated — СОСТОЯНИЕ (может длиться секундами), не скачок.
   Раньше входил в общий "jump" — многосекундный пересвет давал бы
   события CAM_JUMP каждую секунду. Теперь jump — строго exp/gray
   дельта (переходный по построению); top_saturated отдельно, событие
   в лог для него — только на переднем фронте.
3. Сравнение "текущий/предыдущий" не знало dt между замерами — плавный
   дрейф за несколько секунд после паузы выглядел бы одним скачком.
   dt_s теперь параметр, CAM_JUMP_MAX_VALID_DT_S гейтит именно jump
   (exp_ratio/gray_delta по-прежнему возвращаются для разбора).
4. pitch в событии писался без свежести attitude — теперь рядом
   att_age_ms той же формулой, что _att_age_ms в process_locked_tracker.

ПРАВКА ПОСЛЕ РЕВЬЮ ВТОРОГО ПРОГОНА (3f977f2): att_age_ms попал в CSV, а
сам pitch_deg, замороженный на МОМЕНТ ТОГО ЖЕ замера, — нет. Обычный
fc_pitch в этой же строке пишется каждый кадр и успевает уйти далеко за
то время, пока cam_jump_* держат значения секундной давности — фильтр
"cam_jump_detected=True -> смотрим соседний fc_pitch" сравнивал бы
скачок камеры с ЧУЖИМ, более новым тангажом. cam_jump_pitch_deg делает
одну запись замера самодостаточной: sample_seq/sample_age_ms/exp_ratio/
gray_delta/dt_ms/jump/top_saturated/pitch_deg/att_age_ms — всё с ОДНОГО
и того же 1 Гц момента.

Реальная камера недоступна в offline-прогоне — интеграция в
camera_callback проверяется по исходному тексту (тот же подход, что
test_dynamic_ae.py), арифметика — прямым вызовом чистой функции.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Нет предыдущего замера -> дельты None, jump=False ===")
r = t._camera_jump_check(2000.0, None, 130.0, None, 120.0, None)
assert r["exp_ratio"] is None and r["gray_delta"] is None
assert r["dt_ms"] is None and r["dt_valid"] is False
assert r["jump"] is False
print("    exp_ratio=None gray_delta=None dt_valid=False jump=False")

print("\n=== 2. Маленькое изменение, dt=1с (штатный тик) -> jump=False ===")
r = t._camera_jump_check(2100.0, 2000.0, 132.0, 130.0, 120.0, 1.0)
assert abs(r["exp_ratio"] - 1.05) < 1e-6
assert abs(r["gray_delta"] - 2.0) < 1e-6
assert r["dt_valid"] is True and abs(r["dt_ms"] - 1000.0) < 1e-6
assert r["top_saturated"] is False
assert r["jump"] is False, "5%% рост экспозиции и +2 gray не должны считаться скачком"
print("    exp_ratio=1.05 gray_delta=2.0 dt_ms=1000 -> jump=False")

print("\n=== 3. Скачок экспозиции ВВЕРХ при штатном dt (пример стенда: "
      "604 -> 3147, ~5.2x за 1с) -> jump=True по exp_ratio ===")
r = t._camera_jump_check(3147.0, 604.0, 140.0, 138.0, 120.0, 1.0)
assert r["exp_ratio"] > t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert r["jump"] is True
print("    exp_ratio=%.2f (>%.1f порог), dt=1с -> jump=True"
      % (r["exp_ratio"], t.CAM_JUMP_EXP_RATIO_THRESHOLD))

print("\n=== 4. Скачок экспозиции ВНИЗ -> jump=True тоже ===")
r = t._camera_jump_check(600.0, 3000.0, 130.0, 132.0, 120.0, 1.0)
assert r["exp_ratio"] < 1.0 / t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert r["jump"] is True
print("    exp_ratio=%.3f (резкое падение) -> jump=True" % r["exp_ratio"])

print("\n=== 5. Скачок mean_gray сам по себе (пример стенда: +36 за 1с) "
      "-> jump=True даже при стабильной экспозиции ===")
r = t._camera_jump_check(2000.0, 1980.0, 166.0, 130.0, 120.0, 1.0)
assert r["exp_ratio"] < t.CAM_JUMP_EXP_RATIO_THRESHOLD
assert abs(r["gray_delta"] - 36.0) < 1e-6
assert r["jump"] is True
print("    exp стабильна, gray_delta=36.0 (>%.1f порог), dt=1с -> jump=True"
      % t.CAM_JUMP_GRAY_DELTA_THRESHOLD)

print("\n=== 6. НАЙДЕНО ревью (правка 2): пересвет верхней строки — "
      "СОСТОЯНИЕ, НЕ входит в jump. Держится несколько тиков подряд без "
      "новых exp/gray скачков -> jump=False каждый раз, top_saturated="
      "True каждый раз (не 'один скачок', а 'плохое состояние длится') ===")
for _ in range(5):
    r = t._camera_jump_check(2000.0, 1990.0, 130.0, 129.0, 252.0, 1.0)
assert r["top_saturated"] is True
assert r["jump"] is False, (
    "top_saturated не должен сам по себе давать jump=True — иначе "
    "многосекундный пересвет выглядел бы как серия скачков (ровно то, "
    "что нашло ревью по 66b7f4b)")
print("    top_row_mean=252.0 -> top_saturated=True, jump=False (exp/gray "
      "стабильны) — состояние не путается со скачком")

print("\n=== 7. НАЙДЕНО ревью (правка 3): большой dt (после паузы/сбоя) "
      "-> exp_ratio/gray_delta всё равно возвращаются (для разбора), но "
      "jump=False — плавный дрейф за 4с не считается 'скачком за доли "
      "секунды' ===")
r = t._camera_jump_check(3147.0, 604.0, 140.0, 138.0, 120.0,
                         t.CAM_JUMP_MAX_VALID_DT_S + 1.0)
assert r["exp_ratio"] > t.CAM_JUMP_EXP_RATIO_THRESHOLD, (
    "exp_ratio обязан быть посчитан даже при большом dt — для разбора")
assert r["dt_valid"] is False
assert r["jump"] is False, (
    "тот же самый скачок exp_ratio, что в секции 3, но при dt=%.1fс "
    "(>порог %.1fс) не должен засчитываться как jump"
    % (t.CAM_JUMP_MAX_VALID_DT_S + 1.0, t.CAM_JUMP_MAX_VALID_DT_S))
print("    тот же exp_ratio=%.2f, но dt_valid=False (dt=%.1fс > %.1fс "
      "порог) -> jump=False, хотя число то же, что дало jump=True в §3"
      % (r["exp_ratio"], t.CAM_JUMP_MAX_VALID_DT_S + 1.0, t.CAM_JUMP_MAX_VALID_DT_S))

print("\n=== 8. Недостаточно данных (metadata недоступна) -> None, не "
      "False; top_saturated честно False при top_row_mean=None ===")
r = t._camera_jump_check(None, 2000.0, None, 130.0, None, 1.0)
assert r["exp_ratio"] is None and r["gray_delta"] is None
assert r["top_saturated"] is False
assert r["jump"] is False
print("    exp_us=None/mean_gray=None/top_row_mean=None -> дельты None, "
      "jump=False")

print("\n=== 9. По исходному тексту: обе точки вызова в camera_callback "
      "(idle и tracked ветки) гейтятся CAM_JUMP_SHADOW_ENABLED, обёрнуты "
      "в try/except, передают dt_s и пишут att_age_ms ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_cb = src.index("def camera_callback(request):")
i_cb_end = src.index("\ndef ", i_cb + 1)
cb_body = src[i_cb:i_cb_end]
_n_calls = cb_body.count("_camera_jump_check(")
assert _n_calls == 2, (
    "ожидали ровно 2 вызова _camera_jump_check (idle + tracked), нашли %d"
    % _n_calls)
_n_gates = cb_body.count("if CAM_JUMP_SHADOW_ENABLED:")
assert _n_gates == 2
_n_dt = cb_body.count("_cam_dt_s")
assert _n_dt >= 2, "dt_s (правка 3) обязан передаваться в оба вызова"
_n_att_age = cb_body.count('_cjc["att_age_ms"] = (')
assert _n_att_age == 2, "att_age_ms (правка 4) обязан считаться в обеих ветках"
_n_edge = cb_body.count("not _cam_shadow_prev_top_saturated")
assert _n_edge == 2, (
    "событие CAM_TOP_SATURATED (правка 2) обязано быть edge-triggered "
    "(rising edge) в обеих ветках")
print("    2 вызова, оба под флагом/try-except, dt_s и att_age_ms "
      "считаются, top_saturated-событие edge-triggered")

print("\n=== 10. По исходному тексту: camera_callback не пишет ни в одну "
      "control/tracking-переменную из-за camera jump shadow ===")
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
    assert "try:" in _block and "except Exception:" in _block
    for name in _LIVE_FORBIDDEN:
        assert (name + " =") not in _block, (
            "camera jump shadow присваивает запрещённому имени %s" % name)
    _checked_blocks += 1
    _idx = _block_end
assert _checked_blocks == 2
print("    оба блока: try/except есть, ни одно из %d запрещённых имён не "
      "присваивается" % len(_LIVE_FORBIDDEN))

print("\n=== 11. CSV: новые cam_jump_*/cam_top_saturated колонки на "
      "месте, sample_age_ms считается ЗАНОВО каждую строку (не берётся "
      "из замороженного словаря) ===")
assert "cam_jump_exp_ratio,cam_jump_gray_delta,cam_jump_dt_ms," in src
assert "cam_jump_detected,cam_top_saturated," in src
assert "cam_jump_sample_seq,cam_jump_sample_age_ms,cam_jump_att_age_ms," in src
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
assert "_cam_sample_t = _cam_shadow_dbg.get(\"sample_t\")" in row_body
assert "time.monotonic() - _cam_sample_t" in row_body, (
    "sample_age_ms обязан пересчитываться от time.monotonic() каждую "
    "строку, а не читаться готовым из словаря — иначе тот же баг "
    "'заморожено на момент замера', который эта правка и чинит")
for field in ("exp_ratio", "gray_delta", "dt_ms", "jump", "top_saturated",
             "sample_seq", "att_age_ms", "pitch_deg"):
    assert ('_cam_shadow_dbg.get("%s")' % field) in row_body, (
        "_capture_flight_row не читает _cam_shadow_dbg.get(%r)" % field)
print("    CSV-колонки на месте, sample_age_ms — живой пересчёт, не "
      "замороженное значение")

print("\nOK: Camera Jump Shadow — jump строго переходный (exp/gray "
      "дельта, top_saturated отдельно как состояние), dt между замерами "
      "гейтит валидность сравнения, sample_seq/sample_age_ms снимают "
      "неоднозначность '1 замер vs много строк CSV', att_age_ms рядом с "
      "pitch в событии — всё диагностика-only, не пишет ни в одну "
      "control/tracking-переменную")
