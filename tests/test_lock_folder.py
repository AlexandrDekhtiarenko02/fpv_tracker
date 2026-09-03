"""Папка на каждый захват должна реально создаваться и наполняться.

Почему это отдельная проверка. Кампания по сбору данных — сотня заходов, и
разбирать их надо поштучно: один заход — одна выборка. Если папки не
создаются или в них пусто, это выяснится ПОСЛЕ полётного дня, когда данных
уже не собрать.

И почему её не было раньше: офлайн-стенд зовёт process_locked_tracker
напрямую, а папку открывает _capture_flight_row — камерный поток. Мимо этой
функции проверялось всё остальное, а сама она не проверялась ни разу.
Здесь воспроизводится именно её вызов, кадр за кадром.
"""
import glob
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


def _prognat(t, frames, chroma, kadrov):
    """Крутит контур ТАК ЖЕ, как камерный поток: слежение плюс запись строки."""
    t.reset_tracking(to_acq=True)
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    # Один кадр в поиске ДО слежения: папка открывается на переходе состояния,
    # и без этого перехода повторный заход дописался бы в прежнюю папку.
    t._capture_flight_row(time.monotonic())
    zahvatov = 0
    for i, f in enumerate(frames[:kadrov]):
        if chroma is not None:
            cu, cv_ = t.extract_chroma(np.ascontiguousarray(chroma[i]))
            if cu is not None:
                t.chroma_u, t.chroma_v = cu, cv_
        t.process_locked_tracker(np.ascontiguousarray(f))
        t._capture_flight_row(time.monotonic())
        if t.track_state == t.TRACK_STATE_TRACKED:
            zahvatov += 1
    return zahvatov


zapisi = sorted(glob.glob(os.path.join(_ROOT, "flight_logs", "recordings",
                                       "*.yuv")))
assert zapisi, "нет ни одной записи — проверять нечего"

