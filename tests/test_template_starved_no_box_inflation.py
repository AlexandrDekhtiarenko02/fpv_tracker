"""TEMPLATE_STARVED больше не раздувает target box (найдено оператором на
реальных стендовых логах 2f7d275).

БАГ. Ветка `if template_std < TEMPLATE_STARVED_STD:` умножала lock_w/
lock_h на TEMPLATE_STARVED_GROW=1.30 до предела TEMPLATE_STARVED_MAX_X=
3.0×, обещая "растём, пока край объекта не попадёт в эталон". Но
TEMPLATE_RESCALE_ON_SIZE_CHANGE=False — эталон физически НЕ растёт: в
ветке ниже, где cur_tmpl.shape != template_gray.shape, cur_tmpl молча
выбрасывается. Получалась ловушка:
    box:      24 → 32 → 40 → 53 → 69/72
    template: 24 → 24 → 24 → 24 → 24 (навсегда)
На реальном логе это видно построчно. Маленький 24×24 template гулял
внутри огромной 72×72 box, matcher стабильно давал почти равные пики
(identity_ambiguous), anchor_score постепенно падал ниже MATCH_GOOD_
SCORE — и захват уходил в UNCERTAIN.

ФИКС. Ветка сохранена как ДИАГНОСТИКА (size_skip=6/7 продолжают писаться
в CSV), но lock_w/lock_h больше НЕ мутируются. Низкий template_std сам
по себе не является измерением размера цели.
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

CX, CY = t.LORES_W // 2, t.LORES_H // 2


def scene_uniform_interior():
    """Однотонный slightly-brighter квадрат — build_template по нему
    вернёт эталон с std ~ 0. Ровно тот случай, где ветка STARVED должна
    сработать."""
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cv2.rectangle(frame, (CX - 12, CY - 12), (CX + 12, CY + 12), 130, -1)
    return frame


def setup_starved_state():
    """Прямая инъекция состояния (минуя acquisition) — иначе acquisition
    ambiguity-reject завернёт заведомо однородную цель. Проверяем ИМЕННО
    STARVED-ветку в TRACKED, не acquisition."""
    scene0 = scene_uniform_interior()
    lock_wh = 12.0
    t.reset_tracking(to_acq=False)
    t.lock_cx, t.lock_cy = float(CX), float(CY)
    t.lock_w, t.lock_h = lock_wh, lock_wh
    t.lock_w0, t.lock_h0 = lock_wh, lock_wh
    tmpl = t.build_template(scene0, CX, CY, lock_wh, lock_wh)
    t.template_gray = tmpl
    t.sync_template_metadata()
    t.template_base = tmpl.copy()
    t._commit_confirmed_identity(tmpl, float(CX), float(CY), "test_direct")
    t.prev_pts = t.refresh_flow_points(scene0, CX, CY, lock_wh, lock_wh)
    t.prev_gray = scene0.copy()
    t.track_state = t.TRACK_STATE_TRACKED
    with t.state_lock:
        t.target_controllable = True
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    return scene0


scene0 = setup_starved_state()

assert t.template_std < t.TEMPLATE_STARVED_STD, (
    "тест сам по себе негоден: template_std=%.2f >= STARVED_STD=%s — "
    "starved-ветка не сработает" % (t.template_std, t.TEMPLATE_STARVED_STD))

_lock_w0 = t.lock_w
_tmpl_shape0 = t.template_gray.shape
print("=== 1. Настройка: lock_w=%.1f template.shape=%s template_std=%.2f "
      "(< STARVED_STD=%s) — starved-ветка должна теперь ТОЛЬКО писать "
      "size_skip, но НЕ мутировать lock_w/lock_h ==="
      % (_lock_w0, _tmpl_shape0, t.template_std, t.TEMPLATE_STARVED_STD))

_size_skips = []
_lock_widths = []
N_FRAMES = 100
for i in range(N_FRAMES):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene0)
    _size_skips.append(t._match_dbg.get("size_skip"))
    _lock_widths.append(t.lock_w)

print("    финальный lock_w=%.1f template.shape=%s" %
      (t.lock_w, t.template_gray.shape))

assert t.lock_w == _lock_w0, (
    "lock_w вырос с %.1f до %.1f за %d кадров с template_std<STARVED_STD "
    "— starved-ветка мутирует box, чего фикс явно запрещает" %
    (_lock_w0, t.lock_w, N_FRAMES))
assert t.lock_h == _lock_w0, (
    "lock_h вырос — та же ошибка на другой оси")
assert t.template_gray.shape == _tmpl_shape0, (
    "template физически изменился, хотя цель полностью однородна и "
    "матч должен молча пропускать пересборку (TEMPLATE_RESCALE_ON_SIZE_"
    "CHANGE=False)")
print("    OK: lock_w/lock_h остались %.1f на протяжении %d кадров — "
      "starved-ловушка (box→3×, template=const) не воспроизвелась" %
      (_lock_w0, N_FRAMES))

print("\n=== 2. Контроль: диагностика starved-ветки продолжает "
      "работать (size_skip=6 или 7 когда starved, разные когда нет) ===")
# Гоняем достаточно кадров, чтобы примерка масштаба точно попалась.
_size_skip_starved = sum(1 for s in _size_skips if s in (6, 7))
_size_skip_other = sum(1 for s in _size_skips if s is not None and s not in (6, 7))
print("    size_skip=6/7 (starved) на %d кадрах из %d, size_skip другие "
      "значения — %d кадров" % (_size_skip_starved, N_FRAMES, _size_skip_other))
assert _size_skip_starved >= 1, (
    "size_skip НИ РАЗУ не был 6/7 — диагностика starved-ветки исчезла "
    "вместе с мутацией, чего фикс НЕ должен был сделать")

t.reset_tracking(to_acq=False)

print("\nOK: TEMPLATE_STARVED больше не раздувает target box до 3× (та "
      "самая ловушка box=72/template=24 из реальных стендовых логов). "
      "Диагностика (size_skip=6/7) сохранена — offline-разбор реальных "
      "логов по-прежнему может отличить 'template безлик' от других "
      "причин пропуска примерки. Проблема 'template starved' теперь "
      "решается на этапе acquisition, а не эскалацией box в рантайме.")
