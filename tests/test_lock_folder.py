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
import re
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


def _snat_lok(t):
    """Закрыть выборку так же, как оператор: только выключением AUX4."""
    with t.state_lock:
        t.aux4_state = False
    t.fast_idle_update()
    t._capture_flight_row(time.monotonic())
    assert not t.lock_log.active, "AUX4 выключен, а папка лока не закрылась"


def _sintet(n=320, w=320, h=240):
    """Синтетический заход: яркое пятно ползёт по шумному фону.

    Записи лежат в flight_logs, а он не в репозитории — на чистой копии
    проверка не запустилась бы вовсе. Логика папок от картинки не зависит,
    поэтому кадры делаем сами: так проверка работает всегда и везде.
    """
    rng = np.random.RandomState(7)
    fon = (rng.rand(h, w) * 40 + 60).astype(np.uint8)
    kadry = []
    for i in range(n):
        f = np.clip(fon.astype(np.int16) + rng.randint(-3, 4, (h, w)), 0,
                    255).astype(np.uint8)
        cx = w // 2 + int(28 * np.sin(i * 0.045))
        cy = h // 2 + int(16 * np.cos(i * 0.037))
        f[cy - 7:cy + 7, cx - 7:cx + 7] = 235
        f[cy - 3:cy + 3, cx - 3:cx + 3] = 90     # фактура внутри цели
        kadry.append(f)
    return kadry


