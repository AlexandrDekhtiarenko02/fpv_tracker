"""Shadow controller не влияет на live-команды — главный acceptance
criterion архитектурного аудита (ветка control-shadow-architecture).

ТЗ: "Поведение аппарата должно быть идентично предыдущему commit. Для
одинаковой записанной последовательности: old global_roll_cmd == new
global_roll_cmd" (и то же для pitch/yaw/throttle). Прямой git-diff двух
ревизий тут не годится — вместо этого проверяем сам инвариант, который и
делает shadow безопасным: РЕЗУЛЬТАТ ОДНОЙ И ТОЙ ЖЕ последовательности
кадров не должен зависеть от того, работает shadow-блок нормально, сломан
исключением, или его вовсе нет. Если это верно — переключение git-ревизий
взад-вперёд физически не может изменить live-выход, потому что сам
shadow-код целиком читает уже посчитанные live-величины и не пишет ни в
одну live-переменную ДО заворачивания в try, а ВЕСЬ shadow-блок стоит
строго ПОСЛЕ commit'а live-команд в state_lock.

Два прогона ОДНОЙ и той же сценарной последовательности кадров с
мокнутыми часами (иначе k=dt_ratio гулял бы по реальному времени между
двумя прогонами и результаты отличались бы не из-за shadow, а из-за
таймингов теста):
  1. Shadow работает как обычно.
  2. _shadow_windup_step намеренно ломается (raise при каждом вызове).
Live-выход (global_roll/pitch/yaw/throttle_cmd + все внутренние
интеграторы/slew-состояние) обязан совпасть кадр-в-кадр.
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

# Сценарий: серия смещений цели от центра — захватывает и стабильный
# трекинг, и резкие скачки (чтобы slew/trust/anti-windup реально
# сработали, а не молчали весь прогон).
SEQUENCE = [
    (0, 0, 0.85), (10, 5, 0.85), (30, 20, 0.85), (60, 40, 0.85),
    (55, 35, 0.20),      # обвал качества -> trust урезает
    (-40, -30, 0.85), (120, 90, 0.85), (150, 110, 0.85),
    (20, 10, 0.85), (0, 0, 0.85),
]


def force_reset():
    """Тот же путь, которым живой контур сбрасывает ВСЁ (roll/pitch/yaw
    интеграторы, slew, shadow-состояние, EMA доверия) между заходами —
    не переизобретаем список глобалов вручную, доверяем существующему,
    уже проверенному сбросу."""
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()


def run_sequence():
    force_reset()
    out = []
    for dx, dy, score in SEQUENCE:
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
            t.app_state["gyro"] = (8, 8, 8)
            t.app_state["imu_ts"] = t.time.monotonic()
        _clk.tick(FRAME_DT)
        t.update_control_from_target()
        out.append((
            t.global_roll_cmd, t.global_pitch_cmd,
            t.global_yaw_cmd, t.global_throttle_cmd,
            t.override_active,
            t.roll_integral, t.pitch_integral, t.yaw_integral,
            t._slew_roll, t._slew_pitch, t._slew_yaw,
        ))
    return out


print("=== 1. Прогон с нормально работающим shadow ===")
_clk.t = 1000.0
results_normal = run_sequence()
print("    %d кадров, финальная команда roll=%.1f pitch=%.1f yaw=%.1f thr=%.1f"
      % ((len(results_normal),) + results_normal[-1][:4]))

print("\n=== 2. Тот же сценарий с намеренно сломанным shadow "
      "(_shadow_windup_step всегда бросает исключение) ===")
_orig_windup = t._shadow_windup_step


def _boom(*a, **k):
    raise RuntimeError("shadow намеренно сломан тестом изоляции")


t._shadow_windup_step = _boom
_clk.t = 1000.0
results_broken = run_sequence()
t._shadow_windup_step = _orig_windup
print("    %d кадров, финальная команда roll=%.1f pitch=%.1f yaw=%.1f thr=%.1f"
      % ((len(results_broken),) + results_broken[-1][:4]))

print("\n=== 3. Live-выход идентичен кадр-в-кадр, работает shadow или "
      "сломан ===")
assert len(results_normal) == len(results_broken) == len(SEQUENCE)
for i, (a, b) in enumerate(zip(results_normal, results_broken)):
    assert a == b, (
        "кадр %d: live-выход разошёлся между нормальным и сломанным "
        "shadow — shadow-блок как-то влияет на live-путь!\n"
        "  нормальный: %s\n"
        "  сломанный:  %s" % (i, a, b))
print("    все %d кадров совпали побитово (cmd, override_active, "
      "интеграторы, slew-состояние)" % len(SEQUENCE))

print("\n=== 4. Сломанный shadow не уронил update_control_from_target() "
      "и не оставил активной диагностики ===")
# После run_sequence() со сломанным _shadow_windup_step последний кадр
# обязан был поймать исключение и честно пометить _shadow_ctl_dbg
# неактивным — а не тихо оставить прошлые (валидные) значения висеть.
assert t._shadow_ctl_dbg.get("active") is False, (
    "после исключения внутри shadow _shadow_ctl_dbg должен стать "
    "{'active': False}, а не хранить успех прошлого кадра")
print("    _shadow_ctl_dbg={'active': False} после сбоя — честно, не "
      "тихо подставляет старое")

print("\n=== 5. По исходному тексту: shadow-блок не пишет ни в одну "
      "live-переменную ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = src.index("# ================= SHADOW CONTROLLER =================")
i_end = src.index("# ================ /SHADOW CONTROLLER ================")
shadow_body = src[i_start:i_end]
_LIVE_NAMES = (
    "global_roll_cmd", "global_pitch_cmd", "global_yaw_cmd",
    "global_throttle_cmd", "override_active", "target_controllable",
    "track_state", "roll_integral", "pitch_integral", "yaw_integral",
    "_slew_roll", "_slew_pitch", "_slew_yaw",
    "_dover_score_ema", "_dover_psr_ema",
)
for name in _LIVE_NAMES:
    # Присваивание (не просто чтение): "name =" или "name +=" и т.п., не
    # часть более длинного идентификатора (roll_integral не должен матчить
    # _shadow_roll_integral).
    for op in (" = ", " += ", " -= ", " *= ", " /="):
        pattern = name + op
        idx = 0
        while True:
            idx = shadow_body.find(pattern, idx)
            if idx < 0:
                break
            # Убедиться, что перед найденным именем нет буквы/подчёркивания
            # (то есть это не суффикс более длинного имени вроде
            # _shadow_roll_integral для roll_integral).
            before = shadow_body[idx - 1] if idx > 0 else " "
            assert not (before.isalnum() or before == "_"), (
                "shadow-блок присваивает live-переменной %s (%r) — "
                "нарушение изоляции shadow/live" % (name, pattern))
            idx += 1
print("    ни одно из %d live-имён (global_*_cmd, override_active, "
      "интеграторы, slew, EMA доверия) не присваивается внутри "
      "shadow-блока" % len(_LIVE_NAMES))

print("\nOK: shadow controller не влияет на live-команды ни при штатной "
      "работе, ни при внутреннем сбое, и не пишет в live-переменные "
      "по исходному тексту")
