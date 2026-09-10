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

Проверка идёт в четыре шага. Первый — БЕЗ АРМА, аппарат просто наклоняют рукой.
Второй требует арма и потому спрашивает подтверждение отдельно.

Если первый шаг уже пройден, второй запускается отдельно:

    python3 tools/os_znaki.py --shag2

ВИНТЫ СНЯТЬ. Второй шаг армит аппарат и раскручивает моторы.
"""
import ast
import io
import os
import struct
import sys
import time

import serial

KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _znaki_iz_koda():
    """Знаки осей — из самого tracker.py, а не копией здесь.

    Копия однажды уже разошлась: знак тангажа исправили в трекере, а вердикт
    в этом скрипте продолжал печатать «НЕ СОВПАДАЕТ» на верном замере. Второй
    источник правды о знаках хуже, чем никакого. Разбираем файл текстом —
    импортировать нельзя, tracker.py тянет камеру и OpenCV.
    """
    nuzhno = ("PITCH_SIGN", "ROLL_SIGN", "YAW_SIGN")
    out = {}
    try:
        derevo = ast.parse(io.open(os.path.join(KOREN, "tracker.py"),
                                   encoding="utf-8").read())
        for node in derevo.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            imya = getattr(node.targets[0], "id", None)
            if imya in nuzhno:
                out[imya] = ast.literal_eval(node.value)
    except Exception:
        pass
    return out


ZNAKI = _znaki_iz_koda()


def _sverit(imya, izmereno, chto):
    """Печатает вердикт, сверяя замер с тем, что стоит в трекере."""
    v_kode = ZNAKI.get(imya)
    print()
    print("  ВЫВОД: %s PWM %s 1500."
          % (chto, "ВЫШЕ" if izmereno > 0 else "НИЖЕ"))
    if v_kode is None:
        print("  Не смог прочитать %s из tracker.py — сверь вручную." % imya)
        return izmereno
    if v_kode == izmereno:
        print("  Совпадает с %s = %+d в tracker.py." % (imya, v_kode))
    else:
        print("  НЕ СОВПАДАЕТ: в tracker.py %s = %+d." % (imya, v_kode))
        print("  Так летать нельзя — аппарат будет доворачивать ОТ цели.")
        print("  Скажи мне результат, поправлю знак.")
    return izmereno

PORT_BY_ID = "/dev/serial/by-id/usb-Betaflight_Betaflight_STM32F405_0x8000000-if00"
PORT = PORT_BY_ID if os.path.exists(PORT_BY_ID) else "/dev/ttyACM0"
BAUD = 115200

MSP_STATUS = 101
MSP_MOTOR = 104
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_MIXER_CONFIG = 42
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


def mikser(fc):
    """Тип микшера и признак развёрнутых по рысканию моторов.

    Именно этого не хватало, чтобы определить рыскание по моторам: диагональ,
    которая ускоряется, говорит лишь о ЗНАКЕ команды в микшере, а куда при
    этом повернётся нос — зависит от направления вращения винтов. Полётник
    знает его сам и отдаёт в MSP_MIXER_CONFIG.
    """
    d = msp(fc, MSP_MIXER_CONFIG)
    if d is None or len(d) < 2:
        return None, None
    return d[0], bool(d[1])


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

# Порядок MSP_SET_RAW_RC — rcmap external (AETR): R, P, T, Y, затем AUX.
OS_INDEX = {"kren": 0, "tangazh": 1, "ryskanie": 3}


def _kanaly(os_imya, pwm):
    ch = [1500, 1500, 1000, 1500, 1000, 1000, 1000, 1000]
    ch[OS_INDEX[os_imya]] = int(pwm)
    return struct.pack("<8H", *ch)


def _srednie_motory(fc, os_imya, pwm, sek=1.2):
    """Держим команду и копим моторы. Поток кадров нужен непрерывный."""
    sbor = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < sek:
        msp(fc, MSP_SET_RAW_RC, _kanaly(os_imya, pwm))
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
    nizhe = _srednie_motory(fc, "tangazh", 1350)
    print("  подаю тангаж 1650 ...")
    vyshe = _srednie_motory(fc, "tangazh", 1650)
    # Возврат в центр и снятие команды.
    _srednie_motory(fc, "tangazh", 1500, sek=0.5)

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
    znak = +1 if d_vyshe > d_nizhe else -1
    return _sverit("PITCH_SIGN", znak, "нос опускает")


def shag_kren(fc):
    """Знак крена — тем же способом по моторам.

    Отдельным шагом, а не заодно с тангажом: тангаж на этом борту оказался
    обратным «стандартному», и после такого принимать крен на веру нельзя.
    """
    print()
    print("=" * 62)
    print("ШАГ 3. Куда PWM клонит аппарат по крену. ТРЕБУЕТ АРМА.")
    print("=" * 62)
    print("ВИНТЫ ДОЛЖНЫ БЫТЬ СНЯТЫ.")
    print()
    print("Подаю крен 1350 и 1650. Аппарат кренится вправо той командой,")
    print("при которой больше тяги дают ЛЕВЫЕ моторы.")
    if not _soglasie():
        print("Шаг 3 пропущен.")
        return None
    if not armed(fc):
        print()
        print("  АППАРАТ НЕ ЗААРМЛЕН. Заармь и запусти заново.")
        return None

    print()
    print("  подаю крен 1350 ...")
    nizhe = _srednie_motory(fc, "kren", 1350)
    print("  подаю крен 1650 ...")
    vyshe = _srednie_motory(fc, "kren", 1650)
    _srednie_motory(fc, "kren", 1500, sek=0.5)
    if nizhe is None or vyshe is None:
        print()
        print("  МОТОРЫ НЕ ОТВЕЧАЮТ. Либо разармился, либо в маске каналов")
        print("  оверрайда нет крена.")
        return None

    print()
    print("  %-8s %8s %8s %8s %8s" % ("крен", "m1", "m2", "m3", "m4"))
    print("  %-8s %8.0f %8.0f %8.0f %8.0f" % (("1350",) + tuple(nizhe)))
    print("  %-8s %8.0f %8.0f %8.0f %8.0f" % (("1650",) + tuple(vyshe)))

    # quadX по умолчанию: m1 зад-право, m2 перёд-право, m3 зад-лево,
    # m4 перёд-лево. Крен вправо = больше тяги СЛЕВА.
    def levye_minus_pravye(m):
        return (m[2] + m[3]) - (m[0] + m[1])

    d_n = levye_minus_pravye(nizhe)
    d_v = levye_minus_pravye(vyshe)
    print()
    print("  левые минус правые:  при 1350  %+.0f" % d_n)
    print("                       при 1650  %+.0f" % d_v)
    if abs(d_v - d_n) < 30:
        print()
        print("  РАЗНИЦА СЛИШКОМ МАЛА — команда до моторов не дошла.")
        return None

    znak = +1 if d_v > d_n else -1
    return _sverit("ROLL_SIGN", znak, "вправо кренит")


MIXER_QUADX = 3


def shag_ryskanie(fc):
    """Знак рыскания. По моторам плюс направление вращения винтов.

    Одних моторов мало: диагональ показывает знак команды в микшере, а не
    сторону поворота. Недостающее — yaw_motors_reversed из MSP_MIXER_CONFIG.
    """
    print()
    print("=" * 62)
    print("ШАГ 4. Куда PWM разворачивает нос. ТРЕБУЕТ АРМА.")
    print("=" * 62)
    print("ВИНТЫ ДОЛЖНЫ БЫТЬ СНЯТЫ.")

    rezhim, razvernuty = mikser(fc)
    if rezhim is None:
        print()
        print("  Полётник не отдал MSP_MIXER_CONFIG — направление вращения")
        print("  винтов неизвестно, а без него сторону поворота по моторам")
        print("  не определить. Знак рыскания придётся проверять в воздухе.")
        return None
    print()
    print("  микшер: %d%s" % (rezhim, " (quadX)" if rezhim == MIXER_QUADX else ""))
    print("  моторы развёрнуты по рысканию: %s" % ("да" if razvernuty else "нет"))
    if rezhim != MIXER_QUADX:
        print()
        print("  Это не quadX. Раскладка моторов другая, разбирать надо")
        print("  вручную. Знак рыскания проверяй в воздухе.")
        return None

    print()
    print("Подаю рыскание 1350 и 1650, смотрю, какая ДИАГОНАЛЬ ускоряется.")
    if not _soglasie():
        print("Шаг 4 пропущен.")
        return None
    if not armed(fc):
        print()
        print("  АППАРАТ НЕ ЗААРМЛЕН. Заармь и запусти заново.")
        return None

    print()
    print("  подаю рыскание 1350 ...")
    nizhe = _srednie_motory(fc, "ryskanie", 1350)
    print("  подаю рыскание 1650 ...")
    vyshe = _srednie_motory(fc, "ryskanie", 1650)
    _srednie_motory(fc, "ryskanie", 1500, sek=0.5)
    if nizhe is None or vyshe is None:
        print()
        print("  МОТОРЫ НЕ ОТВЕЧАЮТ. Либо разармился, либо в маске каналов")
        print("  оверрайда нет рыскания.")
        return None

    print()
    print("  %-10s %8s %8s %8s %8s" % ("рыскание", "m1", "m2", "m3", "m4"))
    print("  %-10s %8.0f %8.0f %8.0f %8.0f" % (("1350",) + tuple(nizhe)))
    print("  %-10s %8.0f %8.0f %8.0f %8.0f" % (("1650",) + tuple(vyshe)))

    # quadX: m1 зад-право, m2 перёд-право, m3 зад-лево, m4 перёд-лево.
    # Микшер Betaflight по рысканию: m2 и m3 в плюс, m1 и m4 в минус.
    def diagonal(m):
        return (m[1] + m[2]) - (m[0] + m[3])

    d_n = diagonal(nizhe)
    d_v = diagonal(vyshe)
    print()
    print("  диагональ (m2+m3) - (m1+m4):  при 1350  %+.0f" % d_n)
    print("                                при 1650  %+.0f" % d_v)
    if abs(d_v - d_n) < 30:
        print()
        print("  РАЗНИЦА СЛИШКОМ МАЛА — команда до моторов не дошла.")
        return None

    # Положительная команда микшера = нос вправо, если моторы не развёрнуты.
    polozh_vpravo = (d_v > d_n)
    if razvernuty:
        polozh_vpravo = not polozh_vpravo
    znak = +1 if polozh_vpravo else -1
    return _sverit("YAW_SIGN", znak, "нос вправо разворачивает")


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
    z3 = shag_kren(fc)
    z4 = shag_ryskanie(fc)

    print()
    print("=" * 62)
    print("ИТОГ")
    print("=" * 62)
    print("  знак угла (нос вниз = ...):   %s"
          % ({1: "положительный fc_pitch — как в коде",
              -1: "отрицательный fc_pitch — В КОДЕ ИНАЧЕ"}.get(z1, "не определён")))
    shodyatsya = True
    for imya, zam, podpis in (("PITCH_SIGN", z2, "тангаж (нос вниз)"),
                              ("ROLL_SIGN", z3, "крен (вправо)"),
                              ("YAW_SIGN", z4, "рыскание (вправо)")):
        v_kode = ZNAKI.get(imya)
        if zam is None:
            sost = "не определён"
            shodyatsya = False
        elif v_kode is None:
            sost = "PWM %s 1500 — в коде не прочитан" % (
                "выше" if zam > 0 else "ниже")
            shodyatsya = False
        elif v_kode == zam:
            sost = "PWM %s 1500 — как в коде" % ("выше" if zam > 0 else "ниже")
        else:
            sost = "PWM %s 1500 — В КОДЕ ИНАЧЕ" % (
                "выше" if zam > 0 else "ниже")
            shodyatsya = False
        print("  знак: %-22s %s" % (podpis, sost))
    if z1 == 1 and shodyatsya:
        print()
        print("  Все три оси сходятся с кодом. По знакам можно лететь.")
    else:
        print()
        print("  Есть расхождение либо неопределённость. Пришли мне вывод.")
    print()
    print("Не забудь вернуть службу:  sudo systemctl start tracker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
