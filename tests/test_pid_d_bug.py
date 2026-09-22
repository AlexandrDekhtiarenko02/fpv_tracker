"""D в _pid_dbg должен совпадать с D, реально участвующим в команде
(диагностический коммит, PID/anti-windup не менялся).

Сам контур считал верно: pd_part = err*p_gain + (d_err/k)*d_gain. Но
_pid_dbg[dbg_key] записывал D как d_err*d_gain — БЕЗ деления на k. При
k != 1 (а k != 1 почти всегда, см. dt_ratio — реальный интервал между
кадрами гуляет) колонка roll_d/pitch_d в CSV показывала D, которого в
реальной команде не было: разбор лога мог указать на "слишком резкий D"
и увести чинить то, чего в команде физически не было.

Проверяется напрямую вызовом _pid_axis_step с k, заметно отличным от 1 —
иначе расхождение было бы слишком маленьким, чтобы отличить от ошибки
округления.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

P_GAIN = 2.0
D_GAIN = 5.0
I_GAIN = 0.1
FF_GAIN = 0.0
K = 2.5  # заметно не 1 — при 24 fps соответствует ~10 fps кадру


def one_step(error, prev_error, k=K):
    out, new_prev, integral = t._pid_axis_step(
        error, prev_error, 0.0, 0.0,
        P_GAIN, D_GAIN, I_GAIN, FF_GAIN,
        integral_max=1000.0, integral_decay=1.0,
        sign=1, max_deflect=500.0, dbg_key="test_axis", k=k,
    )
    return out, t._pid_dbg["test_axis"]


print("=== 1. D в _pid_dbg = (d_err/k)*d_gain, НЕ d_err*d_gain ===")
error, prev_error = 20.0, 5.0
d_err = error - prev_error
out, dbg = one_step(error, prev_error)
p_logged, d_logged, i_logged, ff_logged, out_logged, sat_logged = dbg

d_correct = (d_err / K) * D_GAIN
d_bug = d_err * D_GAIN
print("    d_err=%.1f K=%.1f -> D в логе %.4f (верно %.4f, старый баг "
      "дал бы %.4f)" % (d_err, K, d_logged, d_correct, d_bug))
assert abs(d_logged - d_correct) < 1e-9, (
    "D в _pid_dbg не совпадает с (d_err/k)*d_gain — логируемое значение "
    "снова разошлось с тем, что реально участвует в pd_part")
assert abs(d_logged - d_bug) > 1e-6, (
    "тест сам по себе не различает верное и ошибочное значение при "
    "выбранных P_GAIN/D_GAIN/K — числа совпали случайно")

print("\n=== 2. P в _pid_dbg не тронут (err_f * p_gain, без k) ===")
assert abs(p_logged - error * P_GAIN) < 1e-9, (
    "P в _pid_dbg неожиданно изменился — P и так не делится на k, "
    "трогать эту часть было не нужно")

print("\n=== 3. out самосогласован с P+D+I+FF из того же снимка ===")
# out — это РЕАЛЬНАЯ команда (P+D+I+FF, здесь без насыщения — далеко от
# max_deflect=500). Собираем её из уже залогированных P/D/I/FF: если это
# совпадает, значит фикс логирования не расходится с тем, что реально
# участвует в команде — не пересчитываем internal saturation/anti-windup
# логику заново, доверяем самому _pid_axis_step.
out_expected = p_logged + d_logged + i_logged + ff_logged
assert abs(out) < 500.0 - 1e-6, (
    "тест сам по себе не показателен: out насыщен по max_deflect, "
    "самосогласованность с P+D+I+FF так не проверить")
assert abs(out - out_expected) < 1e-6, (
    "out не равен сумме залогированных P+D+I+FF — фикс логирования "
    "разошёлся с тем, что реально участвует в команде")
assert abs(out_logged - out) < 1e-9, (
    "out в _pid_dbg не совпадает с реально возвращённым out")

print("\n=== 4. При k=1 старая и новая формулы совпадают (нет регресса "
      "на номинальном кадре) ===")
_, dbg1 = one_step(error, prev_error, k=1.0)
d_at_k1 = dbg1[1]
assert abs(d_at_k1 - d_err * D_GAIN) < 1e-9, (
    "при k=1 (d_err/k)*d_gain обязан совпасть с d_err*d_gain — если нет, "
    "деление на k само по себе сломано")
print("    k=1: D в логе %.4f == d_err*d_gain %.4f" % (d_at_k1, d_err * D_GAIN))

print("\nOK: D в логе совпадает с D, реально участвующим в команде, "
      "P и итоговый out не задеты")
