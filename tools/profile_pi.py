"""Замер стоимости кадра НА БОРТУ, по настоящей записи полёта.

Пересчёт «Мак умножить на 55» — грубая калибровка, и на этой задаче она
разошлась с фактом почти вдвое. Поэтому меряем там, где оно работает.

Трекер на время замера должен быть остановлен: он занимает то же ядро, и
цифры получатся вдвое хуже правды.

    sudo systemctl stop tracker
    python3 tools/profile_pi.py
    sudo systemctl start tracker
"""
import glob
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402


def main():
    zap = sorted(glob.glob(os.path.join(_ROOT, "flight_logs", "recordings",
                                        "*.yuv")))
    if len(sys.argv) > 1:
        zap = [x for x in zap if sys.argv[1] in x]
    if not zap:
        print("нет записей для замера")
        return 1
    put = zap[-1]
    t = offline.load_tracker()
    fr, _r, w, h, ch = offline.load_recording(put[:-4])
    print("запись: %s, %d кадров %dx%d" % (os.path.basename(put), len(fr), w, h))

    with t.state_lock:
        t.app_state.update({"armed": True, "alt_cm": 1900,
                            "fc_pitch_deg": 14.0})
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True

    kadry = [np.ascontiguousarray(f) for f in fr]
    chrom = [np.ascontiguousarray(c) for c in ch] if ch is not None else None
    glavnyi = np.zeros((t.MAIN_H, t.MAIN_W, 4), np.uint8)

    def zamer(imya, fn, n=None):
        n = n or len(kadry)
        t.reset_tracking(to_acq=True)
        t.track_state = t.TRACK_STATE_ACQ
        fn(0)                                   # прогрев
        t0 = time.perf_counter()
        for i in range(n):
            fn(i % len(kadry))
        ms = (time.perf_counter() - t0) / n * 1000.0
        print("  %7.2f мс/кадр   %s" % (ms, imya))
        return ms

    def cvet(i):
        if chrom is not None:
            cu, cv_ = t.extract_chroma(chrom[i])
            if cu is not None:
                t.chroma_u, t.chroma_v = cu, cv_

    print("\n=== по частям ===")
    a = zamer("цветность", cvet)
    b = zamer("бег земли", lambda i: t.estimate_ground_speed(
        kadry[i], time.monotonic() + i * 0.048))
    t.reset_tracking(to_acq=True)
    t.track_state = t.TRACK_STATE_ACQ
    for i in range(min(60, len(kadry))):
        t.process_locked_tracker(kadry[i])
    c = zamer("слежение", lambda i: t.process_locked_tracker(kadry[i]))
    d = zamer("оверлей", lambda i: t.draw_overlay_on_frame(glavnyi))
    e = zamer("  из них лупа",
              lambda i: t.draw_magnifier(glavnyi, t.target_box_main))
    f = zamer("  из них телеметрия",
              lambda i: t.draw_range_readout(glavnyi, t.target_box_main))

    print("\n=== итого ===")
    summa = a + b / max(1, t.GROUND_EVERY_N) + c + d
    print("  %7.2f мс на кадр (бег земли учтён с прореживанием %d)"
          % (summa, t.GROUND_EVERY_N))
    if summa > 0:
        print("  потолок по обработке: %.1f к/с" % (1000.0 / summa))
    print("\n  для 24 к/с бюджет 41.7 мс; из них на обработку разумно "
          "оставить не больше 30")
    return 0


if __name__ == "__main__":
    sys.exit(main())
