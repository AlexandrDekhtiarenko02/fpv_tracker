#!/usr/bin/env python3
"""Прогнать запись через НАСТОЯЩИЙ трекер и выдать это глазами.

Получается каталог с видео и кадрами, где на каждом кадре нарисовано, что
трекер решал в этот момент: рамка, прицел, размер, совпадение, и — красным —
кадры, где рамка стояла на неподвижном фоне, то есть съехала с цели.

    python3 tools/render.py                       разобрать ВСЕ записи
    python3 tools/render.py <запись>              одну
    python3 tools/render.py --out каталог

Итог ложится в flight_logs/rendered/<имя записи>/.
"""
import argparse
import os
import sys

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "flight_logs", "recordings")
OUT = os.path.join(ROOT, "flight_logs", "rendered")

GREEN = (80, 230, 80)
RED = (60, 60, 255)
GREY = (170, 170, 170)
YELLOW = (0, 220, 255)


def draw(frame, i, res, on_bg, total):
    """Кадр как есть плюс то, что трекер про него думал."""
    k = 3
    v = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    v = cv2.resize(v, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    h, w = v.shape[:2]
    # прицел — центр кадра, куда наводится аппарат
    cv2.drawMarker(v, (w // 2, h // 2), GREY, cv2.MARKER_CROSS, 22, 1)

    _i, cx, cy, bw, score = res
    if cx is not None:
        col = RED if on_bg else GREEN
        x, y, b = int(cx * k), int(cy * k), int(bw * k)
        cv2.rectangle(v, (x - b // 2, y - b // 2), (x + b // 2, y + b // 2), col, 2)
        cv2.line(v, (w // 2, h // 2), (x, y), col, 1)
        label = "sz %.0f   score %.2f" % (bw * 2, score)
        if on_bg:
            label += "   НА ФОНЕ"
    else:
        label = "цель не ведётся"
        col = RED
    bar = np.zeros((26, w, 3), np.uint8)
    cv2.putText(bar, "%04d/%04d  %s" % (i, total, label), (6, 18),
                cv2.FONT_HERSHEY_PLAIN, 1.1, col, 1)
    return np.vstack([v, bar])


def render_one(t, prefix, outdir, save_png=False):
    name = os.path.basename(prefix)
    frames, rows, w, h, chroma = offline.load_recording(prefix)
    out = offline.run(t, frames, chroma)
    masks = offline.motion_mask(frames)

    d = os.path.join(outdir, name)
    os.makedirs(d, exist_ok=True)
    fps = 25.0
    first = draw(frames[0], 0, out[0], False, len(frames))
    vw = cv2.VideoWriter(os.path.join(d, name + ".mp4"),
                         cv2.VideoWriter_fourcc(*"mp4v"), fps,
                         (first.shape[1], first.shape[0]))
    bad = 0
    lines = ["кадр,cx,cy,sz,score,на_фоне"]
    for i, f in enumerate(frames):
        res = out[i]
        on_bg = False
        if res[1] is not None:
            y = max(0, min(h - 1, int(round(res[2]))))
            x = max(0, min(w - 1, int(round(res[1]))))
            on_bg = masks[i, max(0, y - 3):y + 4, max(0, x - 3):x + 4].sum() == 0
            if on_bg:
                bad += 1
        vis = draw(f, i, res, on_bg, len(frames))
        vw.write(vis)
        if save_png:
            cv2.imwrite(os.path.join(d, "%05d.png" % i), vis)
        lines.append("%d,%s,%s,%s,%.4f,%d" % (
            i,
            "" if res[1] is None else "%.1f" % res[1],
            "" if res[2] is None else "%.1f" % res[2],
            "" if res[3] is None else "%.1f" % (res[3] * 2),
            res[4], 1 if on_bg else 0))
    vw.release()
    with open(os.path.join(d, "разбор.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    ok = [r for r in out if r[1] is not None]
    with open(os.path.join(d, "итог.txt"), "w", encoding="utf-8") as fh:
        fh.write("запись: %s, %d кадров %dx%d\n" % (name, len(frames), w, h))
        fh.write("код: %s\n" % t._code_version())
        if ok:
            fh.write("кадров со слежением: %d\n" % len(ok))
            fh.write("совпадение среднее: %.3f\n"
                     % (sum(r[4] for r in ok) / len(ok)))
            fh.write("коробка: %.0f -> %.0f px\n" % (ok[0][3] * 2, ok[-1][3] * 2))
        fh.write("рамка на неподвижном фоне: %.1f%% кадров\n"
                 % (100.0 * bad / max(len(ok), 1)))
        fh.write("\nкрасная рамка на видео = рамка стоит на фоне, а не на цели\n")
    return name, len(frames), 100.0 * bad / max(len(ok), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", nargs="?", help="одна запись; без него — все")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--png", action="store_true", help="ещё и отдельные кадры")
    a = ap.parse_args()

    t = offline.load_tracker()
    if a.prefix:
        names = [a.prefix]
    else:
        names = [os.path.join(REC, f[:-len(".meta.txt")])
                 for f in sorted(os.listdir(REC)) if f.endswith(".meta.txt")]
    if not names:
        print("записей нет")
        return 1
    os.makedirs(a.out, exist_ok=True)
    print("%-20s %8s %10s" % ("запись", "кадров", "НА ФОНЕ"))
    print("-" * 42)
    for p in names:
        nm, n, pct = render_one(t, p, a.out, a.png)
        print("%-20s %8d %9.1f%%" % (nm, n, pct))
    print("-" * 42)
    print("готово: %s" % a.out)
    print("в каждом каталоге: <имя>.mp4, разбор.csv, итог.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
