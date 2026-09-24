"""Pitch reference-conflict sustained run — найдено разбором reference
construction по всем 14 заходам crash-полёта (flight_20260922_165053),
24.09.2026. Формула причинности ПЕРЕДЕЛАНА после ревью по bb7d206.

Гипотеза "raw sign-conflict% разделяет заходы" НЕ подтвердилась: у #7
(перелёт) sign-conflict ~78% flip-кадров, у #13 (вертикальный промах) ~79%
— почти неотличимо. Мгновенный comp-dominance% тоже не разделяет: #4/#7/#9
все в районе 11-13%, только #13 выделяется (44%), но #7/#9 при этом уже
плохие исходы, а dom% этого не показывает.

Разделяет ДЛИТЕЛЬНОСТЬ непрерывной серии, где ИМЕННО добавление pitch_comp
меняет знак уже сформированной (static+lead+other) reference-ошибки. На 4
"чистых" (nudge~0) заходах максимальная непрерывная серия росла строго по
тяжести исхода:
  #4  сошлось              0.09с (3 кадра)
  #7  перелёт              0.31с (7 кадров)
  #9  недолёт              0.75с (15 кадров)
  #13 вертикальный промах  1.25с (29 кадров)

ПРИЧИННОСТЬ, НЕ КОРРЕЛЯЦИЯ (найдено ревью по bb7d206). Первая версия
сравнивала dy_raw (БЕЗ static/lead/other) против окончательного dy_aim
(СО ВСЕМИ компонентами) и требовала abs(att)>abs(dy_raw) — это ловило
"одновременно большая pitch_comp и итоговый flip", не "flip сделала именно
pitch_comp". Контрпример из ревью: dy_raw=-20, pitch_comp=+25, lead+LOS=
+30 -> итог +35. Старая формула: abs(25)>abs(20) и знаки разные -> ложно
объявляет "pitch_comp конфликт", хотя большую часть переворота (-20->+10)
сделали lead/LOS, а pitch_comp лишь довёл (+10->+35, знак уже был + и без
него). Новая формула: err_without_att=-20+30=+10 (уже +, без pitch_comp),
err_with_att=+35 (тоже +) -> conflict=False, причина правильно НЕ
приписана pitch_comp.

Этот тест не воспроизводит все 14 заходов (для этого нужны реальные CSV) —
проверяет механику самого shadow-поля (копится непрерывно, сбрасывается на
разрыве и на force_reset) и что pitch_ref_conflict на реалистичной
последовательности кадров ТОЧНО совпадает с прямым пересчётом по
определению причинности (err_without_att vs err_with_att), не с чем-то
похожим.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()


def kadr(dx=60, dy=40, fc_pitch=15.0, score=0.85, dt=FRAME_DT):
    with t.state_lock:
        t.target_box_main = (CX - 20 + dx, CY - 20 + dy,
                             CX + 20 + dx, CY + 20 + dy)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = score
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = fc_pitch
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(dt)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. Малый pitch/малый dy: pitch_comp мал, dominance не должно "
      "быть, run_s=0 ===")
force_reset()
_clk.t = 1000.0
for _ in range(5):
    sc = kadr(dx=60, dy=10, fc_pitch=10.0)
assert sc["pitch_ref_conflict"] is False
assert sc["pitch_ref_conflict_run_s"] == 0.0
print("    pitch_ref_conflict=False, run_s=0.0 на спокойном заходе")

print("\n=== 2. Большой fc_pitch + dy такого же знака, что и pitch_comp "
      "(камера уже видит цель НАД центром, компенсация тащит ниже) -> "
      "conflict, run_s растёт непрерывно ===")
force_reset()
_clk.t = 1000.0
prev_run = None
saw_conflict = False
for i in range(15):
    # dy отрицательный (камера видит цель выше центра) и растущий по
    # модулю; fc_pitch растёт вместе с ним — на реальном runaway это одно
    # и то же (аппарат наклоняется, camera видит цель всё выше).
    sc = kadr(dx=0, dy=-(20 + 3 * i), fc_pitch=30.0 + 3.0 * i)
    if sc["pitch_ref_conflict"]:
        saw_conflict = True
        if prev_run is not None:
            assert sc["pitch_ref_conflict_run_s"] > prev_run - 1e-9, (
                "run_s обязан монотонно расти, пока конфликт длится "
                "непрерывно (кадр %d: %.4f после %.4f)"
                % (i, sc["pitch_ref_conflict_run_s"], prev_run))
        prev_run = sc["pitch_ref_conflict_run_s"]
assert saw_conflict, (
    "растущий pitch_comp на растущем отрицательном dy должен был "
    "когда-нибудь превысить |dy_raw| и дать conflict=True")
print("    conflict появился и run_s монотонно рос все кадры подряд, "
      "финальный run_s=%.3f" % prev_run)

print("\n=== 3. Разрыв серии: один спокойный кадр обязан сбросить run_s "
      "в 0, а не продолжить накопление ===")
sc_calm = kadr(dx=0, dy=0, fc_pitch=0.0)
assert sc_calm["pitch_ref_conflict"] is False
assert sc_calm["pitch_ref_conflict_run_s"] == 0.0
sc_again = kadr(dx=0, dy=-80, fc_pitch=60.0)
if sc_again["pitch_ref_conflict"]:
    assert sc_again["pitch_ref_conflict_run_s"] < 0.2, (
        "новая серия после разрыва обязана начаться заново (маленький "
        "run_s), а не продолжить старый счёт: run_s=%.3f"
        % sc_again["pitch_ref_conflict_run_s"])
print("    после разрыва серии run_s корректно ушёл в 0 и начался заново")

print("\n=== 4. force_reset() чистит серию между заходами — новый заход "
      "не наследует чужой счётчик ===")
force_reset()
_clk.t = 1000.0
for i in range(5):
    sc = kadr(dx=0, dy=-(30 + 5 * i), fc_pitch=40.0 + 4.0 * i)
assert t._shadow_pitch_conflict_since_t is not None or not sc["pitch_ref_conflict"]
force_reset()
assert t._shadow_pitch_conflict_since_t is None, (
    "force_reset обязан сбросить _shadow_pitch_conflict_since_t — иначе "
    "новый заход стартует с чужим накопленным run_s")
_clk.t = 1000.0
sc_new = kadr(dx=60, dy=10, fc_pitch=10.0)
assert sc_new["pitch_ref_conflict_run_s"] == 0.0
print("    _shadow_pitch_conflict_since_t сброшен, новый заход стартует чисто")

print("\n=== 5. Причинность: pitch_ref_conflict ТОЧНО совпадает с прямым "
      "пересчётом по определению (err_without_att vs err_with_att), не с "
      "чем-то похожим — проверка формулы, а не просто финального "
      "результата ===")
force_reset()
_clk.t = 1000.0
seen_conflict = False
seen_no_conflict = False
for i in range(20):
    # dy ФИКСИРОВАН (не растёт вместе с pitch — в отличие от теста #2), а
    # fc_pitch растёт с нуля: err_y стартует одного знака с dy_raw (att мал),
    # потом att дорастает и переворачивает err_y — обе ветки формулы
    # проверяются на одном прогоне (в тесте #2 dy и fc_pitch росли синхронно
    # и того же не давали).
    sc = kadr(dx=15 * ((-1) ** i) + i, dy=-35.0, fc_pitch=4.0 * i)
    err_y = sc["err_y"]
    att_y = sc["ref_att_y"]
    err_without_att = err_y - att_y
    expected = (err_without_att != 0.0 and err_y != 0.0
                and (err_without_att > 0) != (err_y > 0))
    assert sc["pitch_ref_conflict"] == expected, (
        "кадр %d: pitch_ref_conflict=%s разошёлся с прямым пересчётом по "
        "определению причинности (err_without_att=%.2f, err_with_att=%.2f, "
        "ожидали %s) — формула в коде не то же самое, что заявлено"
        % (i, sc["pitch_ref_conflict"], err_without_att, err_y, expected))
    if sc["pitch_ref_conflict"]:
        seen_conflict = True
    else:
        seen_no_conflict = True
assert seen_conflict and seen_no_conflict, (
    "сценарий обязан был дать оба варианта (conflict и не-conflict), "
    "иначе проверка формулы покрывает только одну ветку")
print("    pitch_ref_conflict совпал с err_without_att/err_with_att "
      "пересчётом на всех 20 кадрах (обе ветки встретились)")

print("\n=== 6. Диагностика-only: только читает уже посчитанные box_cy/"
      "CENTER_Y/_ref_att_y/_shadow_err_y, никакого нового измерения ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = src.index("# ================= SHADOW CONTROLLER =================")
i_end = src.index("# ================ /SHADOW CONTROLLER ================")
shadow_body = src[i_start:i_end]
i_conflict = shadow_body.index("Reference conflict / sustained pitch-compensation")
i_windup_call = shadow_body.index("_shadow_windup_step(\n            adx,")
assert i_conflict < i_windup_call, (
    "блок должен идти сразу после AimReference (§4), до anti-windup — "
    "порядок внутри try не влияет на изоляцию, но так он не создаёт "
    "видимость, что участвует в windup-решении")
print("    блок стоит сразу после AimReference, до anti-windup — по месту "
      "в файле")

print("\n=== 7. Реальный баг (найден ревью по fad59c8): "
      "_reset_geometry_history() обязан сбрасывать _shadow_pitch_conflict_"
      "since_t — иначе конфликт до re-anchor/frame gap и конфликт после "
      "склеиваются в одну общую серию, хотя это разные temporal epoch ===")
force_reset()
_clk.t = 1000.0
sc_run = None
for i in range(15):
    sc_run = kadr(dx=0, dy=-35.0, fc_pitch=4.0 * i)
assert sc_run["pitch_ref_conflict"] is True, (
    "сценарий должен был построить непрерывный conflict-run для теста")
assert sc_run["pitch_ref_conflict_run_s"] > 0.15, (
    "серия должна успеть накопить заметный run_s перед разрывом эпохи")
_run_before_break = sc_run["pitch_ref_conflict_run_s"]
t._reset_geometry_history("test_epoch_break")
assert t._shadow_pitch_conflict_since_t is None, (
    "_reset_geometry_history() обязан сбросить _shadow_pitch_conflict_"
    "since_t в None — без этого следующий conflict-кадр продолжит СТАРЫЙ "
    "отсчёт времени вместо нового")
_clk.tick(FRAME_DT)
sc_after = kadr(dx=0, dy=-35.0, fc_pitch=80.0)
if sc_after["pitch_ref_conflict"]:
    assert sc_after["pitch_ref_conflict_run_s"] < _run_before_break, (
        "после разрыва эпохи новая серия обязана быть КОРОЧЕ старой "
        "(отсчёт заново), а не продолжением: run_s=%.3f, было до разрыва "
        "%.3f" % (sc_after["pitch_ref_conflict_run_s"], _run_before_break))
print("    _reset_geometry_history() сбрасывает since_t; серия после "
      "разрыва (run_s=%.3f) короче серии до разрыва (%.3f), а не "
      "продолжение одной склеенной" % (
          sc_after.get("pitch_ref_conflict_run_s", 0.0), _run_before_break))

print("\nOK: shadow_pitch_ref_conflict/_run_s копится непрерывно, "
      "изолирует причинность именно pitch_comp (err_without_att vs "
      "err_with_att, не голый dy_raw vs dy_aim), сбрасывается на разрыве "
      "серии, на force_reset() и на _reset_geometry_history(), не путает "
      "заходы")
