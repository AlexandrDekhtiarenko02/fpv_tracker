"""Реальный рост масштаба ТОЙ ЖЕ цели не должен ложно триггерить
IDENTITY_UNCERTAIN через периодическую anchor-сверку (найдено оператором:
test_identity_anchor_scaling.py фейкал measure_scale_change(), но пиксели
объекта в сцене физически не менялись — anchor (снятый с исходного
размера) естественно проходил проверку против НЕИЗМЕНИВШЕГОСЯ реального
объекта; тест не проверял то, что заявлял).

ЭТОТ файл — объект ФИЗИЧЕСКИ растёт в кадре (один и тот же источник
текстуры, cv2.resize к каждому новому размеру, реальные пиксели меняются
по-настоящему), позиция и identity не меняются. flow_predict и
template_match_locked — РЕАЛЬНЫЕ (не застаблены): секция 1 просто
наблюдает, что делает уже существующая (не переписанная в этой правке)
логика слежения/SIZE_ADAPT сама по себе, секция 2 проверяет заявленное
оператором свойство — отсутствие ложного срабатывания в РЕАЛИСТИЧНОМ
диапазоне роста.

_shadow_match_against_template НЕ scale-invariant (matchTemplate без
масштабного перебора) — anchor держит РАЗМЕР МОМЕНТА ЗАХВАТА навсегда.
Вопрос не "инвариантна ли функция к масштабу" (заведомо нет), а "где
именно на практике пролегает граница и остаётся ли она за пределами
реалистичного роста цели за время между подтверждениями".
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

assert t.IDENTITY_ANCHOR_CHECK_ENABLED, (
    "тест сам по себе негоден без IDENTITY_ANCHOR_CHECK_ENABLED")

CX, CY = t.LORES_W // 2, t.LORES_H // 2
rng = np.random.default_rng(777)
_FON = np.clip(120 + cv2.GaussianBlur(
    rng.normal(0, 12, (t.LORES_H, t.LORES_W)).astype(np.float32),
    (0, 0), 1.4), 0, 255).astype(np.uint8)
# ОДИН источник текстуры на разрешении с запасом сверху — resize вниз
# (INTER_AREA-подобное сглаживание через LINEAR тут не критично, важна не
# фотореалистичность, а то, что это РЕАЛЬНО одна и та же структура на
# разных физических размерах, не подмена).
_SRC_SIZE = 110
_SRC = (rng.random((_SRC_SIZE, _SRC_SIZE)) * 90 + 110).astype(np.uint8)
cv2.circle(_SRC, (_SRC_SIZE // 2, _SRC_SIZE // 2), _SRC_SIZE // 3, 40, -1)
cv2.line(_SRC, (4, 4), (_SRC_SIZE - 4, _SRC_SIZE - 4), 20, 3)
R0 = 24   # размер в момент захвата


def scene_at_size(sz):
    g = _FON.copy()
    obj = cv2.resize(_SRC, (sz, sz), interpolation=cv2.INTER_LINEAR)
    y0, x0 = CY - sz // 2, CX - sz // 2
    g[y0:y0 + sz, x0:x0 + sz] = obj
    return g


def capture(sz):
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene_at_size(sz))
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def tick(sz):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene_at_size(sz))


capture(R0)
_anchor_ref = t._identity_anchor_gray
print("    anchor установлен размером %dx%d (захват на scene-объекте %dpx)"
      % (t._identity_anchor_w, t._identity_anchor_h, R0))

print("=== 1. Реалистичный рост: R0=%d -> 2xR0=%d px (типичный масштаб "
      "сближения), шаг каждые несколько кадров — НИ РАЗУ ложного "
      "IDENTITY_UNCERTAIN ===" % (R0, R0 * 2))
SIZES_REALISTIC = list(range(R0, R0 * 2 + 1, 4))
N_PER_SIZE = 15
_scores_seen = []
_streak_seen = []
for sz in SIZES_REALISTIC:
    for _ in range(N_PER_SIZE):
        tick(sz)
    _scores_seen.append((sz, t._match_dbg.get("identity_anchor_check_score")))
    _streak_seen.append(t._match_dbg.get("identity_anchor_check_streak"))
    assert t.track_state == t.TRACK_STATE_TRACKED, (
        "track_state=%r на размере %dpx (%.2fx от захвата) — ложное "
        "срабатывание в РЕАЛИСТИЧНОМ диапазоне роста"
        % (t.track_state, sz, sz / R0))
    with t.state_lock:
        _controllable_now = t.target_controllable
    assert _controllable_now, (
        "controllable=False на размере %dpx (%.2fx) — soft distrust "
        "ложно сработал в реалистичном диапазоне" % (sz, sz / R0))
print("    identity_anchor_check_score по размерам:",
      ["%dpx=%.2f" % (sz, sc) if sc is not None else "%dpx=?" % sz
       for sz, sc in _scores_seen])
assert all(s == 0 for s in _streak_seen), (
    "identity_anchor_check_streak вырос хотя бы раз до 2x роста: %s"
    % _streak_seen)
print("    OK: рост до 2x (типичное сближение) — anchor_check_streak "
      "остаётся 0 на каждом замере, controllable ни разу не снялся")

print("\n=== 2. Контроль неvacuous: anchor_check РЕАЛЬНО замерял (не "
      "просто ни разу не запускался за отведённое время) ===")
_ran_count = sum(1 for sz, sc in _scores_seen if sc is not None)
assert _ran_count >= len(SIZES_REALISTIC) // 2, (
    "anchor_check слишком редко реально запускался (%d из %d контрольных "
    "точек) — секция 1 могла тривиально пройти просто не проверив ничего"
    % (_ran_count, len(SIZES_REALISTIC)))
print("    anchor_check реально сработал на %d из %d контрольных точек"
      % (_ran_count, len(SIZES_REALISTIC)))

print("\n=== 3. СПРАВОЧНО (не production-порог, не предмет исправления в "
      "этой правке) — где именно на практике пролегает граница: "
      "продолжаем рост ЗА пределы реалистичного диапазона, смотрим на "
      "реальные числа ===")
_break_size = None
_break_ratio = None
for sz in range(R0 * 2, R0 * 5, 4):
    for _ in range(N_PER_SIZE):
        tick(sz)
    score = t._match_dbg.get("identity_anchor_check_score")
    print("    size=%dpx (%.2fx) score=%s streak=%s state=%s"
          % (sz, sz / R0, score, t._match_dbg.get("identity_anchor_check_streak"),
             t.track_state))
    if t.track_state != t.TRACK_STATE_TRACKED:
        _break_size = sz
        _break_ratio = sz / R0
        break
if _break_size is not None:
    print("    граница (первое отклонение от TRACKED): %dpx, %.2fx от "
          "размера захвата" % (_break_size, _break_ratio))
    assert _break_ratio > 2.5, (
        "граница ложного срабатывания оказалась ВНУТРИ реалистичного "
        "диапазона (%.2fx) — секция 1 должна была это поймать первой; "
        "если видите этот assert, порог реально сдвинулся и тест выше "
        "негоден, не полагайтесь только на это число"
        % _break_ratio)
else:
    print("    ни разу не покинул TRACKED даже на 5x исходного размера — "
          "граница (если есть) ещё дальше")

t.reset_tracking(to_acq=False)

print("\nOK: реальный физический рост цели до 2x исходного размера "
      "захвата (типичное сближение) НЕ вызывает ложного IDENTITY_"
      "UNCERTAIN и НЕ снимает controllable — anchor-check остаётся "
      "надёжным в реалистичном диапазоне. Справочно: на этой конкретной "
      "текстуре реальная граница обнаружена заметно дальше (см. секцию "
      "3) — если понадобится её отодвинуть ещё дальше, это отдельная, "
      "осознанная правка (например multi-scale сверка), не предмет "
      "текущей.")
