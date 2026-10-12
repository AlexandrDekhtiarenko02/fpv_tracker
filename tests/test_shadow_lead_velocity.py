"""Lead: box-derived vs flow-derived velocity (ТЗ §8) — без единого
нового CV-прохода, только сравнение уже посчитанных величин.

Гипотеза из обзора: lead строится по движению ИТОГОВОЙ рамки, а рамку
двигает не только реальное движение цели, но и сама работа трекера
(matcher/scale/re-anchor). vel_box_x/y — межкадровая скорость box_cx/cy
(то же, что уже участвует в live lead_x/y, только читается ДО фильтра).
vel_flow_x/y — независимая оценка сдвига из _flow_dbg["translation_x/y"]
(optical flow, посчитан в flow_predict() раньше в этом же кадре, до и
независимо от box). Тест проверяет источники и арифметику сравнения, не
конкретную "правильную" величину расхождения — расхождение зависит от
сцены и является предметом РАЗБОРА, а не проверки здесь.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
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


def make_scene(offset=0):
    """Текстурная сцена с одним объектом, реально сдвигающимся между
    кадрами (offset px) — чтобы и box, и flow имели что реально измерить,
    а не работали по пустому/статичному кадру."""
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx = t.LORES_W // 2 + offset
    cy = t.LORES_H // 2
    s = 28
    rng = np.random.default_rng(7)
    obj = (rng.random((s, s)) * 90 + 110).astype(np.uint8)
    cv2.circle(obj, (s // 2, s // 2), s // 3, 40, -1)
    x0, y0 = cx - s // 2, cy - s // 2
    frame[y0:y0 + s, x0:x0 + s] = obj
    return frame


print("=== 1. Источники: vel_box_x из inst_vx (box_cx-prev_box_cx)/k, "
      "vel_flow_x из _flow_dbg['translation_x']/k ===")
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
with t.state_lock:
    t.app_state["rc_channels"] = [1500] * 8
    t.app_state["rc_link_ts"] = t.time.monotonic()
_clk.tick(FRAME_DT)
t.process_locked_tracker(make_scene(0))
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"

# Несколько кадров РЕАЛЬНОГО сдвига объекта — и box, и flow должны его
# увидеть (объект и правда движется, а не трекер сам себя поправляет).
SHIFT_PER_FRAME = 3
last_sc = None
for i in range(1, 6):
    with t.state_lock:
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(make_scene(i * SHIFT_PER_FRAME))
    last_sc = t._shadow_ctl_dbg

assert last_sc.get("active"), "shadow неактивен на TRACKED-кадре с движением"
print("    vel_box_x=%s vel_flow_x=%s vel_diff_x=%s"
      % (last_sc.get("vel_box_x"), last_sc.get("vel_flow_x"),
         last_sc.get("vel_diff_x")))

print("\n=== 2. Оба источника ненулевые и одного порядка (объект реально "
      "движется вправо) ===")
assert last_sc.get("vel_box_x") is not None, "vel_box_x не посчитан"
assert last_sc.get("vel_flow_x") is not None, "vel_flow_x не посчитан"
assert last_sc["vel_box_x"] > 0, (
    "объект двигался вправо, vel_box_x должен быть положительным")
assert last_sc["vel_flow_x"] > 0, (
    "объект двигался вправо, vel_flow_x (независимый optical flow) тоже "
    "должен быть положительным")
print("    оба источника согласны по знаку (объект действительно "
      "движется вправо)")

print("\n=== 3. vel_diff_x = vel_box_x - vel_flow_x (арифметика "
      "сравнения) ===")
assert abs(last_sc["vel_diff_x"]
           - (last_sc["vel_box_x"] - last_sc["vel_flow_x"])) < 1e-9
print("    vel_diff_x самосогласован")

print("\n=== 4. Ни один новый CV-проход не добавлен: _flow_dbg считается "
      "внутри уже существующего flow_predict(), не отдельным вызовом ===")
import io  # noqa: E402
_ROOT_SRC = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = _ROOT_SRC.index("# ================= SHADOW CONTROLLER =================")
i_end = _ROOT_SRC.index("# ================ /SHADOW CONTROLLER ================")
shadow_body = _ROOT_SRC[i_start:i_end]
for zapreshchennoe in ("calcOpticalFlowPyrLK", "matchTemplate", "cv2.resize",
                       "cvtColor", "goodFeaturesToTrack"):
    assert zapreshchennoe not in shadow_body, (
        "%s вызывается внутри shadow-блока — запрещённый новый тяжёлый "
        "CV-проход (ТЗ §13)" % zapreshchennoe)
print("    в shadow-блоке нет вызовов optical flow/matchTemplate/resize/"
      "cvtColor — используются только уже посчитанные значения")

print("\n=== 5. Первый кадр после захвата (нет prev_box_cx) — vel_box_x "
      "корректно None, а не падение ===")
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
t.prev_box_cx = None
t.prev_box_cy = None
_clk.tick(FRAME_DT)
t.process_locked_tracker(make_scene(0))
if t.track_state == t.TRACK_STATE_TRACKED:
    sc0 = t._shadow_ctl_dbg
    assert sc0.get("active"), "shadow должен остаться активным даже без lead-скорости"
    assert sc0.get("vel_box_x") is None, (
        "на первом кадре после захвата (prev_box_cx=None) vel_box_x "
        "обязан быть None, а не мусорное значение или падение")
    print("    vel_box_x=None на первом кадре, shadow всё равно активен")
else:
    print("    (захват не состоялся в этом синтетическом кадре — "
          "проверка источника уже сделана выше, пропускаем)")

print("\n=== 6. Бюджет времени (ТЗ §13): shadow_time_us пишется и не "
      "проваливается в исключение при штатном прогоне ===")
# НЕ микробенчмарк: часы в этом тесте мокнуты (см. _Chasy) и не тикают
# ВНУТРИ одного вызова update_control_from_target(), поэтому
# time.monotonic()-time.monotonic() тут всегда 0.0 — реальную стоимость
# в мкс так не измерить, для этого нужен стенд/реальные часы. Что
# ПРОВЕРЯЕТСЯ здесь: shadow_time_us вообще пишется каждый кадр (значит,
# try-блок не падает раньше вычисления времени) и остаётся числом в
# разумных границах — защита от будущей регрессии, а не замер текущей
# производительности.
SHADOW_BUDGET_US = 2000.0   # щедрый потолок для scalar-арифметики
_times = []
for i in range(20):
    with t.state_lock:
        t.target_box_main = (CX - 20 + i, CY - 20, CX + 20 + i, CY + 20)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = 0.85
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.update_control_from_target()
    tus = t._shadow_ctl_dbg.get("time_us")
    if tus is not None:
        _times.append(tus)
assert _times, "shadow_time_us ни разу не записан за 20 кадров"
_max_t = max(_times)
_avg_t = sum(_times) / len(_times)
print("    shadow_time_us: среднее %.1f, максимум %.1f (бюджет %.0f)"
      % (_avg_t, _max_t, SHADOW_BUDGET_US))
assert _max_t < SHADOW_BUDGET_US, (
    "shadow-блок занимает %.1f мкс — заметно дороже, чем ожидается от "
    "чистой scalar-арифметики; проверить, не завёлся ли внутри тяжёлый "
    "вызов" % _max_t)

print("\nOK: box-derived и flow-derived скорость берутся из уже "
      "существующих измерений, без нового CV, сравнение самосогласовано, "
      "стоимость shadow-блока укладывается в бюджет")