vremennaya = tempfile.mkdtemp(prefix="zahvat_")
try:
    t = offline.load_tracker()
    t.LOCK_LOG_ENABLED = True
    t.lock_log.dir = vremennaya
    t.lock_log.n = 0
    # ВАЖНО: папку захвата открывает _capture_flight_row, а она выходит
    # сразу, если общий журнал выключен. Офлайн-стенд его глушит — поэтому
    # здесь включаем, но пишем во временную папку, чтобы не сорить.
    t.flight_log.dir = vremennaya
    t.flight_log.enabled = True
    t.flight_log.event = lambda *a, **k: None
    frames, _rows, w, h, chroma = offline.load_recording(zapisi[0][:-4])
    print("=== 1. Захват открывает папку ===")
    print("    запись: %s, %d кадров %dx%d"
          % (os.path.basename(zapisi[0]), len(frames), w, h))
    slezhenie = _prognat(t, frames, chroma, min(200, len(frames)))
    print("    кадров в слежении:", slezhenie)
    assert slezhenie > 0, "трекер вообще не захватил цель — проверять нечего"

    papki = sorted(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")))
    print("    создано папок:", len(papki))
    assert papki, (
        "захват был, а папки нет — весь полётный день запишется в пустоту")

    p = papki[0]
    print("\n=== 2. В папке лежит всё, что нужно для разбора ===")
    for imya in ("строки.csv", "события.log"):
        put = os.path.join(p, imya)
        assert os.path.exists(put), "нет файла %s" % imya
        print("    %-14s %d байт" % (imya, os.path.getsize(put)))

    print("\n=== 3. Строки пишутся, и колонки сходятся с заголовком ===")
    stroki = io.open(os.path.join(p, "строки.csv"), encoding="utf-8").read()
    stroki = [s for s in stroki.splitlines() if s.strip()]
    shapka = stroki[0].split(",")
    print("    колонок в шапке: %d, строк данных: %d"
          % (len(shapka), len(stroki) - 1))
    assert len(stroki) > 1, (
        "папка создана, но пустая — при разборе это неотличимо от «не летали»")
    for nomer, s in enumerate(stroki[1:], 1):
        assert len(s.split(",")) == len(shapka), (
            "строка %d: колонок %d вместо %d — CSV поедет при разборе"
            % (nomer, len(s.split(",")), len(shapka)))

    print("\n=== 4. Есть всё, что нужно для точки прицеливания ===")
    nuzhno = ("alt", "pitch", "box", "range", "gyro")
    for kusok in nuzhno:
        est = [c for c in shapka if kusok in c]
        assert est, "в строках нет ни одной колонки про «%s»" % kusok
        print("    %-8s %s" % (kusok, ", ".join(est[:4])))

    print("\n=== 5. Второй захват — ВТОРАЯ папка, а не дописка в первую ===")
    bylo = len(papki)
    _prognat(t, frames, chroma, min(200, len(frames)))
    papki2 = sorted(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")))
    print("    папок было %d, стало %d" % (bylo, len(papki2)))
    assert len(papki2) > bylo, (
        "второй заход дописался в первую папку — две выборки слиплись в одну, "
        "и разделить их потом будет нечем")

    print("\n=== 6. Итог захвата записан, события дописаны ===")
    t.lock_log.end("проверка окончена")
    sob = os.path.join(p, "события.log")
    print("    события.log после закрытия: %d байт" % os.path.getsize(sob))
    assert os.path.getsize(sob) > 0, (
        "события захвата не дошли до диска даже после закрытия")
    itogi = glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*", "итог.txt"))
    assert itogi, "итог.txt не записан — условия захода потеряны"
    tekst = io.open(itogi[0], encoding="utf-8").read()
    print("    " + tekst.strip().replace("\n", "\n    ")[:420])
    for slovo in ("УСЛОВИЯ ЗАХОДА", "режим наблюдения", "длительность"):
        assert slovo in tekst, "в итоге нет строки про «%s»" % slovo

    print("\n=== 7. ОБОРВАННЫЙ заход не теряет условия ===")
    # Замерено на борту: у захода, прерванного перезапуском, строки.csv был
    # на 517 КБ, а итог.txt и события.log — пустые. Условия захода пропали
    # целиком. Прерывается всегда последний заход, и он же обычно ценнее
    # прочих.
    _prognat(t, frames, chroma, min(120, len(frames)))
    aktivnye = sorted(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")),
                      key=os.path.getmtime)
    tekushchaya = aktivnye[-1]
    assert t.lock_log.active, "заход должен быть ещё открыт"
    itog = os.path.join(tekushchaya, "итог.txt")
    sob = os.path.join(tekushchaya, "события.log")
    razmery = (os.path.getsize(itog), os.path.getsize(sob))
    print("    ещё в полёте: итог.txt %d байт, события.log %d байт" % razmery)
    assert razmery[0] > 0, (
        "условия захода не записаны до конца захода — при обрыве пропадут")
    assert razmery[1] > 0, "события не дошли до диска — при обрыве пропадут"
    tekst = io.open(itog, encoding="utf-8").read()
    assert "НЕ ЗАВЕРШЁН" in tekst, (
        "недописанный итог не помечен — на разборе его примут за полный")
    assert "УСЛОВИЯ ЗАХОДА" in tekst, "условий нет в предварительном итоге"

    print("\n=== 8. Строки доходят до диска по ходу захода ===")
    put = os.path.join(tekushchaya, "строки.csv")
    na_diske = len([l for l in io.open(put, encoding="utf-8") if l.strip()])
    print("    строк на диске: %d при %d записанных"
          % (na_diske, t.lock_log._rows))
    assert na_diske > 1, (
        "на диске только шапка — при обрыве пропадут все строки захода")
    assert t.lock_log._rows - na_diske <= t.LOCK_LOG_FLUSH_ROWS + 1, (
        "на диске отстало больше секунды — последние секунды перед целью "
        "потеряются при обрыве")

    print("\nOK: каждый захват получает свою папку, и обрыв её не обнуляет")
finally:
    shutil.rmtree(vremennaya, ignore_errors=True)
