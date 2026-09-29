"""Одно действие пилота (AUX4 rising) -> немедленный лок на ближайший
кандидат в ROI, БЕЗ отдельного шага подтверждения (отчёт 25.09, разбор
поверх 64949ec, п.A/K-1; прямая цитата ответа пользователя на уточняющий
вопрос: "PROPOSED как пользовательский этап мне не нужен. Желаемый UX: одно
действие запускает acquisition. В ROI вокруг crosshair система
автоматически находит ближайший подходящий визуальный patch и рамка сразу
привязывается к нему").

ОТЛИЧИЕ ОТ test_acq_ambiguity_reject.py §7 И test_zona_poiska.py: там
проверяется либо decision-логика (_nayti_pyatno подменена фиксированным
кортежем), либо сам детектор в изоляции (без process_locked_tracker и
identity anchor вовсе). Здесь — РЕАЛЬНЫЙ _nayti_pyatno (никаких подмен) на
РЕАЛЬНЫХ пикселях, цель НАРОЧНО НЕ под крестиком (dx/dy заметно ненулевые),
через ПОЛНЫЙ process_locked_tracker, ровно одним кадром с поднятым AUX4 —
конечное доказательство того, что весь путь целиком (детектор -> decision
-> lock -> identity anchor) работает за один шаг, а не за несколько.
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

rng = np.random.default_rng(4242)


def make_offset_scene(dx, dy, seed=4242):
    """Текстурная цель ЗАМЕТНО В СТОРОНЕ от крестика (dx, dy px), пустой
    фон вокруг — реальные пиксели, не заглушка _nayti_pyatno."""
    r = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx = t.CENTER_X_LORES + dx
    cy = t.CENTER_Y_LORES + dy
    s = 20
    obj = (r.random((s, s)) * 90 + 110).astype(np.uint8)
    cv2.circle(obj, (s // 2, s // 2), s // 3, 40, -1)
    y0, x0 = int(cy - s / 2), int(cx - s / 2)
    frame[y0:y0 + s, x0:x0 + s] = obj
    return frame


assert t.ACQ_LOCK_AT_CROSSHAIR_EXACTLY, (
    "тест рассчитан именно на ветку _nayti_pyatno/estimate_initial_target")
# Этот файл проверяет K-1 (одно действие -> немедленный лок через ПОЛНЫЙ
# pipeline) на normal-детекторе — small-object детектор (разбор оператора,
# отдельная правка) тут не участвует: на более узкой зоне (после сужения
# ACQ_SNAP_RADIUS_MAIN) он способен честно найти СВОЙ пик на БЛИЖНЕМ К
# КРЕСТИКУ краю той же самой цели (не её геометрический центр, но всё ещё
# часть объекта) и выиграть выбор "ближайший" — тест тогда сравнивал бы
# промах не с тем, что предполагал. У small-object детектора будут свои,
# отдельные тесты.
_orig_small_enabled = t.ACQ_SNAP_SMALL_ENABLED
t.ACQ_SNAP_SMALL_ENABLED = False
# Заметно в стороне (не под крестиком), но надёжно внутри зоны поиска.
OFFSET = int(round(t.ACQ_SNAP_RADIUS_LORES * 0.4))
assert OFFSET >= 5, "тест сам по себе негоден: OFFSET слишком мал"
scene = make_offset_scene(OFFSET, -OFFSET // 2)

print("=== Один кадр с поднятым AUX4 на цели, смещённой от крестика на "
      "(dx=%+d, dy=%+d) px ===" % (OFFSET, -OFFSET // 2))
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
_clk.tick(0.001)
t.process_locked_tracker(scene)   # РОВНО один кадр — не серия попыток

print("    track_state=%s lock=(%.1f,%.1f) acq_candidate_dx=%s "
      "acq_candidate_dy=%s"
      % (t.track_state, t.lock_cx, t.lock_cy,
         t._match_dbg.get("acq_candidate_dx"),
         t._match_dbg.get("acq_candidate_dy")))

assert t.track_state == t.TRACK_STATE_TRACKED, (
    "один кадр с AUX4 на выраженной, но смещённой от крестика цели не "
    "привёл к TRACKED с первой попытки, track_state=%r" % t.track_state)
with t.state_lock:
    _controllable = t.target_controllable
assert _controllable, "controllable не стал True сразу после лока"

expected_cx = t.CENTER_X_LORES + OFFSET
expected_cy = t.CENTER_Y_LORES - OFFSET // 2
miss = ((t.lock_cx - expected_cx) ** 2
        + (t.lock_cy - expected_cy) ** 2) ** 0.5
print("    промах от реального центра цели: %.1f px" % miss)
assert miss < 6.0, (
    "лок сел не на реальную цель (промах %.1f px) — либо детектор нашёл "
    "что-то другое, либо позиция не та" % miss)

dist_from_raw_crosshair = ((t.lock_cx - t.CENTER_X_LORES) ** 2
                            + (t.lock_cy - t.CENTER_Y_LORES) ** 2) ** 0.5
assert dist_from_raw_crosshair > OFFSET * 0.5, (
    "лок сел практически НА сыром крестике (%.1f px от него), хотя цель "
    "была смещена на %d px — значит сработала не 'ближайший кандидат', а "
    "старая 'крестик имеет приоритет' логика" % (dist_from_raw_crosshair, OFFSET))

assert t._match_dbg.get("acq_candidate_present") == 1
assert t._match_dbg.get("acq_candidate_confirmed") == 1
_dx = t._match_dbg.get("acq_candidate_dx")
_dy = t._match_dbg.get("acq_candidate_dy")
assert _dx is not None and abs(_dx - OFFSET) < 6.0, (
    "acq_candidate_dx=%r не соответствует реальному смещению цели (%d)"
    % (_dx, OFFSET))
print("    acq_candidate_dx/dy честно отражают реальное смещение цели от "
      "крестика")

assert t._identity_anchor_gray is not None, (
    "confirmed identity anchor не установлен после единственного успешного "
    "действия — точка B (отдельность confirmed identity) не выполнена")
print("    identity anchor установлен ОДНИМ действием, без отдельного "
      "шага подтверждения")

assert t._match_dbg.get("acq_winner_detector") == "normal", (
    "acq_winner_detector=%r, ожидали 'normal' (small-object детектор "
    "изолирован в этом файле)" % t._match_dbg.get("acq_winner_detector"))

t.ACQ_SNAP_SMALL_ENABLED = _orig_small_enabled

print("\nOK: одно действие AUX4 на реальных пикселях, цель заметно в "
      "стороне от крестика -> немедленный TRACKED на РЕАЛЬНОЙ цели (не на "
      "сыром крестике), controllable=True, identity anchor установлен — "
      "весь путь детектор -> decision -> lock -> anchor работает за один "
      "шаг, без отдельного PROPOSED-этапа.")
