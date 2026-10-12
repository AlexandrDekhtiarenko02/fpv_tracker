"""OFFLINE/SHADOW: 320x240 (текущая acquisition) vs 640x480 (main) на ТОЙ ЖЕ
сцене (разбор оператора, п.4: "проверить acquisition на более высоком
разрешении").

НЕ ТЕСТ (нет pass/fail, не входит в tests/test_*.py и не участвует в
общем прогоне). Не переводит live-control на high-res — это только сбор
данных: помогает ли более высокое разрешение видеть мелкую цель, которую
на 320x240 теряет sensitivity acquisition, или проблема в масштабе самого
детектора (sigma_melko/krupno), а не в разрешении кадра.

МЕТОД. Один и тот же логический кадр строится СРАЗУ на 640x480 (объект
рисуется в НАТИВНОМ разрешении, без апскейла), затем приводится к 320x240
тем же способом, что и реальный RECORD_HIRES-путь в camera_callback
(cv2.resize с INTER_AREA — см. tracker.py). Детектор (_nayti_vershiny_v_
zone — ЯДРО, общее у normal и small-object путей, не переписан) гоняется
на обоих кадрах: на 320x240 с текущими ACQ_SNAP_RADIUS_LORES/sigma, на
640x480 — с ТЕМИ ЖЕ отношениями (радиус и sigma умножены на 2, вслед за
разрешением), через временную подмену CENTER_X_LORES/CENTER_Y_LORES/
ACQ_SNAP_RADIUS_LORES (восстанавливается после каждого прогона).

ПОРОГИ (ACQ_SNAP_MIN_ABS/OTN) НЕ ТРОГАЮТСЯ — те же числа на обоих
разрешениях, прямое требование отчёта (не путать resolution/sensitivity
эксперимент с подбором production-порога).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

MAIN_W, MAIN_H = t.MAIN_W, t.MAIN_H
LORES_W, LORES_H = t.LORES_W, t.LORES_H
SCALE = MAIN_W / LORES_W   # 2.0 по умолчанию
MAIN_CX, MAIN_CY = MAIN_W // 2, MAIN_H // 2

_orig_center_x = t.CENTER_X_LORES
_orig_center_y = t.CENTER_Y_LORES
_orig_radius = t.ACQ_SNAP_RADIUS_LORES


def make_main_scene(radius_px, seed, contrast=90):
    """Объект НАТИВНО на 640x480 (не апскейл с 320x240) — radius_px в
    единицах MAIN, значит эквивалент radius_px/SCALE на lores. Контраст и
    шум подобраны так, чтобы на самых мелких радиусах результат не был
    тривиальным 8/8 на обоих разрешениях — иначе таблица ничего не
    показывает: тут не production-сцена, а инструмент для поиска
    ГРАНИЦЫ, где сходство с реальным слабым/мелким объектом начинает
    ломаться."""
    rng = np.random.default_rng(seed)
    frame = np.full((MAIN_H, MAIN_W), 120, np.uint8)
    f = frame.astype(np.float32) + rng.normal(0, 7.0, frame.shape)
    frame = np.clip(f, 0, 255).astype(np.uint8)
    cv2.circle(frame, (MAIN_CX + 3, MAIN_CY - 2), radius_px, contrast, -1)
    return frame


def to_lores(main_frame):
    """Тот же метод, что и camera_callback при RECORD_HIRES=True (см.
    tracker.py) — не придуманный отдельно для этого скрипта."""
    return cv2.resize(main_frame, (LORES_W, LORES_H), interpolation=cv2.INTER_AREA)


def probe(gray, radius_scale):
    """radius_scale=1 -> текущие lores-константы как есть; radius_scale=2
    -> временно переключиться на main-эквивалентные (радиус и sigma x2,
    центр в main-координатах), прогнать оба детектора, вернуть диагностику
    и восстановить lores-константы."""
    global _orig_center_x, _orig_center_y, _orig_radius
    if radius_scale != 1:
        t.CENTER_X_LORES = MAIN_CX
        t.CENTER_Y_LORES = MAIN_CY
        t.ACQ_SNAP_RADIUS_LORES = int(round(_orig_radius * radius_scale))
    try:
        cands_n, porog_n, peak_n = t._nayti_vershiny_v_zone(
            gray,
            t.ACQ_SNAP_SIGMA_MELKO * radius_scale,
            t.ACQ_SNAP_SIGMA_KRUPNO * radius_scale,
            t.ACQ_SNAP_MIN_ABS, t.ACQ_SNAP_MIN_OTN,
            int(t.ACQ_SNAP_PEAK_OKNO * radius_scale))
        cands_s, porog_s, peak_s = t._nayti_vershiny_v_zone(
            gray,
            t.ACQ_SNAP_SMALL_SIGMA_MELKO * radius_scale,
            t.ACQ_SNAP_SMALL_SIGMA_KRUPNO * radius_scale,
            t.ACQ_SNAP_SMALL_MIN_ABS, t.ACQ_SNAP_SMALL_MIN_OTN,
            int(t.ACQ_SNAP_SMALL_PEAK_OKNO * radius_scale))
    finally:
        t.CENTER_X_LORES = _orig_center_x
        t.CENTER_Y_LORES = _orig_center_y
        t.ACQ_SNAP_RADIUS_LORES = _orig_radius
    return {
        "normal_found": len(cands_n) > 0, "normal_peak": peak_n, "normal_thr": porog_n,
        "normal_margin": peak_n / max(porog_n, 1e-6),
        "small_found": len(cands_s) > 0, "small_peak": peak_s, "small_thr": porog_s,
        "small_margin": peak_s / max(porog_s, 1e-6),
    }


print("=== OFFLINE RESOLUTION SHADOW: %dx%d (lores, ТЕКУЩАЯ acquisition) "
      "vs %dx%d (main) — те же пороги на обоих, НЕ калибровка, только "
      "данные ===" % (LORES_W, LORES_H, MAIN_W, MAIN_H))
print("%-14s | %-30s | %-30s" % ("target r(main)", "320x240 (lores)", "640x480 (main)"))
print("%-14s | %-8s %-8s %-6s %-6s | %-8s %-8s %-6s %-6s"
      % ("", "n_found", "s_found", "n_mrg", "s_mrg", "n_found", "s_found", "n_mrg", "s_mrg"))

# radius_px в MAIN-единицах: от заведомо крупного до едва различимого.
RADII = [20, 12, 8, 6, 5, 4, 3, 2, 1]
N_SEEDS = 12
for r in RADII:
    lores_rows = []
    main_rows = []
    for seed in range(N_SEEDS):
        main_frame = make_main_scene(r, seed)
        lores_frame = to_lores(main_frame)
        lores_rows.append(probe(lores_frame, 1))
        main_rows.append(probe(main_frame, SCALE))

    def agg(rows, key_found, key_margin):
        found_n = sum(1 for row in rows if row[key_found])
        margins = [row[key_margin] for row in rows]
        return found_n, (sum(margins) / len(margins) if margins else 0.0)

    ln_f, ln_m = agg(lores_rows, "normal_found", "normal_margin")
    ls_f, ls_m = agg(lores_rows, "small_found", "small_margin")
    mn_f, mn_m = agg(main_rows, "normal_found", "normal_margin")
    ms_f, ms_m = agg(main_rows, "small_found", "small_margin")
    print("r=%-10d px | %d/%-6d %d/%-6d %-6.2f %-6.2f | %d/%-6d %d/%-6d %-6.2f %-6.2f"
          % (r, ln_f, N_SEEDS, ls_f, N_SEEDS, ln_m, ls_m,
             mn_f, N_SEEDS, ms_f, N_SEEDS, mn_m, ms_m))

print("\nЧтение таблицы: 'found' — сколько из %d seed'ов дали хотя бы один "
      "candidate (порог margin>=1.0 уже внутри); 'mrg' — средний margin "
      "(raw_peak/threshold), в том числе <1.0 у не найденных — показывает, "
      "НАСКОЛЬКО не хватило, не только факт неудачи." % N_SEEDS)
print("\nИнтерпретация: если на каком-то радиусе lores массово не находит "
      "(found=0), а main massively находит (found=%d) -> проблема "
      "РАЗРЕШЕНИЯ (даунскейл стирает структуру раньше, чем детектор её "
      "видит). Если НЕ находит ни на 320, ни на 640 (found=0 на обоих) -> "
      "проблема МАСШТАБА ДЕТЕКТОРА/порога, не разрешения — увеличение "
      "разрешения тут не поможет." % N_SEEDS)
