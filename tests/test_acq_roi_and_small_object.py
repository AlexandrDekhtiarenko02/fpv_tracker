"""TEST A/B/C/D (разбор оператора): сужение ACQ_SNAP_RADIUS_MAIN 60->36 и
small-object detector — через ПОЛНЫЙ реальный pipeline (AUX4 -> estimate_
initial_target -> _nayti_kandidata_acquisition -> process_locked_tracker),
с РЕАЛЬНЫМ текущим ACQ_SNAP_RADIUS_LORES и ОБОИМИ детекторами одновременно
активными (не изолированными, в отличие от других файлов этой правки,
которые нарочно гасят small-object детектор ради чистоты своих decision-
тестов) — этот файл специально проверяет их совместную работу.

ОТЛИЧИЕ ОТ test_zona_poiska.py: тот проверяет _nayti_pyatno как ЧИСТУЮ
функцию с искусственным ACQ_SNAP_RADIUS_LORES=30 (изолированный namespace,
собственный extraction из исходника) — не зависит от того, что реально
стоит в ACQ_SNAP_RADIUS_MAIN сейчас, и не видит small-object детектор
вовсе. Этот файл — наоборот: реальные константы, реальный merge, реальный
process_locked_tracker, обе задачи (selection: выбор среди нескольких
видимых; sensitivity: мелкая цель вообще замечена) разделены по секциям
(п.10 отчёта — не лечить оба одним изменением).

A (sensitivity): цель чуть шире крестика -> появляется candidate.
B (selection): маленькая цель + яркий контрастный объект НА РАССТОЯНИИ,
   которое было внутри СТАРОЙ зоны (~30 lores), но снаружи НОВОЙ (~18) ->
   выбирается маленькая цель, не декой.
C (selection): несколько объектов в кадре, только один близко к крестику
   -> выбирается ближайший допустимый.
D (selection): candidate вне ROI, внутри ROI вообще ничего -> отказ, БЕЗ
   fallback на дальний объект.
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

CX, CY = t.CENTER_X_LORES, t.CENTER_Y_LORES
R = t.ACQ_SNAP_RADIUS_LORES
assert t.ACQ_SNAP_SMALL_ENABLED, "тест сам по себе негоден без small-object детектора"
print("=== Реальная зона захвата сейчас: ACQ_SNAP_RADIUS_LORES=%d ===" % R)


def noisy_bg(seed):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    f = frame.astype(np.float32) + rng.normal(0, 3.0, frame.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def capture(scene):
    t.reset_tracking(to_acq=False)
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    return t.track_state == t.TRACK_STATE_TRACKED


print("\n=== A (sensitivity). Цель чуть шире крестика — появляется "
      "candidate ===")
found = 0
N = 20
for seed in range(N):
    frame = noisy_bg(seed)
    tx, ty = CX + 2, CY - 1
    cv2.circle(frame, (tx, ty), 4, 40, -1)   # радиус 4 px — едва шире точки прицела
    ok = capture(frame)
    dist = ((t.lock_cx - tx) ** 2 + (t.lock_cy - ty) ** 2) ** 0.5 if ok else None
    if ok and dist is not None and dist < 5.0:
        found += 1
    else:
        print("    seed=%d: ok=%s dist=%s winner=%s" % (
            seed, ok, dist, t._match_dbg.get("acq_winner_detector")))
assert found == N, (
    "маленькая цель (r=4px) не найдена в %d случаях из %d — sensitivity "
    "problem всё ещё не закрыта" % (N - found, N))
print("    %d/%d: маленькая цель у самого крестика найдена и захвачена" % (found, N))

print("\n=== B (selection). Маленькая цель внутри НОВОЙ зоны + яркий "
      "декой на расстоянии, которое было внутри СТАРОЙ (~30 lores) зоны, "
      "но снаружи НОВОЙ (~%d) — побеждает маленькая цель ===" % R)
found_b = 0
decoy_dist = R + 8
dx, dy = int(decoy_dist * 0.9), int(decoy_dist * 0.3)
for seed in range(N):
    frame = noisy_bg(seed)
    small_x, small_y = CX + 5, CY - 3
    cv2.circle(frame, (small_x, small_y), 5, 60, -1)
    cv2.circle(frame, (CX + dx, CY + dy), 12, 250, -1)   # ярче, крупнее, но вне новой зоны
    ok = capture(frame)
    dist_small = ((t.lock_cx - small_x) ** 2 + (t.lock_cy - small_y) ** 2) ** 0.5 if ok else None
    if ok and dist_small is not None and dist_small < 6.0:
        found_b += 1
    else:
        print("    seed=%d: ok=%s dist_small=%s lock=%s" % (
            seed, ok, dist_small, (t.lock_cx, t.lock_cy) if ok else None))
assert found_b == N, (
    "яркий декой вне новой зоны выиграл выбор в %d случаях из %d — "
    "сужение ROI не защищает от соседних объектов" % (N - found_b, N))
print("    %d/%d: маленькая цель выиграла, декой на расстоянии %d "
      "(внутри старой ~30, снаружи новой %d) корректно не участвовал"
      % (found_b, N, decoy_dist, R))

print("\n=== C (selection). Несколько объектов в кадре, только один "
      "близко к крестику — выбирается ближайший допустимый ===")
found_c = 0
near_x, near_y = CX + 6, CY + 4
for seed in range(N):
    frame = noisy_bg(seed)
    cv2.circle(frame, (near_x, near_y), 6, 50, -1)
    cv2.circle(frame, (CX - 80, CY - 60), 14, 240, -1)
    cv2.circle(frame, (CX + 100, CY + 50), 14, 30, -1)
    ok = capture(frame)
    dist_near = ((t.lock_cx - near_x) ** 2 + (t.lock_cy - near_y) ** 2) ** 0.5 if ok else None
    if ok and dist_near is not None and dist_near < 6.0:
        found_c += 1
    else:
        print("    seed=%d: ok=%s dist_near=%s" % (seed, ok, dist_near))
assert found_c == N, (
    "ближайший к крестику объект не выиграл в %d случаях из %d, хотя два "
    "других объекта заведомо дальше и вне зоны" % (N - found_c, N))
print("    %d/%d: ближайший к крестику объект выигрывает, дальние "
      "(вне зоны) не участвуют" % (found_c, N))

print("\n=== D (selection). Candidate вне ROI, внутри ROI вообще ничего "
      "— отказ БЕЗ fallback на дальний объект ===")
rejected_d = 0
far_dist = R + 15
for seed in range(N):
    frame = noisy_bg(seed)
    cv2.circle(frame, (CX + far_dist, CY), 12, 250, -1)
    ok = capture(frame)
    if not ok:
        rejected_d += 1
    else:
        print("    seed=%d: ok=True (ожидали отказ), lock=%s count=%s"
              % (seed, (t.lock_cx, t.lock_cy), t._match_dbg.get("acq_candidate_count")))
assert rejected_d == N, (
    "объект вне ROI (%d px, зона %d) всё же был захвачен в %d случаях из "
    "%d — fallback на объект снаружи зоны существует, хотя не должен"
    % (far_dist, R, N - rejected_d, N))
print("    %d/%d: единственный объект строго вне зоны -> честный отказ "
      "каждый раз, никакого fallback" % (rejected_d, N))

print("\nOK: sensitivity (A) и selection (B/C/D) проверены раздельно через "
      "полный реальный pipeline с текущим ACQ_SNAP_RADIUS_LORES=%d и обоими "
      "детекторами одновременно активными — мелкая цель у крестика находится, "
      "яркие/крупные объекты вне новой узкой зоны больше не перебивают её, "
      "полное отсутствие candidate внутри зоны остаётся честным отказом." % R)
