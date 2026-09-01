#!/usr/bin/env python3
"""Проиграть записанные кадры и сравнить на них варианты слежения.

ЗАЧЕМ. Сравнивать прогоны между собой нельзя: разные цели, разный фон, разная
манера водить. На этом мы уже зарезали хорошую ветку и едва не приняли плохую.
По записи же любой вариант прогоняется по ОДНИМ И ТЕМ ЖЕ кадрам, сколько
угодно раз, и сравнение становится честным.

ЗАПУСК:

    python3 tools/replay.py flight_logs/recordings/20260901_180000
    python3 tools/replay.py <запись> --save-frames кадры/     # выгрузить в png

Начальная коробка и точка берутся из описи, снятой на борту, поэтому все
варианты стартуют совершенно одинаково.
"""
import argparse
import csv
import os
import sys

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(prefix):
    meta = {}
    with open(prefix + ".meta.txt", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                meta[k] = v
    w, h = int(meta["width"]), int(meta["height"])
    raw = np.fromfile(prefix + ".gray", dtype=np.uint8)
    n = raw.size // (w * h)
    frames = raw[:n * w * h].reshape(n, h, w)
    rows = []
    with open(prefix + ".index.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return frames, rows, (w, h)


def as_float(row, key):
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_template(frames, rows, margin=24):
    """Нынешний способ: совпадение с сохранённым куском изображения."""
    cx = as_float(rows[0], "box_cx")
    cy = as_float(rows[0], "box_cy")
    bw = as_float(rows[0], "box_w") or 24.0
    if cx is None:
        return None
    # опись в координатах КАДРА (640x480), кадры записаны в 320x240
    sx = frames.shape[2] / 640.0
    cx, cy, bw = cx * sx, cy * sx, bw * sx
    t = int(max(14, min(110, bw)))
    r = t // 2
    y0, x0 = int(cy) - r, int(cx) - r
    tmpl = frames[0][max(0, y0):y0 + t, max(0, x0):x0 + t].copy()
    track = []
    for f in frames:
        s = int(t + margin * 2)
        yy, xx = int(cy) - s // 2, int(cx) - s // 2
        yy = max(0, min(f.shape[0] - s, yy))
        xx = max(0, min(f.shape[1] - s, xx))
        sub = f[yy:yy + s, xx:xx + s]
        if sub.shape[0] < t or sub.shape[1] < t:
            break
        res = cv2.matchTemplate(sub, tmpl, cv2.TM_CCOEFF_NORMED)
        _, val, _, loc = cv2.minMaxLoc(res)
        cx = xx + loc[0] + t / 2.0
        cy = yy + loc[1] + t / 2.0
        track.append((cx, cy, val))
    return track


def summarize(name, track, rows, scale):
    if not track:
        print("  %-22s не отработал" % name)
        return
    drift = []
    for i, (cx, cy, _v) in enumerate(track):
        if i >= len(rows):
            break
        bx, by = as_float(rows[i], "box_cx"), as_float(rows[i], "box_cy")
        if bx is None or by is None:
            continue
        drift.append(((cx - bx * scale) ** 2 + (cy - by * scale) ** 2) ** 0.5)
    sc = [v for _, _, v in track]
    print("  %-22s кадров %4d | совпадение %.3f | расхождение с бортом %.1f px"
          % (name, len(track), sum(sc) / len(sc),
             (sum(drift) / len(drift)) if drift else float("nan")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", help="путь к записи без расширения")
    ap.add_argument("--save-frames", help="выгрузить кадры в png в этот каталог")
    a = ap.parse_args()

    prefix = a.prefix
    for ext in (".gray", ".meta.txt", ".index.csv"):
        if not os.path.exists(prefix + ext):
            print("нет файла %s" % (prefix + ext))
            return 1
    frames, rows, (w, h) = load(prefix)
    print("запись: %d кадров %dx%d, описи %d строк" % (len(frames), w, h, len(rows)))
    if not len(frames):
        return 1

    if a.save_frames:
        os.makedirs(a.save_frames, exist_ok=True)
        for i, f in enumerate(frames):
            cv2.imwrite(os.path.join(a.save_frames, "%05d.png" % i), f)
        print("кадры выгружены в %s" % a.save_frames)

    scale = w / 640.0
    print("\nчто получилось на ЭТИХ кадрах:")
    summarize("совпадение с эталоном", run_template(frames, rows), rows, scale)
    print("\nСюда же добавляются другие варианты: пишется функция вида")
    print("run_<название>(frames, rows) -> [(cx, cy, уверенность), ...]")
    print("и строка summarize(...). Кадры одни и те же, значит сравнение честное.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
