"""Кадры и индекс обязаны завершаться вместе даже на медленной карте.

На борту поток записи не успел дописать очередь за старый таймаут в 3 секунды.
Основной поток закрыл index.csv у него под руками: YUV продолжился, а опись
оборвалась. Здесь карта намеренно замедлена так, чтобы прежняя реализация
воспроизвела тот же отказ.
"""
import builtins
import csv
import io
import os
import shutil
import sys
import tempfile
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402


class _SlowFile:
    def __init__(self, raw, delay):
        self.raw = raw
        self.delay = delay

    def write(self, data):
        time.sleep(self.delay)
        return self.raw.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.raw.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.raw, name)


t = offline.load_tracker()
t.RECORD_FRAMES = True
t.RECORD_MAX_SECONDS = 30.0
t.RECORD_MAX_MB = 100.0
t.FLIGHT_LOG_FSYNC = False
t.CAM_W, t.CAM_H = 4, 4
events = []
t.flight_log.event = events.append

tmp = tempfile.mkdtemp(prefix="frame_recorder_")
real_open = builtins.open
real_strftime = t.time.strftime
try:
    def slow_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if str(path).endswith(".yuv") and "wb" in mode:
            return _SlowFile(f, 0.04)
        return f

    builtins.open = slow_open
    t.time.strftime = lambda *_args, **_kwargs: "20990101_010203"
    rec = t.FrameRecorder(tmp)
    rec.start((6, 4))
    frame = np.arange(24, dtype=np.uint8).reshape(6, 4)
    for i in range(90):
        rec.add(frame + i, "%.3f,TRACKED,1,2,3,4,0.9" % (i / 30.0))

    print("=== 1. stop ждёт полного опустошения очереди ===")
    rec.stop("проверка")
    assert not rec._thread.is_alive(), "stop вернулся, пока писатель ещё работает"
    assert rec._error is None, "поток записи завершился с ошибкой: %s" % rec._error

    builtins.open = real_open
    with real_open(rec._index_path, newline="") as f:
        rows = list(csv.DictReader(f))
    raw_frames = os.path.getsize(rec._path) // frame.nbytes
    print("    YUV кадров: %d, строк индекса: %d" % (raw_frames, len(rows)))
    assert raw_frames == len(rows) == rec._n, (
        "YUV и индекс разошлись: %d кадров, %d строк" % (raw_frames, len(rows)))
    assert any("ЗАПИСЬ КАДРОВ окончена" in e for e in events), (
        "в журнале нет подтверждения полного завершения записи")

    print("\n=== 2. новый захват не затирает предыдущий в ту же секунду ===")
    first_path = rec._path
    rec.start((6, 4))
    rec.add(frame, "0.000,TRACKED,1,2,3,4,0.9")
    rec.stop("вторая проверка")
    print("    %s -> %s" % (os.path.basename(first_path),
                          os.path.basename(rec._path)))
    assert rec._path != first_path, "второй захват затёр первый"
    assert os.path.exists(first_path) and os.path.exists(rec._path)
finally:
    builtins.open = real_open
    t.time.strftime = real_strftime
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 3. Выключение AUX4 не обходит завершение записи ===")
source = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
idle_branch = source.split("if not aux_snapshot:", 1)[1].split(
    'with MappedArray(request, "lores")', 1)[0]
assert 'frame_recorder.stop("AUX4 выключен")' in idle_branch, (
    "ранний выход AUX4 оставит YUV и индекс открытыми")

print("\nOK: каждый записанный кадр имеет строку индекса")
