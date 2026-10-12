"""Поток не путает масштаб со сдвигом на несимметричном облаке точек
(ТЗ next-commit spec §7).

СЦЕНАРИЙ БАГА. Приближающаяся цель одновременно растёт и (иногда) реально
смещается. Если фактура есть только с одной стороны цели — типичный
случай, не выдумка, — точки потока лежат кучно с этой стороны. При чистом
росте (истинный сдвиг центра равен НУЛЮ) каждая точка справа от центра
уезжает ВПРАВО пропорционально своему расстоянию до центра; если все точки
справа, старая формула (медиана new-old) видит только этот однонаправленный
эффект и показывает сдвиг центра ВПРАВО, хотя он не двигался.

Тест: точки только с ПРАВОЙ стороны истинного центра, между кадрами —
чистый масштаб (без переноса). Прежняя медианная оценка обязана дать
заметный ложный сдвиг направо (это здесь же и показано, отдельным прямым
вызовом cv2, без изменения tracker.py — только для наглядности сравнения).
Новая flow_predict обязана вернуть центр, который практически не сдвинулся.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

C0X, C0Y = 160.0, 120.0
# 8% за кадр — заметно быстрее типичного сближения (~3-5% в СЕКУНДУ по
# замерам borта, то есть в десятки раз меньше за один кадр), но ещё в
# пределах, где LK с реальными боевыми параметрами (FLOW_LEVELS=1,
# FLOW_ITERS=8 — они экономят CPU, а не гонятся за точностью на большом
# смещении) уверенно доводит все точки: при более резком скачке LK сам
# теряет часть точек, и тест начинает мерить сходимость LK, а не разделение
# сдвига и масштаба, которое здесь проверяется.
SCALE = 1.08
PATCH_X0, PATCH_X1, PATCH_Y = 10, 50, 25


def make_frame(seed=0):
    """Плоский фон + текстура ТОЛЬКО справа от центра — асимметрия облака
    точек получается естественно, через goodFeaturesToTrack, а не руками."""
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    # Текстурная область строго справа от C0X, чтобы goodFeaturesToTrack не
    # нашёл ни одной точки слева.
    x0, x1 = int(C0X) + PATCH_X0, min(t.LORES_W, int(C0X) + PATCH_X1)
    y0, y1 = max(0, int(C0Y) - PATCH_Y), min(t.LORES_H, int(C0Y) + PATCH_Y)
    patch = (rng.random((y1 - y0, x1 - x0)) * 255).astype(np.uint8)
    patch = cv2.GaussianBlur(patch, (3, 3), 0)
    frame[y0:y1, x0:x1] = patch
    return frame


prev = make_frame(1)
# Чистый масштаб SCALE относительно (C0X, C0Y): M отображает (x,y) ->
# (cx,cy) + SCALE*((x,y)-(cx,cy)) — истинный центр НЕ смещается.
M = np.float32([[SCALE, 0, C0X * (1 - SCALE)],
                [0, SCALE, C0Y * (1 - SCALE)]])
cur = cv2.warpAffine(prev, M, (t.LORES_W, t.LORES_H))

pts = cv2.goodFeaturesToTrack(prev, maxCorners=25, qualityLevel=0.01,
                              minDistance=3, blockSize=5)
assert pts is not None and len(pts) >= 8, (
    "тест сам по себе негоден: слишком мало точек нашлось в текстурной "
    "области")
mean_rx = float(np.mean(pts[:, 0, 0] - C0X))
print("точек: %d, среднее смещение облака от центра по X: %.1f px"
      % (len(pts), mean_rx))
assert mean_rx > 15.0, (
    "облако точек оказалось недостаточно асимметричным (%.1f px) — тест "
    "не проверяет то, что должен" % mean_rx)

print("\n=== Для сравнения: прежняя формула (медиана new-old) напрямую ===")
# Те же параметры LK, что боевые (FLOW_WIN/FLOW_LEVELS/FLOW_ITERS) — иначе
# сравнение старой и новой формулы измеряло бы разницу настроек LK, а не
# разницу формул.
nxt, st, err = cv2.calcOpticalFlowPyrLK(
    prev, cur, pts, None, winSize=(t.FLOW_WIN, t.FLOW_WIN),
    maxLevel=t.FLOW_LEVELS,
    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
              t.FLOW_ITERS, 0.03))
st = st.reshape(-1).astype(bool)
old_ = pts[st].reshape(-1, 2)
new_ = nxt[st].reshape(-1, 2)
keep = err[st].reshape(-1) < t.FLOW_ERR_MAX
old_, new_ = old_[keep], new_[keep]
old_dx = float(np.median(new_[:, 0] - old_[:, 0]))
old_dy = float(np.median(new_[:, 1] - old_[:, 1]))
print("    старая формула: ложный сдвиг (%.1f, %.1f) px при истинном (0, 0)"
      % (old_dx, old_dy))
assert abs(old_dx) > 1.5, (
    "тест должен ПОКАЗЫВАТЬ баг старой формулы на этом облаке точек, а "
    "ложный сдвиг оказался мал (%.2f px) — сценарий недостаточно острый"
    % old_dx)

print("\n=== Новая flow_predict: сдвиг+масштаб совместно ===")
t.prev_gray = prev
ok, new_cx, new_cy = t.flow_predict(prev, cur, pts.astype(np.float32),
                                    C0X, C0Y)
err_px = math.hypot(new_cx - C0X, new_cy - C0Y)
print("    flow_predict: ok=%s центр=(%.2f, %.2f) истина=(%.1f, %.1f) "
      "ошибка=%.2f px" % (ok, new_cx, new_cy, C0X, C0Y, err_px))
print("    _flow_dbg: points=%s lk_ok=%s inliers=%s quality=%.2f"
      % (t._flow_dbg.get("points"), t._flow_dbg.get("lk_ok"),
         t._flow_dbg.get("inliers"), t._flow_dbg.get("quality", 0.0)))
assert ok, "поток не сошёлся — тест сам по себе негоден"
assert err_px < 2.0, (
    "новая оценка всё равно поймала ложный сдвиг %.2f px при истинном "
    "нуле — совместная подгонка не разделила масштаб и перенос" % err_px)
assert err_px < abs(old_dx) / 2.0, (
    "новая оценка (%.2f px) не заметно точнее старой (%.2f px) — фикс не "
    "даёт обещанного улучшения" % (err_px, old_dx))

print("\nOK: асимметричное облако точек при чистом росте не даёт "
      "систематического сдвига центра")
