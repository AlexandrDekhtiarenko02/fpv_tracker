#!/usr/bin/env python3
"""Прогнать НАСТОЯЩИЙ код трекера по записанным кадрам.

Отличие от tools/replay.py принципиальное. Replay гоняет упрощённый матч —
он годится, чтобы сравнить наш подход с чужим, но проверять им наши
СОБСТВЕННЫЕ правки нельзя: правки живут в tracker.py, а не в упрощённой копии.

Здесь берётся сам tracker.py целиком. Железо подменяется заглушками, полёт не
запускается, вызывается ровно та функция слежения, что работает на борту. То
есть любую правку можно проверить на записи — сколько угодно раз, по одним и
тем же кадрам, без прогонов оператора.

    python3 tools/offline.py flight_logs/recordings/20260901_175723
    python3 tools/offline.py <запись> --strip out.png
"""
import argparse
import csv
import os
import sys
import types

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_hardware():
    """Заглушки железа, чтобы tracker.py импортировался на любой машине."""
    picam = types.ModuleType("picamera2")
    picam.Picamera2 = type("Picamera2", (), {})
    picam.Preview = type("Preview", (), {"NULL": 0})
    picam.MappedArray = type("MappedArray", (), {})
    sys.modules.setdefault("picamera2", picam)

    lib = types.ModuleType("libcamera")
    lib.Transform = lambda **k: types.SimpleNamespace(**k)
    sys.modules.setdefault("libcamera", lib)

    ser = types.ModuleType("serial")

    class _S(object):
        def __init__(self, *a, **k):
            raise IOError("порта нет, и не нужно")

    ser.Serial = _S
    sys.modules.setdefault("serial", ser)


def load_tracker():
    _fake_hardware()
    sys.path.insert(0, ROOT)
    import importlib
    t = importlib.import_module("tracker")
    # Ни журнала, ни снимков, ни записи: прогон на земле должен быть тихим.
    t.ACQ_DEBUG_DUMP = False
    t.RECORD_FRAMES = False
    t.flight_log.enabled = False
    t.flight_log.event = lambda *a, **k: None
    return t


def load_recording(prefix):
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
    return frames, rows, w, h


def run(t, frames):
    """Прогон ровно того контура, что крутится на борту."""
    t.reset_tracking(to_acq=True)
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    out = []
    for i, f in enumerate(frames):
        t.process_locked_tracker(np.ascontiguousarray(f))
        box = t.target_box_main
        if t.track_state == t.TRACK_STATE_TRACKED and box:
            out.append((i, (box[0] + box[2]) / 4.0, (box[1] + box[3]) / 4.0,
                        (box[2] - box[0]) / 2.0, t.last_match_score))
        else:
            out.append((i, None, None, None, t.last_match_score))
    return out


def compare(out, rows, scale):
    ok = [r for r in out if r[1] is not None]
    if not ok:
        print("  трекер не удержал цель ни одного кадра")
        return
    drift = []
    for i, cx, cy, _w, _s in ok:
        if i >= len(rows):
            break
        bx, by = rows[i].get("box_cx"), rows[i].get("box_cy")
        if not bx:
            continue
        drift.append(((cx - float(bx) * scale) ** 2
                      + (cy - float(by) * scale) ** 2) ** 0.5)
    sc = [r[4] for r in ok]
    lost = len(out) - len(ok)
    print("  кадров со слежением %d из %d (потеряно %d)" % (len(ok), len(out), lost))
    print("  совпадение %.3f | коробка %.0f -> %.0f px"
          % (sum(sc) / len(sc), ok[0][3] * 2, ok[-1][3] * 2))
    if drift:
        print("  расхождение с тем, что решил борт: %.1f px в среднем, %.1f максимум"
              % (sum(drift) / len(drift), max(drift)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix")
    ap.add_argument("--strip", help="полоса кадров с рамками")
    a = ap.parse_args()
    for ext in (".gray", ".meta.txt", ".index.csv"):
        if not os.path.exists(a.prefix + ext):
            print("нет файла", a.prefix + ext)
            return 1
    t = load_tracker()
    frames, rows, w, h = load_recording(a.prefix)
    if (w, h) != (t.LORES_W, t.LORES_H):
        print("запись %dx%d, а трекер настроен на %dx%d — не сравнить"
              % (w, h, t.LORES_W, t.LORES_H))
        return 1
    print("запись: %d кадров %dx%d" % (len(frames), w, h))
    print("код: %s" % t._code_version())
    print()
    out = run(t, frames)
    compare(out, rows, w / 640.0)

    if a.strip:
        n = len(frames)
        idx = [int(i * (n - 1) / 4) for i in range(5)]
        tiles = []
        for i in idx:
            v = cv2.cvtColor(frames[i], cv2.COLOR_GRAY2BGR)
            r = rows[i] if i < len(rows) else None
            if r and r.get("box_cx"):
                bx = float(r["box_cx"]) / 2
                by = float(r["box_cy"]) / 2
                bw = float(r["box_w"]) / 2
                cv2.rectangle(v, (int(bx - bw / 2), int(by - bw / 2)),
                              (int(bx + bw / 2), int(by + bw / 2)), (0, 255, 0), 1)
            _i, cx, cy, bw2, _s = out[i]
            if cx is not None:
                cv2.rectangle(v, (int(cx - bw2 / 2), int(cy - bw2 / 2)),
                              (int(cx + bw2 / 2), int(cy + bw2 / 2)), (0, 140, 255), 1)
            cv2.putText(v, str(i), (4, 13), cv2.FONT_HERSHEY_PLAIN, 0.9,
                        (255, 255, 255), 1)
            tiles.append(v)
        cv2.imwrite(a.strip, np.hstack(tiles))
        print("\nполоса: %s  (зелёное — что решил борт, оранжевое — этот прогон)"
              % a.strip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
