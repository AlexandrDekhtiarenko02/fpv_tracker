"""Confirmed identity anchor не меняется, пока подтверждённая цель растёт
в кадре (отчёт 25.09, разбор поверх 64949ec, п.B/K-5).

Реальный рост через чистую CV-сцену (цель на самом деле увеличивается по
пикселям) оказался хрупким инструментом для ЭТОГО теста и по не относящейся
к делу причине: измерение масштаба намеренно (и правильно —
см. test_rost_ramki.py) тормозит рост, если совпадение от него хоть немного
проседает, а на синтетической сцене с ограниченным окном поиска оно
проседает почти сразу — тест тогда проверял бы устойчивость анти-самоходного
тормоза, а не то, что здесь нужно (anchor при РЕАЛЬНО состоявшемся росте).

Поэтому measure_scale_change() — единственная, чисто измерительная функция
— подменена детерминированной заглушкой (тот же приём, что и fake_flow_
predict/fake_match в test_identity_uncertain.py), а весь код ПОСЛЕ неё —
resize по накопленному template_scale_acc, sync_template_metadata,
_adapt_template_base, gate по _template_adaptation_gate — выполняется БЕЗ
единой подмены, тем же путём, что и в полёте."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

_real_measure_scale_change = t.measure_scale_change


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

assert t.SIZE_ADAPT_ENABLED, "тест сам по себе негоден без SIZE_ADAPT_ENABLED"
assert t.SIZE_BY_SCALE_ENABLED, "тест рассчитан именно на ветку measure_scale_change"


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


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.measure_scale_change = _real_measure_scale_change
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


def fake_growth(gray, cx, cy):
    """Детерминированный 'рост' — та же сцена, только измерение масштаба
    стабильно говорит 'крупнее' (k_scale чуть выше 1.0 на каждой попытке).
    Реальный score/match НЕ подменяются — они настоящие, со static-сцены,
    поэтому анти-самоходный тормоз (score деградирует -> рост запрещён,
    см. tracker.py у SIZE_GROW_SCORE_PADENIE) не срабатывает: score
    остаётся стабильно высоким кадр к кадру, расти РЕАЛЬНО можно."""
    return 1.05


capture()
assert t._identity_anchor_gray is not None, "тест сам по себе негоден"
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_tmpl_ref_start = t.template_gray.copy()
_lock_w_start = t.lock_w

print("=== Детерминированный рост цели (measure_scale_change застаблен) "
      "===")
t.measure_scale_change = fake_growth
N_FRAMES = 400
_anchor_changed_any = False
for i in range(N_FRAMES):
    tick()
    if t._match_dbg.get("identity_anchor_changed") == 1:
        _anchor_changed_any = True
    assert t._identity_anchor_gray is _anchor_ref, (
        "_identity_anchor_gray стал другим объектом на кадре %d "
        "(lock_w=%.1f)" % (i, t.lock_w))
    assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
        "байты _identity_anchor_gray изменились на кадре %d (lock_w=%.1f)"
        % (i, t.lock_w))
    assert t.track_state == t.TRACK_STATE_TRACKED, (
        "track_state=%r на кадре %d — сценарий должен был остаться "
        "TRACKED на всём протяжении роста" % (t.track_state, i))
t.measure_scale_change = _real_measure_scale_change

print("    track_state все %d кадров — TRACKED" % N_FRAMES)
print("    lock_w: %.1f -> %.1f (рост в %.2f×)"
      % (_lock_w_start, t.lock_w, t.lock_w / _lock_w_start))
assert not _anchor_changed_any, (
    "identity_anchor_changed=1 всплыл хотя бы раз за %d кадров роста цели"
    % N_FRAMES)

print("\n=== Контроль: сценарий не тривиален — живое представление "
      "РЕАЛЬНО подстроилось под рост (иначе тест ничего не проверяет) ===")
# Рост НАМЕРЕННО медленный и НЕ монотонный каждый кадр — это не вялость
# теста, а работающий анти-самоходный тормоз (см. SIZE_GROW_SCORE_PADENIE
# в tracker.py, тот же механизм, что test_rost_ramki.py проверяет отдельно):
# реальный score от РЕАЛЬНОГО template_match_locked слегка проседает после
# нескольких resize подряд (интерполяция не бесплатна), и рост сам себя
# притормаживает сериями, пока score не отыграет назад. Поэтому порог
# нетривиальности взят скромным (rост реален, но не обязан быть быстрым) —
# а более сильное, однозначное доказательство ниже: РЕАЛЬНЫЙ resize формы.
assert t.lock_w > _lock_w_start * 1.08, (
    "lock_w почти не вырос (%.1f -> %.1f) — measure_scale_change застаблен "
    "на постоянный рост, но код SIZE_ADAPT почему-то не применил его: тест "
    "нужно свериться с текущей веткой SIZE_BY_SCALE_ENABLED в tracker.py"
    % (_lock_w_start, t.lock_w))
assert t.template_gray.shape != _tmpl_ref_start.shape, (
    "template_gray ни разу не изменил РАЗМЕР за весь прогон роста (остался "
    "%r) — однозначное доказательство, что resize по накопленному "
    "template_scale_acc не происходил вовсе" % (t.template_gray.shape,))
print("    lock_w вырос в %.2f× (%.1f -> %.1f), template_gray реально "
      "перестроен под новый размер (resize от template_base + "
      "sync_template_metadata) — SIZE_ADAPT был РЕАЛЬНО активен, и ВСЁ "
      "РАВНО anchor не тронут ни разу"
      % (t.lock_w / _lock_w_start, _lock_w_start, t.lock_w))

print("\n=== identity_anchor_change_reason пуст на всём протяжении (не "
      "унаследовал причину с самого первичного захвата) ===")
assert t._match_dbg.get("identity_anchor_change_reason") == "", (
    "identity_anchor_change_reason=%r — должен быть пуст на обычном кадре "
    "роста цели" % t._match_dbg.get("identity_anchor_change_reason"))
print("    identity_anchor_change_reason == '' — подтверждено")

print("\nOK: цель выросла в кадре в %.2f× (lock_w), живой template_gray "
      "реально пересобран под новый размер (resize от template_base, "
      "sync_template_metadata, template_adaptation_gate) через РЕАЛЬНЫЙ "
      "код SIZE_ADAPT (застаблено только само измерение k_scale), но "
      "_identity_anchor_gray не изменился ни объектом, ни байтами ни разу "
      "за %d кадров — рост размера остаётся работой живого представления, "
      "не новым подтверждением identity." % (t.lock_w / _lock_w_start, N_FRAMES))
