"""VISUAL_UNSTABLE — camera jump/top_saturated отключают automation ДЛЯ
ЭТОГО КАДРА, не только пишутся в лог (отчёт 25.09, п.5).

КОНТЕКСТ. Camera Jump Shadow (66b7f4b..fe02d95) был чисто диагностическим:
cam_jump/top_saturated считались раз в секунду и писались в CSV/events.log,
но НИКАК не влияли на target_controllable — прямая цитата отчёта: "Camera
Jump Shadow fix was diagnostic only, did not fix AE itself". Эта правка не
трогает AE/AWB (остаются полностью динамическими) — только заставляет
control реагировать на уже существующий сигнал их нестабильности.

АРХИТЕКТУРНО ОТЛИЧАЕТСЯ ОТ IDENTITY_UNCERTAIN (п.2 того же отчёта):
IDENTITY_UNCERTAIN персистентен (нужен explicit reset_tracking) — вопрос
идентичности цели. VISUAL_UNSTABLE — вопрос доверия к ПИКСЕЛЯМ конкретного
момента: gate читается заново каждый кадр из _cam_shadow_dbg (тот же
freshness-принцип, что уже используют cam_jump_* колонки — возраст против
CAM_JUMP_MAX_VALID_DT_S, ни одного нового порога), и как только свежий
замер снова показывает стабильную картинку, track_state сам возвращается в
TRACKED — БЕЗ explicit pilot-действия.
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()


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


def make_scene(seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


def stable_cam():
    t._cam_shadow_dbg = {
        "sample_t": _clk.t, "jump": False, "top_saturated": False}


def unstable_cam(reason="jump", age_s=0.0):
    d = {"sample_t": _clk.t - age_s, "jump": False, "top_saturated": False}
    d[reason] = True
    t._cam_shadow_dbg = d


assert t.VISUAL_UNSTABLE_ENABLED, "тест сам по себе негоден без ENABLED"
MAX_DT = t.CAM_JUMP_MAX_VALID_DT_S

print("=== 1. _visual_unstable_now: чистая функция, граничные случаи ===")
assert t._visual_unstable_now({}, 100.0) == (False, ""), (
    "пустой _cam_shadow_dbg (нет sample_t) не должен считаться "
    "нестабильным — недостаточно данных, не 'стабильно' и не "
    "'нестабильно', но controllable отключать не за что")
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": True, "top_saturated": False}, 100.0
) == (True, "jump")
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": False, "top_saturated": True}, 100.0
) == (True, "top_saturated")
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": True, "top_saturated": True}, 100.0
) == (True, "jump"), "jump проверяется первым, если оба флага одновременно"
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": False, "top_saturated": False}, 100.0
) == (False, "")
# Свежесть: РОВНО на границе ещё доверяем (та же граница, что и dt_valid
# внутри _camera_jump_check: dt_s <= CAM_JUMP_MAX_VALID_DT_S).
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": True, "top_saturated": False},
    100.0 + MAX_DT
) == (True, "jump"), "граница включительно должна ещё доверять замеру"
assert t._visual_unstable_now(
    {"sample_t": 100.0, "jump": True, "top_saturated": False},
    100.0 + MAX_DT + 0.001
) == (False, ""), "замер старше CAM_JUMP_MAX_VALID_DT_S не должен учитываться"
print("    None-sample_t / jump / top_saturated / оба сразу / граница "
      "свежести — все корректны")

print("\n=== 2. Camera jump: свежий замер отключает controllable ДЛЯ ЭТОГО "
      "КАДРА, без reanchor, БЕЗ явного reset ===")
capture()
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
_lock_before = (t.lock_cx, t.lock_cy)
_prev_gray_before = t.prev_gray
_events = []
t.flight_log.event = _events.append
unstable_cam("jump")
tick()
assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE, (
    "track_state=%r после свежего camera jump, ожидали VISUAL_UNSTABLE"
    % t.track_state)
with t.state_lock:
    _controllable_2 = t.target_controllable
assert not _controllable_2, "controllable остался True при camera jump"
assert (t.lock_cx, t.lock_cy) == _lock_before, (
    "lock_cx/cy сдвинулись на кадре, чьим пикселям не доверяем")
assert t.prev_gray is _prev_gray_before, (
    "prev_gray обновился НА кадре нестабильности — заразил бы flow "
    "следующего хорошего кадра плохими пикселями")
# geometry_epoch МОЖЕТ вырасти на этом кадре — это _reset_geometry_
# history("not_controllable"), существующий, общий для ЛЮБОЙ потери
# controllable механизм (тот же, что уже отрабатывает на HOLD/LOST/ACQ/
# IDENTITY_UNCERTAIN), не reanchor. Проверяем именно ОТСУТСТВИЕ REANCHOR-
# события — это и есть признак того, что позиция НЕ была подтверждена
# как новая точка привязки.
_reanchor_events = [e for e in _events if e.startswith("REANCHOR")]
assert not _reanchor_events, (
    "REANCHOR всё-таки случился при срабатывании VISUAL_UNSTABLE: %s"
    % _reanchor_events)
_vu_events = [e for e in _events if e.startswith("VISUAL_UNSTABLE")]
assert len(_vu_events) == 1
print("    событие: %s; REANCHOR не вызывался, lock/prev_gray не тронуты"
      % _vu_events[0])

print("\n=== 3. Camera jump: САМО-ВОССТАНОВЛЕНИЕ на следующем свежем "
      "стабильном замере — БЕЗ explicit reset_tracking() (в отличие от "
      "IDENTITY_UNCERTAIN/nudge-abort) ===")
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "track_state=%r после того, как замер снова стабилен — ожидали "
    "самостоятельное возвращение в TRACKED" % t.track_state)
with t.state_lock:
    _controllable_3 = t.target_controllable
assert _controllable_3, (
    "controllable не вернулся в True сам, хотя замер снова стабилен")
print("    один стабильный кадр -> TRACKED, controllable=True, без "
      "единого reset_tracking()")

print("\n=== 4. top_saturated (без jump) триггерит НАРАВНЕ ===")
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
unstable_cam("top_saturated")
tick()
assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE
with t.state_lock:
    assert not t.target_controllable
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED
print("    top_saturated отключает controllable так же, как jump, и "
      "так же само разрешается")

print("\n=== 5. Устаревший замер (старше CAM_JUMP_MAX_VALID_DT_S) НЕ "
      "отключает controllable — freshness gate реально работает в "
      "полном пайплайне, не только в юнит-тесте чистой функции ===")
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
unstable_cam("jump", age_s=MAX_DT + 1.0)
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "устаревший замер jump всё равно отключил controllable")
with t.state_lock:
    assert t.target_controllable
print("    устаревший (age > CAM_JUMP_MAX_VALID_DT_S) jump проигнорирован")

print("\n=== 6. Нет всплеска lost_frames/LOST при ЗАТЯЖНОЙ нестабильности "
      "— это НЕ путь к LOST (иначе многосекундный пересвет солнцем сам "
      "уронил бы лок целиком, чего отчёт прямо просит не делать) ===")
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
_lost_frames_before = t.lost_frames
unstable_cam("top_saturated")
for _ in range(30):
    tick()
    assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE
    with t.state_lock:
        assert not t.target_controllable
assert t.lost_frames == _lost_frames_before, (
    "lost_frames вырос во время визуальной нестабильности (%r -> %r) — "
    "риск случайного скатывания в LOST/TOGGLE на затяжном пересвете"
    % (_lost_frames_before, t.lost_frames))
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "даже после 30 кадров нестабильности одного стабильного кадра "
    "достаточно, чтобы вернуться в TRACKED")
print("    30 кадров подряд top_saturated -> lost_frames не вырос, "
      "восстановление одним стабильным кадром")

print("\n=== 7. Событие VISUAL_UNSTABLE логируется ТОЛЬКО на переднем "
      "фронте, не на каждом кадре затяжного эпизода ===")
stable_cam()
tick()
_events7 = []
t.flight_log.event = _events7.append
unstable_cam("jump")
for _ in range(10):
    tick()
    assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE
_vu7 = [e for e in _events7 if e.startswith("VISUAL_UNSTABLE")]
assert len(_vu7) == 1, (
    "ожидали ровно 1 событие VISUAL_UNSTABLE за 10 кадров одного "
    "непрерывного эпизода, получили %d: %s" % (len(_vu7), _vu7))
stable_cam()
tick()
print("    10 кадров одного эпизода -> 1 событие в логе (не 10)")

print("\n=== 8. Нет размазывания: visual_unstable/visual_unstable_reason "
      "в _match_dbg честны и на ДРУГИХ путях (ACQ, активный nudge), не "
      "только внутри обычного TRACKED (та же болезнь, что чинилась для "
      "cam_jump_* в ревью по 66b7f4b) ===")
stable_cam()
tick()
assert t._match_dbg.get("visual_unstable") == 0, "тест сам по себе негоден"
unstable_cam("jump")
tick()   # переходим в VISUAL_UNSTABLE
assert t._match_dbg.get("visual_unstable") == 1, "тест сам по себе негоден"
# Явно портим сэмплер, чтобы застрять в нестабильности, затем гоним AUX4
# toggle -> ACQ -> новый захват — если бы visual_unstable писался только
# внутри "нормального TRACKED", он унаследовал бы устаревшее "1" на этих
# кадрах ACQ вместо честного значения по СВЕЖЕМУ, уже стабильному замеру.
stable_cam()
t.reset_tracking(to_acq=True)
t.acq_wait_left = 0
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)   # ACQ-кадр (ищем и захватываем заново)
assert t._match_dbg.get("visual_unstable") == 0, (
    "visual_unstable остался 1 на ACQ-кадре после того, как камера снова "
    "стабильна — размазанная диагностика (значение с прошлого TRACKED-"
    "кадра, не честное текущее)")
assert t._match_dbg.get("visual_unstable_reason") == "", (
    "visual_unstable_reason не сброшен вместе с visual_unstable")
print("    visual_unstable=0/reason='' корректно и на ACQ-кадре после "
      "восстановления камеры — не унаследовано с прошлого TRACKED")

print("\n=== 9. Kill-switch VISUAL_UNSTABLE_ENABLED=False: РЕАКЦИЯ "
      "выключена, но диагностика в CSV остаётся честной (тот же принцип, "
      "что у identity_ambiguous/identity_flow_gap — иначе офлайн-"
      "калибровка порога на выключенной живой реакции невозможна, п.9 "
      "отчёта) ===")
capture()
stable_cam()
tick()
t.VISUAL_UNSTABLE_ENABLED = False
unstable_cam("jump")
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "VISUAL_UNSTABLE сработал несмотря на ENABLED=False")
with t.state_lock:
    assert t.target_controllable, "controllable снят несмотря на ENABLED=False"
assert t._match_dbg.get("visual_unstable") == 1, (
    "диагностика visual_unstable погашена вместе с реакцией — при "
    "ENABLED=False офлайн-анализ не увидит, что camera jump вообще был")
assert t._match_dbg.get("visual_unstable_reason") == "jump"
t.VISUAL_UNSTABLE_ENABLED = True
stable_cam()
tick()
print("    camera jump при ENABLED=False -> track_state остался "
      "TRACKED, controllable не тронут, НО visual_unstable=1/reason="
      "'jump' в CSV — диагностика честна независимо от kill-switch")

print("\n=== 10. По исходному тексту: gate стоит ПОСЛЕ ветки ручной "
      "коррекции (nudge не отрезан фактом нестабильности камеры на "
      "кадре перехода) и ДО flow_predict (не тратим CV на кадр, чьим "
      "пикселям не доверяем) ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def process_locked_tracker(")
i_nudge_first = src.index("if _nudge_active:", i_fn)
i_gate = src.index(
    "if _visually_unstable:", i_fn)
i_flow = src.index(
    "flow_ok, pred_cx, pred_cy = flow_predict(", i_fn)
assert i_nudge_first < i_gate < i_flow, (
    "порядок веток нарушен: ожидали nudge -> VISUAL_UNSTABLE gate -> "
    "flow_predict")
print("    порядок в исходнике: nudge -> VISUAL_UNSTABLE gate -> "
      "flow_predict")

print("\n=== 11. CSV: обе колонки на месте и подключены в "
      "_capture_flight_row ===")
assert "visual_unstable,visual_unstable_reason," in src
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
assert '_match_dbg.get("visual_unstable")' in row_body
assert '_match_dbg.get("visual_unstable_reason")' in row_body
print("    обе колонки объявлены и подключены")

print("\n=== 12. Confirmed identity anchor НЕ трогается visual instability "
      "(отчёт 25.09, разбор поверх 64949ec, п.I/K-8: 'плохой кадр не "
      "должен становиться новым anchor для prev_gray/template') ===")
capture()
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
assert t._identity_anchor_gray is not None, (
    "тест сам по себе негоден: anchor обязан быть установлен после захвата")
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_events12 = []
t.flight_log.event = _events12.append
unstable_cam("jump")
for _ in range(10):
    tick()
    assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE
    assert t._identity_anchor_gray is _anchor_ref, (
        "_identity_anchor_gray стал другим объектом ВО ВРЕМЯ visual "
        "instability")
    assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
        "байты _identity_anchor_gray изменились во время visual "
        "instability")
    assert t._match_dbg.get("identity_anchor_changed") == 0, (
        "identity_anchor_changed=1 на кадре visual instability")
_anchor_events12 = [e for e in _events12 if e.startswith("IDENTITY ANCHOR")]
assert not _anchor_events12, (
    "событие 'IDENTITY ANCHOR: подтверждена' произошло во время visual "
    "instability: %s" % _anchor_events12)
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED
assert t._identity_anchor_gray is _anchor_ref, (
    "anchor подменился уже ПОСЛЕ восстановления камеры")
print("    10 кадров camera jump + восстановление — anchor остался тем же "
      "объектом с теми же байтами на протяжении всего эпизода, "
      "identity_anchor_changed ни разу не всплыл 1")

print("\nOK: VISUAL_UNSTABLE переиспользует существующий freshness-гейт "
      "(CAM_JUMP_MAX_VALID_DT_S, ни одного нового порога), отключает "
      "controllable ТОЛЬКО пока замер свеж и показывает нестабильность, "
      "не трогает geometry/prev_gray/lost_frames, само разрешается без "
      "explicit reset на первом же стабильном замере, событие не "
      "спамится на затяжном эпизоде, диагностика в CSV честна на ЛЮБОМ "
      "пути (не только внутри обычного TRACKED), kill-switch сохраняет "
      "старое поведение.")
