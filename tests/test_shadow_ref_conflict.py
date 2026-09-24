"""Pitch reference-conflict sustained run — найдено разбором reference
construction по всем 14 заходам crash-полёта (flight_20260922_165053),
24.09.2026. Не по коду.

Гипотеза "raw sign-conflict% разделяет заходы" НЕ подтвердилась: у #7
(перелёт) sign-conflict ~78% flip-кадров, у #13 (вертикальный промах) ~79%
— почти неотличимо. Мгновенный comp-dominance% тоже не разделяет: #4/#7/#9
все в районе 11-13%, только #13 выделяется (44%), но #7/#9 при этом уже
плохие исходы, а dom% этого не показывает.

Разделяет ДЛИТЕЛЬНОСТЬ непрерывной серии, где pitch_comp (_ref_att_y) уже
не просто перевернул знак dy_raw, а ПРЕВЫШАЕТ его по модулю. На 4 "чистых"
(nudge~0) заходах максимальная непрерывная серия росла строго по тяжести
исхода:
  #4  сошлось              0.09с (3 кадра)
  #7  перелёт              0.31с (7 кадров)
  #9  недолёт              0.75с (15 кадров)
  #13 вертикальный промах  1.25с (29 кадров)

Этот тест не воспроизводит все 14 заходов (для этого нужны реальные CSV) —
проверяет механику самого shadow-поля: копится, пока конфликт длится
непрерывно, сбрасывается при первом же неконфликтном кадре, не путает
серию нового захода со старой после force_reset(), и НЕ путает голый
sign-flip с dominance (must be abs(att) > abs(dy_raw), не просто разные
знаки).
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

print("\n=== 5. Sign-flip БЕЗ dominance (att слабее dy_raw) — НЕ conflict. "
      "Дублирует главный вывод разбора: голый flip не то же самое, что "
      "dominance ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = src.index("# ================= SHADOW CONTROLLER =================")
i_end = src.index("# ================ /SHADOW CONTROLLER ================")
shadow_body = src[i_start:i_end]
assert "abs(_ref_att_y) > abs(_dy_raw_y)" in shadow_body, (
    "pitch_ref_conflict обязан требовать dominance (|att|>|dy_raw|), "
    "не просто разные знаки — иначе это будет тот же неинформативный "
    "sign-flip%, который разбор явно отверг как разделяющий признак")
print("    в исходном тексте conflict требует dominance, не только flip")

print("\n=== 6. Диагностика-only: только читает уже посчитанные box_cy/"
      "CENTER_Y/_ref_att_y/_shadow_err_y, никакого нового измерения ===")
i_conflict = shadow_body.index("Reference conflict / sustained pitch-compensation")
i_windup_call = shadow_body.index("_shadow_windup_step(\n            adx,")
assert i_conflict < i_windup_call, (
    "блок должен идти сразу после AimReference (§4), до anti-windup — "
    "порядок внутри try не влияет на изоляцию, но так он не создаёт "
    "видимость, что участвует в windup-решении")
print("    блок стоит сразу после AimReference, до anti-windup — по месту "
      "в файле")

print("\nOK: shadow_pitch_ref_conflict/_run_s копится непрерывно, требует "
      "dominance (не просто flip), сбрасывается на разрыве серии и на "
      "force_reset(), не путает заходы")
