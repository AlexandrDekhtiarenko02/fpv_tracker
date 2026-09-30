"""Manual nudge и nudge-release НЕ являются подтверждением identity —
soft distrust, накопленный persistent-счётчиками, обязан пережить
early-return ветки process_locked_tracker (найдено оператором на
реальном коде f6a10f7).

СЦЕНАРИЙ, воспроизводящий bypass: identity уже в soft distrust
(anchor_check_streak>=1 или ambiguity/flow_gap streak >= CONFIRM_FRAMES,
persistent UNCERTAIN ещё не набрал). Пилот двигает стик. До этой правки
_identity_soft_distrust безусловно сбрасывался в False в начале КАЖДОГО
process_locked_tracker(), а ветка активного nudge:
    - выполняется РАНЬШЕ обычного TRACKED-пути
    - самостоятельно ставит target_controllable=True
    - зовёт update_control_from_target() и делает return
Централизованная проверка _update_control_from_target_impl() в этот
момент читала _identity_soft_distrust — уже сброшенный в False — и не
блокировала controllable. То же самое на nudge-release.

Требование: manual nudge/release НЕ восстанавливают controllable, если
до этого persistent-счётчики identity уже свидетельствовали о
недоверии. Восстановить controllable может только новое успешное
identity confirmation (свежий dual-signal кадр без ambiguity — сбрасывает
_identity_uncertain_streak в 0; успешный anchor-check — сбрасывает
_identity_anchor_check_streak в 0).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

AUX2_IDX = t.MANUAL_NUDGE_ROLL_AUX_IDX
AUX3_IDX = t.MANUAL_NUDGE_PITCH_AUX_IDX


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

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked


def make_scene(seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()


def set_stick(roll_us, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.flow_predict = _real_flow_predict
    t.template_match_locked = _real_template_match_locked
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick(0)
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED


def fake_flow_still(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_ambiguous(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.90   # lead=0 -> ambiguous
    return True, pred_cx, pred_cy, 0.90


def fake_match_clean(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

print("=== 1. Накопить soft distrust: серия ambiguous кадров до "
      "controllable=False (persistent UNCERTAIN блокируется anchor "
      "arbiter'ом) ===")
capture()
t.flow_predict = fake_flow_still
t.template_match_locked = fake_match_ambiguous
for _ in range(N + 1):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
with t.state_lock:
    _ctrl_before = t.target_controllable
assert not _ctrl_before, "тест сам по себе негоден: soft distrust не набрался"
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "тест сам по себе негоден: persistent UNCERTAIN сработал раньше "
    "времени, проверять nudge-bypass нечего")
assert t._identity_uncertain_streak >= N
print("    _identity_uncertain_streak=%d, controllable=False, "
      "track_state=TRACKED — исходное состояние для проверки установлено"
      % t._identity_uncertain_streak)

print("\n=== 2. Активный nudge: target_controllable ставится в True "
      "локально в ветке, но должен быть принудительно возвращён в False "
      "через централизованный check в _update_control_from_target_impl() "
      "===")
set_stick(200.0)   # заметно за пределами MANUAL_NUDGE_DEADBAND_US
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
with t.state_lock:
    _ctrl_nudge = t.target_controllable
assert t._match_dbg.get("manual_nudge") == 1, (
    "тест сам по себе негоден: nudge не активировался")
assert not _ctrl_nudge, (
    "controllable=True во время nudge, хотя persistent-счётчики identity "
    "показывали недоверие ДО nudge — manual correction сама себя "
    "подтвердила как identity confirmation, что запрещено")
print("    активный nudge: track_state=%s manual_nudge=1 "
      "controllable=False (централизованный check сработал)"
      % t.track_state)

print("\n=== 3. Nudge release (reanchor): controllable тоже НЕ должен "
      "восстановиться сам по себе, без нового identity confirmation ===")
set_stick(0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
with t.state_lock:
    _ctrl_release = t.target_controllable
_geom_epoch_after = t.geometry_epoch
assert _ctrl_release is False, (
    "controllable=True сразу после release — reanchor сам себя подтвердил "
    "как identity confirmation")
print("    release: track_state=%s controllable=False, "
      "geometry_epoch=%d (reanchor реально случился, но identity trust "
      "не восстановлен)" % (t.track_state, _geom_epoch_after))

print("\n=== 4. Свежий чистый dual-signal кадр — controllable "
      "восстанавливается только теперь ===")
t.template_match_locked = fake_match_clean
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._identity_uncertain_streak == 0
with t.state_lock:
    _ctrl_after = t.target_controllable
assert _ctrl_after, (
    "controllable не восстановился даже после явного identity "
    "confirmation (чистый dual-signal кадр)")
print("    чистый кадр -> streak=0, controllable=True — восстановлен "
      "по свежему identity confirmation, а не по факту nudge/release")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: manual nudge и nudge-release больше не бывают "
      "подтверждением identity — persistent-счётчики distrust переживают "
      "early-return ветки process_locked_tracker и продолжают душить "
      "controllable через централизованную проверку до тех пор, пока не "
      "случится реальное свежее identity confirmation.")
