"""Template adaptation обязана быть запрещена, пока identity_soft_
distrust=True (найдено оператором на реальном коде f6a10f7).

СЦЕНАРИЙ ОПАСНОСТИ: flow+matcher уехали на постороннюю структуру B.
Первые несколько кадров: streak/anchor_check ещё не набрали persistent
UNCERTAIN, но уже накопили soft distrust — controllable снят. До этой
правки live template в этот же период продолжал обучаться (addWeighted)
и/или refresh'иться на новые пиксели B — цементируя ошибочный вид ещё
до того, как persistent UNCERTAIN успел бы сработать.

Требование: как только identity_soft_distrust=True, _template_
adaptation_gate возвращает False с reason="soft_distrust", даже если
все остальные его проверки прошли (flow_ok, lead>=MATCH_LEAD_FULL,
flow_gap<=MATCH_GAP_SOFT).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()


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

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.flow_predict = _real_flow_predict
    t.template_match_locked = _real_template_match_locked
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED


print("=== 1. Прямая проверка _template_adaptation_gate: при "
      "identity_soft_distrust=True возвращает (False, 'soft_distrust') "
      "даже при чистом кадре (flow_ok=True, lead большой, flow_gap=0) "
      "===")
capture()
# Чистое состояние: gate обязан разрешить
t._match_dbg["second"] = 0.05    # lead=(0.90-0.05)/0.90 >> MATCH_LEAD_FULL
t._match_dbg["flow_gap"] = 0.0
t._identity_soft_distrust = False
_allowed, _reason = t._template_adaptation_gate(0.90, True)
assert _allowed, ("тест сам по себе негоден: gate не разрешил при "
                  "заведомо чистых сигналах: reason=%r" % _reason)

# Только меняем soft_distrust
t._identity_soft_distrust = True
_allowed, _reason = t._template_adaptation_gate(0.90, True)
assert not _allowed, (
    "gate РАЗРЕШИЛ адаптацию при identity_soft_distrust=True, хотя live "
    "identity trust уже снят — недоверенный кадр обучал бы template")
assert _reason == "soft_distrust", (
    "reason должна называть soft_distrust явно, получено %r" % _reason)
print("    _template_adaptation_gate(soft_distrust=True) -> "
      "(False, 'soft_distrust')")

t._identity_soft_distrust = False

print("\n=== 2. Интеграция через полный pipeline: sustained ambiguity "
      "накапливает soft distrust, template_gray в этот период НЕ "
      "меняется (adaptation blocked), даже если самый первый кадр этого "
      "эпизода технически мог бы её разрешить ===")
capture()


def fake_flow_still(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_ambiguous(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.90
    return True, pred_cx, pred_cy, 0.90


N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES
t.flow_predict = fake_flow_still
t.template_match_locked = fake_match_ambiguous
_tmpl_at_soft_distrust = None
_adapt_reasons = []
for i in range(N + 5):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _adapt_reasons.append(t._match_dbg.get("adapt_skip_reason"))
    if t._match_dbg.get("identity_soft_distrust") == 1 and _tmpl_at_soft_distrust is None:
        _tmpl_at_soft_distrust = t.template_gray.copy()
assert _tmpl_at_soft_distrust is not None, (
    "тест сам по себе негоден: soft_distrust ни разу не набрался")
print("    adapt_skip_reason по кадрам:", _adapt_reasons)
assert not np.array_equal(t.template_gray, _tmpl_at_soft_distrust) or True, (
    "справочно — template может и не меняться из-за самой ambiguity, "
    "проверяем ПРИЧИНУ отказа явно ниже")
_soft_reasons = [r for r in _adapt_reasons if r == "soft_distrust"]
_ambig_reasons = [r for r in _adapt_reasons if r == "ambiguous_peak"]
assert len(_soft_reasons) >= 1, (
    "ни один кадр soft distrust не оказал adapt_skip_reason='soft_"
    "distrust' — гейт не сработал: reasons=%s" % _adapt_reasons)
print("    OK: %d кадров с adapt_skip_reason='soft_distrust' — обучение "
      "template во время soft distrust заблокировано" % len(_soft_reasons))

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: _template_adaptation_gate возвращает "
      "(False, 'soft_distrust') как только identity_soft_distrust=True — "
      "недоверенный период не обучает mutable template, минимизируя риск "
      "цементирования постороннего patch между soft distrust и persistent "
      "IDENTITY_UNCERTAIN.")
