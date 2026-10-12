"""Manual nudge НЕ переписывает confirmed identity anchor (отчёт 25.09,
разбор поверх 64949ec, п.G, K-7).

КОНТЕКСТ. reanchor_tracker_at_current_box() вызывается на ОТПУСКАНИИ стика
ручной коррекции и пересобирает ЖИВОЙ template_gray/template_base на новой
позиции — это легитимно и не трогается этой правкой. Вопрос в другом:
первая версия правки заодно звала _commit_confirmed_identity() из этой же
функции, рассуждая "отпускание стика — тоже явное действие пилота". Сам же
автор правки поймал это как самопротиворечие с собственным п.G ДО того, как
получил фидбек ("Не делать silent template_base = new_patch просто потому,
что стик отпущен") — исправлено до коммита (см. докстроку
reanchor_tracker_at_current_box и _commit_confirmed_identity в tracker.py).

Этот файл — тест ИМЕННО этого различия: nudge поправляет, ГДЕ мы считаем
цель находящейся (живой template/box), но НЕ меняет, ЧТО именно мы считаем
целью (_identity_anchor_gray) — anchor остаётся тем же объектом/теми же
байтами, что были установлены на первичном захвате, независимо от того,
сколько раз пилот подвигал рамку стиком.
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
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick(0)
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def nudge_and_release(roll_us, pitch_us, n_frames=5):
    """Отклонить стик на n_frames кадров, затем отпустить (вызывает
    reanchor_tracker_at_current_box на кадре отпускания)."""
    set_stick(roll_us, pitch_us)
    for _ in range(n_frames):
        _clk.tick(FRAME_DT)
        t.process_locked_tracker(scene)
    set_stick(0)
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)   # кадр отпускания — reanchor здесь


capture()
assert t._identity_anchor_gray is not None, (
    "тест сам по себе негоден: после захвата anchor обязан быть установлен")
_anchor_after_capture = t._identity_anchor_gray   # ссылка, НЕ .copy() —
# нужна именно для проверки object identity (`is`) ниже; побайтовая
# проверка делается отдельной .copy()-снимкой ПЕРЕД первым nudge-циклом.
_anchor_bytes_after_capture = t._identity_anchor_gray.copy()
epoch0 = t.geometry_epoch

print("=== 1. Один цикл nudge (отклонить -> отпустить): anchor не "
      "изменился ни объектом, ни байтами; template_gray/geometry_epoch "
      "ИЗМЕНИЛИСЬ (живая коррекция реально сработала) ===")
_tmpl_before = t.template_gray.copy()
ROLL_US = 450.0
nudge_and_release(ROLL_US, 0.0, n_frames=5)
assert t.geometry_epoch == epoch0 + 1, (
    "тест сам по себе негоден: отпускание стика не подняло geometry_epoch "
    "— reanchor не сработал, дальше нечего проверять")
assert not np.array_equal(t.template_gray, _tmpl_before), (
    "тест сам по себе негоден: template_gray не изменился после реального "
    "сдвига рамки стиком — reanchor не пересобрал живой шаблон на новом "
    "месте, значит сценарий не тот, что нужно проверить")
assert t._identity_anchor_gray is _anchor_after_capture, (
    "_identity_anchor_gray стал ДРУГИМ объектом после nudge-release — "
    "anchor переписан ручной коррекцией, хотя это НЕ явное подтверждение "
    "новой identity")
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes_after_capture), (
    "байты _identity_anchor_gray изменились после nudge-release")
assert t._match_dbg.get("identity_anchor_changed") == 0, (
    "identity_anchor_changed=1 на кадре отпускания стика — anchor не "
    "должен был меняться от nudge")
print("    geometry_epoch %d -> %d (reanchor реально случился), "
      "template_gray изменился (живая коррекция сработала), НО "
      "_identity_anchor_gray — тот же объект, те же байты, "
      "identity_anchor_changed=0" % (epoch0, t.geometry_epoch))

print("\n=== 2. Несколько циклов nudge подряд (разные направления) — "
      "anchor остаётся неизменным на протяжении ВСЕХ ===")
for roll, pitch in ((-250.0, 0.0), (0.0, 200.0), (180.0, -180.0)):
    nudge_and_release(roll, pitch, n_frames=3)
    assert t._identity_anchor_gray is _anchor_after_capture, (
        "anchor подменился после ещё одного цикла nudge (roll=%.0f "
        "pitch=%.0f)" % (roll, pitch))
    assert t._match_dbg.get("identity_anchor_changed") == 0
print("    3 дополнительных цикла nudge (разные направления) — anchor "
      "ни разу не подменился")

print("\n=== 3. identity_anchor_changed НИ РАЗУ не всплыл =1 ни на одном "
      "кадре всего эпизода (не только на кадре отпускания) ===")
_events = []
t.flight_log.event = _events.append
set_stick(150.0, -100.0)
for _ in range(8):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    assert t._match_dbg.get("identity_anchor_changed") == 0, (
        "identity_anchor_changed=1 ВО ВРЕМЯ активной коррекции (до "
        "отпускания) — anchor не должен трогаться даже мимоходом")
set_stick(0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("identity_anchor_changed") == 0
_anchor_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
assert not _anchor_events, (
    "событие 'IDENTITY ANCHOR: подтверждена' всё-таки произошло во время "
    "nudge-эпизода: %s" % _anchor_events)
assert t._identity_anchor_gray is _anchor_after_capture
print("    8 кадров активной коррекции + отпускание — ни одного события "
      "'IDENTITY ANCHOR: подтверждена', anchor не тронут")

print("\n=== 4. Явный новый захват (reset_tracking + повторное AUX4) — "
      "ЭТО единственный способ реально сменить anchor, и он его "
      "действительно меняет ===")
t.reset_tracking(to_acq=False)
assert t._identity_anchor_gray is None, (
    "reset_tracking() не очистил _identity_anchor_gray")
capture()
assert t._identity_anchor_gray is not None
assert t._identity_anchor_gray is not _anchor_after_capture, (
    "новый явный захват обязан создать НОВЫЙ anchor-объект")
print("    reset_tracking() + новый явный захват — anchor обновился, "
      "как и должен: это единственный легитимный путь")

print("\nOK: ручная коррекция (любое число циклов отклонить/отпустить) "
      "двигает живую рамку/template_gray и поднимает geometry_epoch, но "
      "НИ РАЗУ не трогает _identity_anchor_gray — ни объектом, ни байтами, "
      "ни диагностикой identity_anchor_changed; единственный способ "
      "реально сменить anchor — явный новый захват после reset_tracking().")
