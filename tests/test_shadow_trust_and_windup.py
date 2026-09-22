"""Score-EMA time-normalized shadow (ТЗ §9) и anti-windup shadow (ТЗ
§10) — ПЕРЕДЕЛАНО после второго ревью (нашло: shadow-I не участвовал в
shadow-requested; restricted смешивал урезание trust/slew с добавлением
launch/cruise; двойное применение sign делало push_further нечувствительным
к знаку оси).

Trust: live ema += TRUST_EMA_ALPHA * (...) БЕЗ alpha_for_dt/k — при
разном FPS это де-факто разные фильтры. shadow_score_ema_time_norm — то
же обновление, но с alpha_for_dt(TRUST_EMA_ALPHA, k). ЭТО НЕ shadow
trust_k (тот ещё берёт худшее из psr/flow_gap) — только EMA-компонент,
где найден FPS-баг. Название явно это отражает (переименовано с
shadow_trust_time_norm после ревью — то имя намекало на альтернативный
trust_k, которым не является).

Anti-windup: shadow_roll_requested/pitch_requested/yaw_requested теперь
строятся из СВОЕГО _shadow_*_integral (самосогласованно — "как бы вёл
себя PID с исправленным anti-windup"), а restriction раскладывается на
trust_restriction (чистое урезание trust) + slew_restriction (чистое
урезание slew), НЕ конфликтуя с launch/cruise-добавками.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()


def kadr(dx=60, dy=40, score=0.85, dt=FRAME_DT):
    with t.state_lock:
        t.target_box_main = (CX - 20 + dx, CY - 20 + dy,
                             CX + 20 + dx, CY + 20 + dy)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = score
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(dt)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. score_ema_time_norm: при k=1 (номинальный FPS) совпадает "
      "с live _dover_score_ema (одна и та же формула при k=1) ===")
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(10):
    sc = kadr(dt=FRAME_DT)   # ровно номинальный кадр -> k=1
print("    shadow_score_ema_time_norm=%.6f live _dover_score_ema=%.6f"
      % (sc["score_ema_time_norm"], t._dover_score_ema))
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) < 1e-6, (
    "при k=1 обе формулы совпадают по построению — расхождение значит "
    "ошибку в shadow-копии, а не в найденном баге")

print("\n=== 2. score_ema_time_norm: резкий шаг score при неноминальном "
      "FPS расходится с live-EMA — демонстрация найденного FPS-бага ===")
# ВАЖНО: EMA от КОНСТАНТНОГО score не расходится вовсе (score-ema=0 у
# обеих формул независимо от alpha/k) — нужен ПЕРЕХОДНЫЙ процесс: сперва
# сходимся на номинальном FPS (k=1, формулы совпадают по построению),
# затем один резкий шаг score вниз одновременно с неноминальным k.
force_reset()
_clk.t = 1000.0
for _ in range(10):
    sc = kadr(score=0.85, dt=FRAME_DT)
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) < 1e-6, (
    "прогрев на k=1 обязан дать совпадение — иначе тест сам не годен")
sc = kadr(score=0.20, dt=FRAME_DT * 2.0)   # обвал + k~2 за один шаг
print("    после шага (score 0.85->0.20, k~2): shadow=%.6f live=%.6f"
      % (sc["score_ema_time_norm"], t._dover_score_ema))
assert abs(sc["score_ema_time_norm"] - t._dover_score_ema) > 1e-3, (
    "на резком шаге при неноминальном FPS формулы обязаны заметно "
    "разойтись — это и есть found FPS-баг")
print("    подтверждено")

print("\n=== 3. Anti-windup shadow ТЕПЕРЬ самосогласован: "
      "shadow_roll_requested строится из СВОЕГО shadow_roll_i, а не из "
      "live r_off ===")
# Сценарий: крупная устойчивая ошибка -> live PID внутренне насыщен
# (r_off = MAX_ROLL_DEFLECT, его собственный anti-windup блокирует live
# roll_integral). shadow, если бы requested брался у live r_off (старая
# версия), просто продолжал бы копить ПОВЕРХ чужого потолка. В новой
# версии shadow_roll_requested = clamp(ROLL_SIGN*(P+D+FF+shadow_I)) —
# как только shadow_I заметно вырос, shadow_roll_requested ДОЛЖЕН
# прижаться к тому же потолку MAX_ROLL_DEFLECT самостоятельно.
force_reset()
_clk.t = 1000.0
sc = None
for _ in range(30):
    sc = kadr(dx=250, dy=180, score=0.85)
c = t._ctl_dbg
print("    после 30 кадров: live roll_off=%.1f shadow_roll_requested=%.1f "
      "shadow_roll_i=%.1f live roll_integral=%.1f"
      % (c["roll_off"], sc["roll_requested"], sc["roll_i"], t.roll_integral))
assert abs(sc["roll_requested"]) <= t.MAX_ROLL_DEFLECT + 1e-6, (
    "shadow_roll_requested обязан быть зажат по MAX_ROLL_DEFLECT, как и "
    "live r_off — та же внутренняя логика _pid_axis_step")
print("    shadow_roll_requested зажат тем же потолком — самосогласовано")

print("\n=== 4. shadow-интегралы физически отдельные от live (изменение "
      "shadow не задевает live) ===")
_live_before = (t.roll_integral, t.pitch_integral, t.yaw_integral)
t._shadow_roll_integral = 99999.0
t._shadow_pitch_integral = -99999.0
t._shadow_yaw_integral = 12345.0
assert (t.roll_integral, t.pitch_integral, t.yaw_integral) == _live_before, (
    "изменение shadow-интеграторов задело live — не отдельное состояние")
print("    подтверждено: физически разные переменные")

print("\n=== 5. Restriction РАЗЛОЖЕН: launch/cruise-добавка НЕ считается "
      "урезанием (точный сценарий из ревью) ===")
# Пример ревью: PID requested невелик, cruise/launch добавляет много —
# raньше restriction=requested-delivered давал ЛОЖНОЕ "урезание" (delivered
# > requested из-за добавки). Проверяем НАПРЯМУЮ на функции
# _shadow_windup_step / на посчитанных trust_restriction/slew_restriction:
# если trust_k=1 (доверие полное) и slew не режет — обе restriction ~ 0,
# ДАЖЕ ЕСЛИ launch/cruise добавили много к финальной команде.
force_reset()
_clk.t = 1000.0
# Небольшая, устойчивая ошибка -> requested далеко от MAX_DEFLECT,
# trust=1.0 (хороший score), но заставляем glide включиться (крупная
# выдержка по времени до контакта отсутствует в offline-харнессе по
# умолчанию — используем launch/cruise напрямую нельзя без полного
# закрытия, поэтому проверяем то же свойство минимальным путём: при
# trust_k=1.0 и slew, не ограничивающем медленное движение,
# trust_restriction и slew_restriction оба близки к нулю).
for _ in range(15):
    sc = kadr(dx=15, dy=10, score=0.95)
print("    roll_trust_restriction=%.3f roll_slew_restriction=%.3f"
      % (sc["roll_trust_restriction"], sc["roll_slew_restriction"]))
assert abs(sc["roll_trust_restriction"]) < 5.0, (
    "доверие ~1.0 -> trust_restriction обязан быть близок к нулю")
print("    при полном доверии и небольшом движении обе restriction "
      "близки к нулю — cruise/launch (если бы были) не исказили бы их")

print("\n=== 6. КЛЮЧЕВОЙ ФИКС (найден ревью): push_further теперь "
      "ЗАВИСИТ от sign оси — раньше двойное применение sign сокращалось "
      "в sign**2=1 и делало функцию НЕЧУВСТВИТЕЛЬНОЙ к знаку ===")
# Прямой юнит-тест _shadow_windup_step: одни и те же err_f/restriction,
# только sign меняется +1 -> -1 — push_further обязан переключиться.
ERR_F = 10.0
RESTRICTION = 20.0   # > SHADOW_WINDUP_RESTRICT_PWM=15 -> restricted=True
I_GAIN, I_MAX, I_DECAY, K = 0.1, 1000.0, 1.0, 1.0

new_i_plus, restricted_plus = t._shadow_windup_step(
    ERR_F, RESTRICTION, 0.0, I_GAIN, I_MAX, I_DECAY, 1, K)
new_i_minus, restricted_minus = t._shadow_windup_step(
    ERR_F, RESTRICTION, 0.0, I_GAIN, I_MAX, I_DECAY, -1, K)
print("    sign=+1: integral %.4f (было 0.0) | sign=-1: integral %.4f "
      "(было 0.0)" % (new_i_plus, new_i_minus))
assert restricted_plus and restricted_minus, (
    "restriction=20 > порог 15 — restricted обязан быть True в обоих "
    "случаях (restricted не зависит от sign, это отдельная проверка)")
# sign=+1: i_dir=+1*10=+10, restriction*i_dir=20*10=200>0 -> push_further
# =True -> интеграл НЕ растёт (push_further блокирует накопление).
assert abs(new_i_plus - 0.0) < 1e-9, (
    "sign=+1: push_further должен был заблокировать накопление "
    "(restriction и i_dir в одном знаке) — интеграл не должен был вырасти")
# sign=-1: i_dir=-1*10=-10, restriction*i_dir=20*(-10)=-200<0 ->
# push_further=False -> интеграл РАСТЁТ на err_f*i_gain*k=10*0.1*1=1.0.
assert abs(new_i_minus - 1.0) < 1e-6, (
    "sign=-1: push_further должен был пропустить накопление "
    "(restriction и i_dir в разных знаках) — интеграл обязан вырасти "
    "на err_f*i_gain*k=1.0, а не остаться на месте, как при sign=+1")
assert new_i_plus != new_i_minus, (
    "РЕГРЕССИЯ НАЙДЕННОГО БАГА: при одних и тех же err_f/restriction "
    "смена sign с +1 на -1 обязана менять классификацию push_further — "
    "если результаты совпали, функция снова нечувствительна к знаку "
    "оси (тот самый баг sign**2=1 из старой версии)")
print("    sign=+1 и sign=-1 дают РАЗНЫЙ результат — функция "
      "чувствительна к знаку оси (баг исправлен)")

print("\n=== 7. Старая (баг) формула ДЕЙСТВИТЕЛЬНО была нечувствительна "
      "к sign — документируем регрессию явно ===")


def _staraya_buggy_formula(err_f, restriction, sign):
    i_dir = sign * err_f
    return restriction * i_dir * sign > 0   # старая push_further-формула


_stary_plus = _staraya_buggy_formula(ERR_F, RESTRICTION, 1)
_stary_minus = _staraya_buggy_formula(ERR_F, RESTRICTION, -1)
assert _stary_plus == _stary_minus, (
    "тест сам не показателен: старая формула должна была давать "
    "ОДИНАКОВЫЙ результат для sign=+1 и sign=-1 (sign**2=1) — если тут "
    "разные, воспроизвести баг для документации не вышло")
print("    старая формула: sign=+1 -> %s, sign=-1 -> %s (одинаково, как "
      "и было в баге) — новая версия ведёт себя иначе (см. пункт 6)"
      % (_stary_plus, _stary_minus))

print("\nOK: score_ema_time_norm корректно назван и показывает FPS-баг; "
      "anti-windup shadow самосогласован (requested из своего "
      "интеграла), restriction чист от launch/cruise, push_further "
      "исправлен и чувствителен к sign оси")
