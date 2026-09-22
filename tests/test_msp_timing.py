"""Диагностика MSP-транспорта: длительность каждого msp_request() по типу
команды + интервал между успешными MSP_SET_RAW_RC, с p50/p90/p99 в
журнал раз в MSP_TIMING_REPORT_PERIOD_S (диагностический коммит, сам
протокол/поведение не меняет).

msp_request() блокирующий, serial timeout=0.05 с — один неудачный запрос
способен съесть 50 мс, а цикл fc_io_loop идёт каждые MSP_RC_PERIOD=0.04 с.
Раньше не было способа увидеть, действительно ли низкоприоритетная
диагностика (STATUS/GPS/MOTOR) задерживает то, что важнее по времени
(MSP_RC, отправку MSP_SET_RAW_RC) — только предположение по чтению кода.

msp_request() и _msp_timing_report()/_percentile() проверяются
ФУНКЦИОНАЛЬНО: offline-харнесс подменяет serial.Serial так, что t.fc
всегда None (реальный порт открыть нельзя) — здесь t.fc подменяется
на фейковый объект, чтобы пройти протокольную логику до конца, включая
finally-блок, который и пишет тайминг. Интервал между отправками (код
внутри fc_io_loop, не отдельная функция) проверяется по исходному тексту.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()


class _FakeSerialTimeout(object):
    """Имитирует таймаут порта: read(1) сразу отдаёт пустые байты, как
    настоящий serial.Serial(timeout=0.05) при отсутствии ответа. Этого
    достаточно, чтобы msp_request() дошёл до одного из ранних `return
    None` внутри try — а значит и до finally, который пишет тайминг:
    сам протокольный код (sync/checksum) этим тестом не проверяется, он
    не менялся."""

    def __init__(self):
        self.written = []

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written.append(data)

    def read(self, n):
        return b""


print("=== 1. msp_request() пишет тайминг даже при таймауте (finally) ===")
t.fc = _FakeSerialTimeout()
t._msp_timing_ms["rc"].clear()
result = t.msp_request(105)
assert result is None, "фейковый порт без ответа должен дать None"
assert len(t._msp_timing_ms["rc"]) == 1, (
    "тайминг не записан после msp_request(105) — finally не сработал "
    "или cmd 105 не смаплен на 'rc'")
assert t._msp_timing_ms["rc"][0] >= 0.0
print("    msp_request(105) с таймаутом дал None, но записал %.3f мс в "
      "_msp_timing_ms['rc']" % t._msp_timing_ms["rc"][0])

print("\n=== 2. Разные cmd пишутся в разные ключи (по _MSP_CMD_NAMES) ===")
t._msp_timing_ms["att"].clear()
t.msp_request(108)
assert len(t._msp_timing_ms["att"]) == 1, (
    "msp_request(108) не записался в _msp_timing_ms['att']")
print("    msp_request(108) -> _msp_timing_ms['att'], не смешался с 'rc'")

print("\n=== 3. Неизвестный cmd не падает и никуда не пишется ===")
before = {k: len(v) for k, v in t._msp_timing_ms.items()}
t.msp_request(255)  # нет в _MSP_CMD_NAMES
after = {k: len(v) for k, v in t._msp_timing_ms.items()}
assert before == after, (
    "неизвестный cmd записался куда-то в _msp_timing_ms — маппинг "
    "cmd->имя должен молча игнорировать то, чего в нём нет")
print("    msp_request(255) не породил записи ни в одном известном ключе")

print("\n=== 4. fc is None (порт не открыт) — тайминг не пишется вовсе ===")
t.fc = None
t._msp_timing_ms["rc"].clear()
assert t.msp_request(105) is None
assert len(t._msp_timing_ms["rc"]) == 0, (
    "при fc is None msp_request() выходит ДО _msp_t0 = time.monotonic() — "
    "тайминг в этом случае писаться не должен (порт даже не потревожен)")
print("    fc=None -> msp_request() выходит раньше замера, тайминг не пишется")

print("\n=== 5. _percentile() — обычная процентиль отсортированного списка ===")
vals = sorted([10.0, 20.0, 30.0, 40.0, 50.0])
assert t._percentile(vals, 0.0) == 10.0
assert t._percentile(vals, 1.0) == 50.0
assert t._percentile([], 0.5) is None
print("    p0=%.0f p100=%.0f, пустой список -> None"
      % (t._percentile(vals, 0.0), t._percentile(vals, 1.0)))

print("\n=== 6. _msp_timing_report() пишет одну читаемую строку с p50/p90/p99 ===")
for imya in t._msp_timing_ms:
    t._msp_timing_ms[imya].clear()
t._msp_timing_ms["rc"].extend([5.0, 6.0, 7.0, 40.0])
t._msp_send_interval_ms.clear()
t._msp_send_interval_ms.extend([38.0, 40.0, 41.0, 90.0])
_zapisano = []
t.flight_log.event = lambda msg: _zapisano.append(msg)
t._msp_timing_report()
assert len(_zapisano) == 1, "_msp_timing_report() должен писать РОВНО одну строку"
stroka = _zapisano[0]
assert "MSP TRANSPORT" in stroka
assert "rc " in stroka and "p50=" in stroka and "p90=" in stroka and "p99=" in stroka
assert "send_interval_ms" in stroka
print("    " + stroka)

print("\n=== 7. Интервал между успешными SET_RAW_RC — по исходному тексту "
      "(код внутри fc_io_loop, не отдельная функция) ===")
i_send = src.index("sent_ok = send_msp_set_raw_rc(channels)")
i_state_lock = src.index("with state_lock:", i_send)
mezhdu = src[i_send:i_state_lock]
assert "_msp_send_interval_prev_ts" in mezhdu, (
    "интервал между отправками не считается между send и захватом "
    "state_lock — тайминг ищется не там")
assert "if sent_ok:" in mezhdu, (
    "интервал копится независимо от sent_ok — при провале отправки "
    "интервал не должен растягивать статистику успешных кадров")
assert "_msp_send_interval_ms.append(" in mezhdu
print("    интервал копится только при sent_ok, между send и state_lock")

print("\n=== 8. Периодический вызов отчёта — раз в MSP_TIMING_REPORT_PERIOD_S "
      "внутри fc_io_loop ===")
assert "if now >= _msp_timing_report_t:" in src
assert "_msp_timing_report_t = now + MSP_TIMING_REPORT_PERIOD_S" in src
assert "_msp_timing_report()" in src
print("    гейт по времени на месте, отчёт не печатается каждый кадр")

print("\nOK: msp_request() таймингует все пути выхода через finally, "
      "интервал между send'ами и периодический p50/p90/p99-отчёт на месте")