zapisi = sorted(glob.glob(os.path.join(_ROOT, "flight_logs", "recordings",
                                       "*.yuv")))

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
    if zapisi:
        frames, _rows, w, h, chroma = offline.load_recording(zapisi[0][:-4])
        otkuda = os.path.basename(zapisi[0])
    else:
        frames, chroma = _sintet(), None
        h, w = frames[0].shape
        otkuda = "синтетический заход (записей нет)"
    print("=== 1. Захват открывает папку ===")
    print("    источник: %s, %d кадров %dx%d" % (otkuda, len(frames), w, h))
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
    _snat_lok(t)
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
    # Предварительный итог пишется в ФОНЕ (в камерном потоке он стоил 29 мс
    # ровно в момент захвата). Даём потоку долететь до диска.
    for _ in range(40):
        if os.path.getsize(os.path.join(tekushchaya, "итог.txt")) > 0:
            break
        time.sleep(0.05)
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

    print("\n=== 9. Любая потеря цели НЕ рвёт папку до выключения AUX4 ===")
    # HOLD, LOST и повторный ACQ относятся к тому же включению лока независимо
    # от длительности. На стенде такие потери не случаются, поэтому имитируем.
    t.lock_log.end("подготовка")
    t.track_state = t.TRACK_STATE_ACQ
    t._capture_flight_row(time.monotonic())
    bylo = len(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")))
    zaminok = 0
    for i, f in enumerate(frames[:150]):
        if chroma is not None:
            cu, cv_ = t.extract_chroma(np.ascontiguousarray(chroma[i]))
            if cu is not None:
                t.chroma_u, t.chroma_v = cu, cv_
        t.process_locked_tracker(np.ascontiguousarray(f))
        if i and i % 30 == 0:
            for zamin in (t.TRACK_STATE_HOLD, t.TRACK_STATE_LOST,
                          t.TRACK_STATE_ACQ):
                with t.state_lock:
                    t.track_state = zamin
                t._capture_flight_row(time.monotonic())
                zaminok += 1
        t._capture_flight_row(time.monotonic())
    stalo = len(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")))
    print("    заминок: %d, новых папок: %d" % (zaminok, stalo - bylo))
    assert stalo - bylo == 1, (
        "%d заминок породили %d папок — один заход развалился на куски"
        % (zaminok, stalo - bylo))

    novaya = sorted(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")),
                    key=os.path.getmtime)[-1]
    sob = io.open(os.path.join(novaya, "события.log"), encoding="utf-8").read()
    assert "заминка" in sob, (
        "заминки нигде не отмечены — на разборе провал в данных будет "
        "неотличим от исправного слежения")
    print("    заминок записано событиями:", sob.count("заминка"))

    print("\n=== 10. А снятие лока папку закрывает ===")
    _snat_lok(t)
    itog = io.open(os.path.join(novaya, "итог.txt"), encoding="utf-8").read()
    assert "НЕ ЗАВЕРШЁН" not in itog, "закрытый заход помечен как оборванный"
    assert "длительность" in itog, "у закрытого захода нет длительности"
    print("    папка закрыта, итог дописан")

    print("\n=== 11. В имени папки стоит версия кода ===")
    # Папок за день набирается под сотню, и при разборе постоянно нужно
    # знать, каким кодом снят конкретный заход. Без метки в имени это
    # выяснялось листанием итогов по одной.
    imya = os.path.basename(p)
    print("    имя папки:", imya)
    assert t.KOD_METKA and t.KOD_METKA in imya, (
        "в имени папки нет метки версии (%r) — на разборе не отличить, каким "
        "кодом снят заход" % t.KOD_METKA)
    if t.KOD_METKA != "nogit":
        assert re.match(r"^[0-9a-f]{7,}(-izm)?$", t.KOD_METKA), (
            "метка %r не похожа на хеш коммита" % t.KOD_METKA)
    # Имя должно оставаться пригодным для файловой системы и для сортировки:
    # сначала время, потом номер, потом версия.
    assert re.match(r"^\d{8}_\d{6}_zahvat\d{2}_", imya), (
        "имя папки перестало начинаться со времени — сортировка по времени "
        "сломается: %s" % imya)
    assert "/" not in t.KOD_METKA and " " not in t.KOD_METKA, (
        "метка непригодна для имени папки: %r" % t.KOD_METKA)

    print("\n=== 12. Метка считается ЗАРАНЕЕ, а не при захвате ===")
    # Вызов git занимает десятки миллисекунд. В камерном потоке на локе это
    # дало бы ступор на кадре — ровно как отладочные снимки, которые мы уже
    # оттуда убирали.
    src_t = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
    i = src_t.index("def begin(self, seq):")
    telo = src_t[i:i + 1800]
    assert "subprocess" not in telo and "_kod_metka()" not in telo, (
        "версия кода вычисляется прямо при захвате — кадр встанет в самый "
        "неподходящий момент")
    print("    в begin() нет ни git, ни subprocess")

    print("\n=== 13. Захват НЕ пишет на диск из камерного потока ===")
    # Замерено на борту: выброс cb_ms в 250 мс приходился РОВНО на нулевую
    # строку каждого захода — то есть на момент, когда цель только взята и
    # слежение хрупче всего. Виновны были отладочные снимки захвата и запись
    # итога прямо в камерном потоке.
    import threading as _th
    t.lock_log.end("подготовка к замеру")
    t.track_state = t.TRACK_STATE_ACQ
    t._capture_flight_row(time.monotonic())
    potokov_do = _th.active_count()
    t0 = time.perf_counter()
    t.lock_log.begin(999)
    otkrytie = (time.perf_counter() - t0) * 1000.0
    print("    открытие папки в камерном потоке: %.1f мс" % otkrytie)
    assert otkrytie < 15.0, (
        "открытие папки занимает %.0f мс в камерном потоке — кадр встанет "
        "ровно в момент захвата" % otkrytie)
    assert _th.active_count() > potokov_do, "итог не ушёл в фоновый поток"
    assert not t.ACQ_DEBUG_DUMP, (
        "отладочные снимки захвата включены: шесть PNG на каждый лок прямо "
        "в камерном потоке")

    print("\n=== 14. Штатная остановка службы закрывает папку честно ===")
    # На стенде службу перезапускают постоянно. Если открытая папка при этом
    # остаётся с пометкой «питание снято?», на разборе штатный перезапуск
    # выглядит как обрыв по железу — и его пойдут искать там, где его нет.
    _prognat(t, frames, chroma, min(100, len(frames)))
    assert t.lock_log.active, "заход должен быть открыт"
    otkrytaya = sorted(glob.glob(os.path.join(vremennaya, t.LOCK_LOG_DIR, "*")),
                       key=os.path.getmtime)[-1]
    t.lock_log.end("программа остановлена")
    itog = io.open(os.path.join(otkrytaya, "итог.txt"),
                   encoding="utf-8").read()
    assert "НЕ ЗАВЕРШЁН" not in itog, (
        "штатная остановка помечена как обрыв — на разборе будут искать "
        "несуществующую поломку железа")
    assert "программа остановлена" in itog, "причина остановки не записана"
    assert "длительность" in itog, "длительность не записана"
    print("    итог закрыт с причиной, без пометки об обрыве")

    print("\nOK: каждый захват получает свою папку, и обрыв её не обнуляет")
finally:
    shutil.rmtree(vremennaya, ignore_errors=True)
