#!/usr/bin/env python3
"""Проверка знаков оси тангажа по MSP. Configurator не нужен.

ЗАЧЕМ. В законе наведения два знака, и оба должны быть верны, иначе аппарат
доворачивает ОТ цели:

  1. что значит положительный fc_pitch — нос вниз или нос вверх;
  2. в какую сторону PWM тангажа опускает нос.

Первый знак установлен по 12.9 тыс. кадров полётных логов (нос вниз =
положительный fc_pitch), но те логи писались с ПРЕЖНИМ полётником. После
замены платы соглашение могло смениться — поэтому проверяем заново.

Второй по логам не проверить вовсе: стик тангажа стоял в пределах ±25 от
центра в 96% кадров, сильных отклонений 0.1%. Возбуждения нет — знака нет.

ПОЧЕМУ НЕ CONFIGURATOR. USB полётника занят малиной, второго MSP-клиента
плата не примет. Но всё нужное она и так отдаёт по MSP: угол — в
MSP_ATTITUDE, отклик на команду — в MSP_MOTOR.

ЗАПУСК:

    sudo systemctl stop tracker
    python3 tools/os_znaki.py
    sudo systemctl start tracker

Проверка идёт в два шага. Первый — БЕЗ АРМА, аппарат просто наклоняют рукой.
Второй требует арма и потому спрашивает подтверждение отдельно.

Если первый шаг уже пройден, второй запускается отдельно:

    python3 tools/os_znaki.py --shag2

ВИНТЫ СНЯТЬ. Второй шаг армит аппарат и раскручивает моторы.
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
MSP_MOTOR = 104
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_SET_RAW_RC = 200


def _ramka(cmd, payload=b""):
    razmer = len(payload)
    telo = struct.pack("<BB", razmer, cmd) + payload
    ks = 0
    for b in telo:
        ks ^= b
    return b"$M<" + telo + struct.pack("<B", ks)


def msp(fc, cmd, payload=b""):
    """Запрос-ответ. Возвращает тело ответа либо None."""
    try:
        fc.reset_input_buffer()
        fc.write(_ramka(cmd, payload))
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
        if not size_b or not cmd_b or cmd_b[0] != cmd:
            return None
        n = size_b[0]
        data = fc.read(n) if n else b""
        fc.read(1)
        return data if len(data) == n else None
    except Exception:
        return None


def tangazh(fc):
    d = msp(fc, MSP_ATTITUDE)
    if d is None or len(d) < 6:
        return None
    _roll, pitch, _yaw = struct.unpack("<3h", d[:6])
    return pitch / 10.0


def motory(fc):
    d = msp(fc, MSP_MOTOR)
    if d is None or len(d) < 8:
        return None
    return list(struct.unpack("<" + "H" * (len(d) // 2), d))[:4]


def armed(fc):
    d = msp(fc, MSP_STATUS)
    if d is None or len(d) < 11:
        return None
    flags = struct.unpack("<I", d[6:10])[0]
    return bool(flags & 1)


# ---------- шаг 1: что значит знак fc_pitch ----------

def shag_ugol(fc):
    print()
    print("=" * 62)
    print("ШАГ 1. Знак угла. Арм не нужен, аппарат просто наклоняют рукой.")
    print("=" * 62)
    print("Поставь аппарат ровно и нажми Enter.")
    input()
    ravno = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:
        v = tangazh(fc)
        if v is not None:
            ravno.append(v)
        time.sleep(0.05)
    if not ravno:
        print("Полётник не отвечает на MSP_ATTITUDE. Проверь порт.")
        return None
    nol = sum(ravno) / len(ravno)
    print("  ровно: fc_pitch = %+.1f°" % nol)

    print()
    print("Теперь НАКЛОНИ НОС ВНИЗ градусов на 30 и держи. Enter, когда держишь.")
    input()
    vniz = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:
        v = tangazh(fc)
        if v is not None:
            vniz.append(v)
        time.sleep(0.05)
    if not vniz:
        print("Полётник перестал отвечать.")
        return None
    naklon = sum(vniz) / len(vniz)
    print("  нос вниз: fc_pitch = %+.1f°" % naklon)

    raznica = naklon - nol
    if abs(raznica) < 10.0:
        print()
        print("  РАЗНИЦА СЛИШКОМ МАЛА (%+.1f°). Наклон был меньше 10° либо" % raznica)
        print("  угол не обновляется. Повтори с бóльшим наклоном.")
        return None
    znak = +1 if raznica > 0 else -1
    print()
    print("  ВЫВОД: нос вниз — это %s fc_pitch (изменение %+.1f°)."
          % ("ПОЛОЖИТЕЛЬНЫЙ" if znak > 0 else "ОТРИЦАТЕЛЬНЫЙ", raznica))
    if znak > 0:
        print("  Совпадает с тем, что заложено в tracker.py.")
    else:
        print("  НЕ СОВПАДАЕТ с tracker.py: там нос вниз считается")
        print("  положительным. Так летать нельзя — скажи мне, поправлю.")
    return znak


# ---------- шаг 2: куда PWM клонит нос ----------

def _kanaly(pitch_pwm):
    # Порядок MSP_SET_RAW_RC — rcmap external (AETR): R, P, T, Y, затем AUX.
    ch = [1500, int(pitch_pwm), 1000, 1500, 1000, 1000, 1000, 1000]
    return struct.pack("<8H", *ch)


def _srednie_motory(fc, pitch_pwm, sek=1.2):
    """Держим команду и копим моторы. Поток кадров нужен непрерывный."""
    sbor = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < sek:
        msp(fc, MSP_SET_RAW_RC, _kanaly(pitch_pwm))
        m = motory(fc)
        if m and len(m) >= 4 and max(m) > 1000:
            sbor.append(m)
        time.sleep(0.02)
    if len(sbor) < 5:
        return None
    n = len(sbor)
    return [sum(s[i] for s in sbor) / float(n) for i in range(4)]


DA = ("da", "да", "d", "д", "y", "yes", "ага", "ок", "ok")
NET = ("net", "нет", "n", "н", "no", "-", "")


def _soglasie():
    """Согласие на шаг с армом. Переспрашивает, а не толкует молча.

    Неузнанный ответ раньше означал «пропустить»: набранное второпях «dada»
    отменяло шаг, хотя человек явно соглашался. Молчаливый отказ на шаге,
    который иначе некому выполнить, хуже лишнего вопроса.
    """
    while True:
        print("Продолжаем? (da / net): ", end="")
        sys.stdout.flush()
        try:
            otvet = input().strip().lower()
        except EOFError:
            return False
        if otvet in DA:
            return True
        if otvet in NET:
            return False
        print("  Не понял ответ %r. Нужно da или net." % otvet)


def shag_pwm(fc):
    print()
    print("=" * 62)
    print("ШАГ 2. Куда PWM клонит нос. ТРЕБУЕТ АРМА.")
    print("=" * 62)
    print("ВИНТЫ ДОЛЖНЫ БЫТЬ СНЯТЫ. Моторы раскрутятся.")
    print()
    print("Что произойдёт: скрипт подаёт тангаж 1350 и 1650 и смотрит, какая")
    print("пара моторов ускоряется. Нос опускается той парой, что даёт больше")
    print("тяги сзади — по этому и определяется направление.")
    print()
    print("Нужно: MSP OVERRIDE поднят, аппарат заармлен, аппарат закреплён.")
    if not _soglasie():
        print("Шаг 2 пропущен.")
        return None

    if not armed(fc):
        print()
        print("  АППАРАТ НЕ ЗААРМЛЕН. Заармь и запусти скрипт заново.")
        return None

    print()
    print("  подаю тангаж 1350 ...")
    nizhe = _srednie_motory(fc, 1350)
    print("  подаю тангаж 1650 ...")
    vyshe = _srednie_motory(fc, 1650)
    # Возврат в центр и снятие команды.
    _srednie_motory(fc, 1500, sek=0.5)

    if nizhe is None or vyshe is None:
        print()
        print("  МОТОРЫ НЕ ОТВЕЧАЮТ. Либо аппарат разармился, либо MSP")
        print("  OVERRIDE не поднят — тогда команда никуда не идёт.")
        return None

    print()
    print("  %-8s %8s %8s %8s %8s" % ("тангаж", "m1", "m2", "m3", "m4"))
    print("  %-8s %8.0f %8.0f %8.0f %8.0f" % (("1350",) + tuple(nizhe)))
    print("  %-8s %8.0f %8.0f %8.0f %8.0f" % (("1650",) + tuple(vyshe)))

    # Betaflight quadX по умолчанию: m1 зад-право, m2 перёд-право,
    # m3 зад-лево, m4 перёд-лево. Нос опускается, когда ЗАДНИЕ дают больше.
    def zad_minus_perednie(m):
        return (m[0] + m[2]) - (m[1] + m[3])

    d_nizhe = zad_minus_perednie(nizhe)
    d_vyshe = zad_minus_perednie(vyshe)
    print()
    print("  задние минус передние:  при 1350  %+.0f" % d_nizhe)
    print("                          при 1650  %+.0f" % d_vyshe)

    if abs(d_vyshe - d_nizhe) < 30:
        print()
        print("  РАЗНИЦА СЛИШКОМ МАЛА. Команда до моторов не дошла: скорее")
        print("  всего MSP OVERRIDE не поднят либо маска каналов не включает")
        print("  тангаж.")
        return None

    # Больше тяги сзади = нос вниз.
    nos_vniz_pwm = 1650 if d_vyshe > d_nizhe else 1350
    znak = +1 if nos_vniz_pwm > 1500 else -1
    print()
    print("  ВЫВОД: нос опускает PWM %s 1500."
          % ("ВЫШЕ" if znak > 0 else "НИЖЕ"))
    if znak < 0:
        print("  Совпадает с тем, что заложено в tracker.py")
        print("  (PITCH_SIGN = -1, PITCH_PWM_TO_FC_ANGLE_SIGN = -1).")
    else:
        print("  НЕ СОВПАДАЕТ с tracker.py: там нос опускается PWM НИЖЕ 1500.")
        print("  Так летать нельзя — аппарат будет доворачивать ОТ цели.")
        print("  Скажи мне результат, поправлю знаки.")
    return znak


def main():
    print("порт: %s" % PORT)
    try:
        fc = serial.Serial(PORT, baudrate=BAUD, timeout=0.05)
    except Exception as exc:
        print("НЕ ОТКРЫЛСЯ ПОРТ: %s" % exc)
        print("Его держит служба трекера. Останови её:")
        print("    sudo systemctl stop tracker")
        return 1
    time.sleep(0.3)

    if tangazh(fc) is None:
        print("Полётник не отвечает по MSP. Проверь кабель и порт.")
        return 1

    tolko2 = "--shag2" in sys.argv
    if tolko2:
        # Знак угла уже установлен на этой плате — второй раз наклонять
        # аппарат руками незачем.
        print()
        print("Шаг 1 пропущен по --shag2 (знак угла считаем уже проверенным).")
        z1 = 1
    else:
        z1 = shag_ugol(fc)
    z2 = shag_pwm(fc)

    print()
    print("=" * 62)
    print("ИТОГ")
    print("=" * 62)
    print("  знак угла (нос вниз = ...):   %s"
          % ({1: "положительный fc_pitch — как в коде",
              -1: "отрицательный fc_pitch — В КОДЕ ИНАЧЕ"}.get(z1, "не определён")))
    print("  знак команды (нос вниз = ...): %s"
          % ({-1: "PWM ниже 1500 — как в коде",
              1: "PWM выше 1500 — В КОДЕ ИНАЧЕ"}.get(z2, "не определён")))
    if z1 == 1 and z2 == -1:
        print()
        print("  Оба знака сходятся с кодом. По этой части можно лететь.")
    else:
        print()
        print("  Есть расхождение либо неопределённость. Пришли мне вывод.")
    print()
    print("Не забудь вернуть службу:  sudo systemctl start tracker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
