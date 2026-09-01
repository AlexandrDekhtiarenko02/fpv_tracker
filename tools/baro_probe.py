#!/usr/bin/env python3
"""Опросник барометра: сравнить ВСЕ источники высоты в реальном времени.

Зачем. Betaflight Configurator показывает подъём на метр, а MSP_ALTITUDE
отдаёт трекеру 31 см. Гадать, какой именно источник мы читаем, бессмысленно —
проще опросить полётный контроллер несколькими способами сразу и посмотреть,
какой из них следует за рукой.

ЗАПУСК. Порт занят службой трекера, поэтому её надо остановить:

    sudo systemctl stop tracker
    python3 tools/baro_probe.py
    sudo systemctl start tracker

Что делать во время опроса: заармиться, поднять дрон на метр-полтора,
подержать, опустить. Скрипт печатает строку раз в полсекунды.

ЧТОБЫ УВИДЕТЬ СЫРОЙ БАРОМЕТР, в Betaflight CLI заранее:

    set debug_mode = ALTITUDE
    save

Тогда в debug[] поедут внутренние величины оценщика высоты, и станет видно,
расходится ли сырой барометр с итоговой оценкой. Без этого колонка debug
будет нулевой — это не поломка скрипта.
"""
import os
import struct
import sys
import time

import serial

PORT_BY_ID = "/dev/serial/by-id/usb-Betaflight_Betaflight_STM32F405_0x8000000-if00"
PORT = PORT_BY_ID if os.path.exists(PORT_BY_ID) else "/dev/ttyACM0"
BAUD = 115200

MSP_STATUS = 101
MSP_ALTITUDE = 109
MSP_DEBUG = 254


def msp_request(fc, cmd):
    try:
        fc.reset_input_buffer()
        fc.write(b"$M<" + struct.pack("<BBB", 0, cmd, cmd))
        sync = b""
        for _ in range(64):
            b = fc.read(1)
            if not b:
                return None
            sync += b
            if sync.endswith(b"$M>"):
                break
        else:
            return None
        size_b = fc.read(1)
        cmd_b = fc.read(1)
        if not size_b or not cmd_b:
            return None
        size_ = size_b[0]
        if cmd_b[0] != cmd:
            return None
        data = fc.read(size_) if size_ else b""
        fc.read(1)  # контрольная сумма, нам не нужна
        return data if len(data) == size_ else None
    except Exception:
        return None


def main():
    print("порт: %s" % PORT)
    try:
        fc = serial.Serial(PORT, baudrate=BAUD, timeout=0.05)
    except Exception as exc:
        print("НЕ ОТКРЫЛСЯ ПОРТ: %s" % exc)
        print("Скорее всего его держит служба. Останови её:")
        print("    sudo systemctl stop tracker")
        return 1
    time.sleep(0.3)

    print()
    print("%-8s %-10s %-10s %-9s %s" % (
        "время", "alt, см", "vario", "armed", "debug[0..3]"))
    print("-" * 62)

    t0 = time.monotonic()
    alt_min, alt_max = None, None
    try:
        while True:
            alt_cm = vario = None
            d = msp_request(fc, MSP_ALTITUDE)
            if d is not None and len(d) >= 6:
                alt_cm, vario = struct.unpack("<ih", d[:6])
                alt_min = alt_cm if alt_min is None else min(alt_min, alt_cm)
                alt_max = alt_cm if alt_max is None else max(alt_max, alt_cm)

            armed = "?"
            d = msp_request(fc, MSP_STATUS)
            if d is not None and len(d) >= 10:
                flags = struct.unpack_from("<I", d, 6)[0]
                # Бит арма зависит от раскладки боксов, поэтому показываем
                # сырые флаги: важно лишь, меняются ли они при арме.
                armed = "флаги=%d" % (flags & 0xFF)

            dbg = []
            d = msp_request(fc, MSP_DEBUG)
            if d is not None and len(d) >= 8:
                dbg = list(struct.unpack("<4h", d[:8]))

            print("%-8.1f %-10s %-10s %-9s %s" % (
                time.monotonic() - t0,
                "нет" if alt_cm is None else alt_cm,
                "нет" if vario is None else vario,
                armed,
                dbg if dbg else "нет (нужен set debug_mode = ALTITUDE)"))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        if alt_min is not None:
            print("ИТОГ: высота гуляла от %d до %d см, размах %d см"
                  % (alt_min, alt_max, alt_max - alt_min))
            print("Если ты поднимал дрон на метр, а размах меньше 50 см —")
            print("значит до нас доезжает не сырой барометр, а сглаженная")
            print("оценка высоты, и читать надо другое.")
        fc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
