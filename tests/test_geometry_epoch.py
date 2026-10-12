"""Валидность temporal scale history (ТЗ next-commit spec §10).

Любые производные от размера/расширения (tau, LOS-rate, азимут-rate, рост
коробки) корректны только при НЕПРЕРЫВНОЙ геометрии одного и того же лока.
geometry_epoch растёт на каждый такой разрыв — новый лок (потеря/повторный
захват) ИЛИ пауза ВНУТРИ одного лока, когда camera_callback застрял
(SD-карта, тепловой throttling), но track_state не упал в LOST.

Второй случай — самое важное здесь. Разрыв внутри лока обязан очистить
derivative-историю (иначе следующий кадр посчитает скорость по интервалу
в секунды, а не в кадр — ложный выброс tau_ubyv/los_rate), но НЕ ИМЕЕТ
ПРАВА тронуть control-state (PID, override, launch): аппарат должен
продолжать лететь той же командой, а не дёргаться из-за паузы в трекере.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y
MASHTAB = float(t.MAIN_W) / float(t.LORES_W)
FRAME_DT = 0.043


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt=FRAME_DT):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic


def frame(dt=FRAME_DT, dx_px=0, dy_px=10, rost=1.5):
    _clk.tick(dt)
    t.lock_w0 = t.lock_h0 = 20.0
    storona = 20.0 * MASHTAB * rost
    pol = storona / 2.0
    t.target_box_main = (int(CX - pol + dx_px), int(CY - pol + dy_px),
                         int(CX + pol + dx_px), int(CY + pol + dy_px))
    t.target_visible = True
    t.target_controllable = True
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 20.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["alt_cm"] = 3000
        t.app_state["alt_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    t.update_control_from_target()


def not_controllable_frame():
    _clk.tick()
    with t.state_lock:
        t.target_box_main = None
        t.target_controllable = False
    t.update_control_from_target()


print("=== A. Первый лок за сессию НЕ поднимает эпоху (нечего разрывать), "
      "обычные кадры — тоже нет ===")
# 30 кадров ПОДРЯД вне управления (типичная затянувшаяся ACQ/HOLD/LOST,
# acceptance test из review п.3) не должны плодить эпоху на каждый кадр —
# только на РЕАЛЬНОЙ границе controllable -> not-controllable. Старт
# сессии никогда controllable не был, поэтому границы тут вообще нет.
for _ in range(30):
    not_controllable_frame()
e0 = t.geometry_epoch
assert e0 == 0, (
    "30 кадров подряд вне управления без единого реального перехода "
    "подняли эпоху до %d — должна была остаться на стартовом 0" % e0)
frame()
e1 = t.geometry_epoch
assert e1 == e0, (
    "первый лок за сессию поднял geometry_epoch (%d -> %d), хотя "
    "разрывать было нечего — до этого не было ни кадра валидной геометрии"
    % (e0, e1))
for _ in range(10):
    frame()
assert t.geometry_epoch == e1, (
    "geometry_epoch меняется на обычных кадрах без разрыва (%d -> %d)"
    % (e1, t.geometry_epoch))
print("    30 кадров вне управления и лок держат эпоху на %d, 10 обычных "
      "кадров подряд — тоже" % e1)

print("\n=== B. Разрыв ВНУТРИ лока: эпоха растёт, история чистится, "
      "control-state цел ===")
for _ in range(5):
    frame(dx_px=15, dy_px=15)
assert len(t._size_hist) > 0, (
    "тест сам по себе негоден: _size_hist не накопился за 5 кадров")
tracked_since_before = t._tracked_since_t
override_before = t.override_active
roll_cmd_before = t.global_roll_cmd
assert override_before, "override должен быть активен перед разрывом"
assert roll_cmd_before != 1500.0, (
    "тест сам по себе негоден: команда крена уже на нейтрали (%.1f) — "
    "боковое смещение цели не создало ошибки, разрыв нейтрали ничего не "
    "докажет" % roll_cmd_before)

# Разрыв: пауза больше FLOW_RASSH_SVEZH_S, лок НЕ теряем (controllable
# остаётся True всё это время — track_state в реальном коде тоже не падает).
frame(dt=t.FLOW_RASSH_SVEZH_S + 0.10, dx_px=15, dy_px=15)

print("    geometry_epoch: %d -> %d" % (e1, t.geometry_epoch))
assert t.geometry_epoch == e1 + 1, (
    "разрыв внутри лока не поднял geometry_epoch (%d -> %d)"
    % (e1, t.geometry_epoch))
print("    _size_hist после разрыва: %d записей (было %d)"
      % (len(t._size_hist), 5))
assert len(t._size_hist) <= 1, (
    "_size_hist пережил разрыв геометрии — derivative посчитает скорость "
    "по интервалу в секунды, а не по кадру")
assert len(t._tau_hist) <= 1, "_tau_hist пережил разрыв геометрии"

# ГЛАВНАЯ ПРОВЕРКА: control-state НЕ тронут узким сбросом.
assert t.override_active, (
    "override_active сброшен в False — разрыв геометрии прошёл через "
    "ПОЛНЫЙ сброс лока (ветку not-controllable), а не через узкий "
    "_reset_geometry_history")
assert t._tracked_since_t == tracked_since_before, (
    "_tracked_since_t перезаписан (%.3f -> %.3f) — это происходит только "
    "в ПОЛНОМ сбросе лока; разрыв внутри лока не должен считать заход "
    "заново захваченным" % (tracked_since_before, t._tracked_since_t))
print("    override_active цел, _tracked_since_t не тронут — "
      "control-state пережил разрыв без сброса")

print("\n=== C. Потеря лока поднимает эпоху РОВНО ОДИН РАЗ, а не на "
      "каждый кадр LOST ===")
e2 = t.geometry_epoch
# Граница переход в not-controllable — эпоха растёт здесь и только здесь.
not_controllable_frame()
assert t.geometry_epoch == e2 + 1, (
    "переход в not-controllable не поднял geometry_epoch (%d -> %d)"
    % (e2, t.geometry_epoch))
e2b = t.geometry_epoch
# Ещё 10 кадров подряд БЕЗ лока — та самая затянувшаяся LOST/ACQ. Эпоха
# не должна расти на каждый из них: граница уже пройдена один раз.
for _ in range(10):
    not_controllable_frame()
assert t.geometry_epoch == e2b, (
    "10 кадров подряд вне управления после уже случившегося разрыва "
    "снова подняли эпоху (%d -> %d) — граница не edge-triggered"
    % (e2b, t.geometry_epoch))
# Новый захват сам по себе тоже не поднимает эпоху (см. сценарий A) —
# только граница ДО него уже подняла её ровно на единицу.
frame()
assert t.geometry_epoch == e2b, (
    "новый захват поднял geometry_epoch (%d -> %d) в дополнение к уже "
    "случившейся границе" % (e2b, t.geometry_epoch))
print("    эпоха: %d -> %d на границе, держится на %d все 10 кадров LOST "
      "и на новом захвате" % (e2, e2b, e2b))

print("\nOK: geometry_epoch отмечает РЕАЛЬНЫЕ разрывы (границу потери "
      "лока и паузу внутри лока) РОВНО ОДИН РАЗ каждый, а не всё время, "
      "пока состояние остаётся невалидным; derivative-история чистится, "
      "control-state цел")
