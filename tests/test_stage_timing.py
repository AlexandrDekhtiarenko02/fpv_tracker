"""Stage timing по кадру: control ("upravlenie"), диагностика main-кадра
("diagnostika"), overlay — плюс MEAN/TOPMEAN считаются раз в секунду, а не
каждый кадр (диагностический коммит).

Раньше _etap_ms уже мерил "potok" (flow), "sovpadenie" (match), "primerka"
(scale-fitting) — но не control, не диагностику (MEAN/TOPMEAN), не overlay.
Без этой разбивки видно только «кадр стал дороже», а какой именно этап
съедает время — нет.

Отдельно: mm.array[:, :, 1].mean() (307200 пикселей main-кадра) и
_mean_top_strip() раньше гонялись КАЖДЫЙ кадр (24 раза в секунду) ради
диагностического числа, которое читается раз в секунду (консольный дебаг,
CSV — той же кадансу, что уже существующие CMA/CPU/экспозиция). Теперь
throttled тем же таймером (_cma_read_t/CMA_READ_PERIOD_S), что и они.

Control-часть проверяется ФУНКЦИОНАЛЬНО (update_control_from_target()
доступен в offline-харнессе). Diagnostika/overlay — по исходному тексту:
camera_callback требует настоящую камеру (MappedArray/request), которая в
offline-прогоне заглушена (см. test_cam_fps.py/test_dynamic_ae.py для
того же класса проверок).
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
CX, CY = t.CENTER_X, t.CENTER_Y


print("=== 1. update_control_from_target() — тонкая обёртка, контур в "
      "_update_control_from_target_impl() ===")
assert "def _update_control_from_target_impl():" in src, (
    "реализация не переименована — обёртки для замера времени не "
    "получится без переименования исходной функции")
i_wrap = src.index("def update_control_from_target():")
i_wrap_end = src.index("\ndef ", i_wrap + 1)
wrap_body = src[i_wrap:i_wrap_end]
assert "_update_control_from_target_impl()" in wrap_body, (
    "обёртка не вызывает реализацию — контур перестанет работать")
assert '_etap("upravlenie"' in wrap_body, (
    "обёртка не замеряет время через _etap — stage timing для control "
    "не появится")
print("    update_control_from_target() зовёт _update_control_from_target_impl() "
      "и меряет через _etap")

print("\n=== 2. Вызов update_control_from_target() пишет "
      "_etap_ms['upravlenie'] ===")
t._etap_ms.pop("upravlenie", None)
t.target_box_main = (CX - 20, CY - 20, CX + 20, CY + 20)
t.target_visible = True
t.target_controllable = True
t.lock_w0 = t.lock_h0 = 30.0
with t.state_lock:
    t.app_state["rc_throttle"] = 1450
    t.app_state["rc_throttle_ts"] = t.time.monotonic()
    t.app_state["fc_pitch_deg"] = 15.0
    t.app_state["fc_pitch_ts"] = t.time.monotonic()
t.update_control_from_target()
assert "upravlenie" in t._etap_ms, (
    "_etap_ms['upravlenie'] не появился после update_control_from_target()")
assert t._etap_ms["upravlenie"] >= 0.0, "время этапа отрицательное"
print("    _etap_ms['upravlenie'] = %.4f мс" % t._etap_ms["upravlenie"])

print("\n=== 3. camera_callback: MEAN/TOPMEAN только на тике 1 Гц "
      "(_diag_1hz_tick), не каждый кадр ===")
i_cb = src.index("def camera_callback(request):")
i_cb_end = src.index("\ndef ", i_cb + 1)
cb_body = src[i_cb:i_cb_end]
assert "_diag_1hz_tick = (_cma_read_t == _cb_t0)" in cb_body, (
    "флаг тика 1 Гц для MEAN/TOPMEAN не найден — диагностика рискует "
    "остаться безусловной (каждый кадр)")
_tick_sites = cb_body.count("if _diag_1hz_tick:")
assert _tick_sites >= 2, (
    "if _diag_1hz_tick: встречается реже двух раз — в camera_callback два "
    "независимых MappedArray-блока (idle-ветка и активная), и оба обязаны "
    "гейтить MEAN/TOPMEAN одинаково (нашлось %d)" % _tick_sites)
print("    if _diag_1hz_tick: встречается %d раза (оба блока MappedArray)"
      % _tick_sites)

print("\n=== 4. mm.array[:, :, 1].mean() и _mean_top_strip() ВНУТРИ "
      "if _diag_1hz_tick:, не до него ===")
for marker in ("mm.array[:, :, 1].mean()", "_mean_top_strip(mm.array)"):
    positions = [p for p in range(len(cb_body))
                 if cb_body.startswith(marker, p)]
    assert positions, "%s не найден в camera_callback вовсе" % marker
    # Отсеиваем упоминания в прозе комментариев (строка до marker уже
    # содержит "#") — реальных вызовов это не касается.
    line_starts = [cb_body.rfind("\n", 0, p) + 1 for p in positions]
    positions = [p for p, ls in zip(positions, line_starts)
                 if "#" not in cb_body[ls:p]]
    assert positions, "%s встречается только в комментариях-пояснениях" % marker
    for p in positions:
        # Ближайший if _diag_1hz_tick: ПЕРЕД этой позицией обязан быть
        # ближе, чем предыдущий "with MappedArray" (иначе замер уже не
        # внутри гейта, а в общем теле блока).
        i_tick = cb_body.rfind("if _diag_1hz_tick:", 0, p)
        i_block = cb_body.rfind("with MappedArray(request, \"main\")", 0, p)
        assert i_tick > i_block, (
            "%s на позиции %d не защищён if _diag_1hz_tick: — считается "
            "каждый кадр, а не раз в секунду" % (marker, p))
print("    оба вызова (%d вхождений) внутри if _diag_1hz_tick:"
      % sum(cb_body.count(m) for m in
            ("mm.array[:, :, 1].mean()", "_mean_top_strip(mm.array)")))

print("\n=== 5. overlay замеряется через _etap, сам draw_overlay_on_frame "
      "не тронут ===")
_ovl_etap = cb_body.count('_etap("overlay"')
assert _ovl_etap >= 2, (
    "_etap('overlay', ...) встречается реже двух раз — по одному разу на "
    "каждый из двух MappedArray-блоков (нашлось %d)" % _ovl_etap)
# draw_overlay_on_frame САМ не должен был измениться этим коммитом —
# проверяем, что сигнатура вызова (аргумент) осталась прежней.
assert "draw_overlay_on_frame(mm.array)" in cb_body, (
    "вызов draw_overlay_on_frame(mm.array) изменился — по условию задачи "
    "оверлей в этом коммите не трогаем")
print("    _etap('overlay', ...) вокруг обоих вызовов "
      "draw_overlay_on_frame(mm.array), сам вызов не изменён")

print("\n=== 6. Новые колонки CSV на месте и в _row_values ===")
for col in ("ms_upravlenie", "ms_diagnostika", "ms_overlay"):
    assert col in t._FLIGHT_LOG_COLUMNS, "%s отсутствует в заголовке CSV" % col
assert '_etap_ms.get("upravlenie")' in src
assert '_etap_ms.get("diagnostika")' in src
assert '_etap_ms.get("overlay")' in src
print("    ms_upravlenie/ms_diagnostika/ms_overlay в заголовке и в "
      "_row_values")

print("\nOK: control/diagnostika/overlay замеряются через _etap, "
      "MEAN/TOPMEAN throttled до 1 Гц, оверлей сам не изменён")
