"""Неоднозначный candidate — отказ, а не подтверждённый захват (отчёт
25.09, п.3, пересмотрено после разбора оператора поверх 64949ec, п.A).

КОНТЕКСТ. До 64949ec estimate_initial_target() ВСЕГДА возвращал ok=True —
даже когда _nayti_pyatno() не нашёл в зоне поиска вообще ничего. 64949ec
закрыл это для отдельной "прицел"-ветки конкретным множителем
(ACQ_YADRO_CONFIDENT_MULT=2.0). Разбор поверх 64949ec убрал саму ветку
"прицел имеет приоритет" целиком (см. test_zona_poiska.py) — _nayti_pyatno
теперь ВСЕГДА ищет ближайшую вершину по всей зоне, крестик — не более чем
её центр. Соответственно и отказ переработан:

  1. Ничего не нашли в зоне (_nayti_pyatno -> None) — ОТКАЗ БЕЗУСЛОВНО, не
     опция ACQ_AMBIGUOUS_REJECT_ENABLED. "Ничего не нашли -> всё равно
     захватить сырой крестик" убрано из архитектуры целиком.
  2. Candidate НАЙДЕН, но margin ниже ACQ_CANDIDATE_CONFIDENT_MULT — отказ,
     управляется ACQ_AMBIGUOUS_REJECT_ENABLED. Дефолт множителя (1.0) —
     намеренно нейтральный (см. его докстроку в tracker.py) — тест ниже
     временно поднимает его, чтобы вообще было что проверять.

Ни в одном из двух случаев нет пути "возьмём что-то другое вместо" — при
отказе acquisition остаётся в ACQ и пробует заново на следующем кадре.
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

_real_nayti_pyatno = t._nayti_pyatno


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


def fake_pyatno(margin, dx=3.0, dy=-2.0):
    """Подменяет _nayti_pyatno фиксированным (x,y,margin,dx,dy) — точная
    CV-математика самого пятна не имеет значения для этого файла, важна
    только реакция estimate_initial_target на margin."""
    def _f(gray):
        return (float(t.CENTER_X_LORES) + dx, float(t.CENTER_Y_LORES) + dy,
               margin, dx, dy)
    return _f


assert t.ACQ_AMBIGUOUS_REJECT_ENABLED, "тест сам по себе негоден без ENABLED"
# Дефолт ACQ_CANDIDATE_CONFIDENT_MULT=1.0 — намеренно нейтральный (см. его
# докстроку в tracker.py: любой найденный vershiny-кандидат уже имеет
# margin>=1.0 по построению, значит 1.0 ничего доп. не фильтрует). Чтобы
# вообще было что проверять в секциях 1-3, временно поднимаем порог —
# восстанавливаем в конце файла.
_orig_mult = t.ACQ_CANDIDATE_CONFIDENT_MULT
t.ACQ_CANDIDATE_CONFIDENT_MULT = 2.0
MULT = t.ACQ_CANDIDATE_CONFIDENT_MULT

print("=== 1. estimate_initial_target: margin ниже порога -> ok=False "
      "(прямая проверка decision-логики) ===")
t._nayti_pyatno = fake_pyatno(MULT - 0.5)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert not ok, "margin=%.2f < %.2f должен был отклонить захват" % (MULT - 0.5, MULT)
assert t._match_dbg.get("acq_candidate_present") == 1
assert t._match_dbg.get("acq_candidate_confirmed") == 0
print("    margin=%.2f (< %.2f) -> ok=False, acq_candidate_confirmed=0"
      % (MULT - 0.5, MULT))

print("\n=== 2. margin выше порога -> ok=True ===")
t._nayti_pyatno = fake_pyatno(MULT + 0.5)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "margin=%.2f >= %.2f должен был подтвердить захват" % (MULT + 0.5, MULT)
assert t._match_dbg.get("acq_candidate_confirmed") == 1
print("    margin=%.2f (>= %.2f) -> ok=True, acq_candidate_confirmed=1"
      % (MULT + 0.5, MULT))

print("\n=== 3. Граница: margin РОВНО на пороге -> принимается (>=, не >) "
      "===")
t._nayti_pyatno = fake_pyatno(MULT)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "margin РОВНО на пороге обязан приниматься (граница включительно)"
print("    margin=%.2f (== порог) -> ok=True" % MULT)

print("\n=== 4. Ничего не найдено в зоне вовсе (_nayti_pyatno -> None) -> "
      "ok=False БЕЗУСЛОВНО, не опция ACQ_AMBIGUOUS_REJECT_ENABLED (отчёт: "
      "'ничего не нашли -> всё равно захватить сырой крестик' убрано из "
      "архитектуры целиком, это НЕ переключатель) ===")
t._nayti_pyatno = lambda gray: None
t.ACQ_AMBIGUOUS_REJECT_ENABLED = False   # даже так — обязано отклонить
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert not ok, (
    "отсутствие ЛЮБОГО candidate в зоне должно отклонять захват "
    "БЕЗУСЛОВНО — даже при ACQ_AMBIGUOUS_REJECT_ENABLED=False")
assert t._match_dbg.get("acq_candidate_present") == 0
t.ACQ_AMBIGUOUS_REJECT_ENABLED = True
print("    _nayti_pyatno=None -> ok=False ДАЖЕ при ENABLED=False "
      "(безусловный путь, не переключаемый)")

print("\n=== 5. Kill-switch ACQ_AMBIGUOUS_REJECT_ENABLED=False: margin-"
      "отказ (найденного, но сомнительного candidate) отключается — это "
      "ЕДИНСТВЕННОЕ, чем управляет переключатель ===")
t.ACQ_AMBIGUOUS_REJECT_ENABLED = False
t._nayti_pyatno = fake_pyatno(0.01)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "ENABLED=False должен был отключить margin-отказ"
t.ACQ_AMBIGUOUS_REJECT_ENABLED = True
print("    margin=0.01 при ENABLED=False -> ok=True (margin-реакция "
      "выключена)")

print("\n=== 6. Диагностика в events.log ===")
_events = []
t.flight_log.event = _events.append
t._nayti_pyatno = fake_pyatno(MULT - 0.5)
t.estimate_initial_target(scene)
_rej = [e for e in _events if e.startswith("ЗАХВАТ ОТКЛОНЁН")]
assert len(_rej) == 1, "ожидали ровно 1 событие отказа, получили %d: %s" % (len(_rej), _rej)
assert ("margin=%.2f" % (MULT - 0.5)) in _rej[0], _rej[0]
print("    событие отказа: %s" % _rej[0])
_events.clear()
t._nayti_pyatno = fake_pyatno(MULT + 0.5)
t.estimate_initial_target(scene)
_ok_ev = [e for e in _events if e.startswith("ЗАХВАТ:")]
assert len(_ok_ev) == 1
assert ("margin=%.2f" % (MULT + 0.5)) in _ok_ev[0], _ok_ev[0]
print("    событие принятия: %s" % _ok_ev[0])

t._nayti_pyatno = _real_nayti_pyatno

print("\n=== 7. ИНТЕГРАЦИЯ, ПОСЛЕДОВАТЕЛЬНОСТЬ КАДРОВ (не одна функция): "
      "несколько отклонённых заходов подряд держат track_state=ACQ, "
      "controllable=False, БЕЗ единого перехода в TRACKED — затем "
      "уверенный кадр захватывает нормально ===")
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
_events2 = []
t.flight_log.event = _events2.append
t._nayti_pyatno = fake_pyatno(MULT - 0.3)
_states_seen = []
_controllable_seen = []
for _ in range(8):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _states_seen.append(t.track_state)
    with t.state_lock:
        _controllable_seen.append(t.target_controllable)
assert all(s == t.TRACK_STATE_ACQ for s in _states_seen), (
    "track_state покинул ACQ на отклонённых кадрах: %s" % _states_seen)
assert not any(_controllable_seen), (
    "controllable стал True хотя бы раз при отклонённых заходах")
_rej2 = [e for e in _events2 if e.startswith("ЗАХВАТ ОТКЛОНЁН")]
assert len(_rej2) == 8, (
    "ожидали 8 событий отказа (по одному на кадр), получили %d"
    % len(_rej2))
print("    8 отклонённых заходов подряд: track_state=ACQ на каждом, "
      "controllable ни разу не стал True")

t._nayti_pyatno = fake_pyatno(MULT + 1.0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "уверенный кадр после серии отказов не привёл к захвату, track_state=%r"
    % t.track_state)
with t.state_lock:
    assert t.target_controllable, (
        "controllable не стал True сразу после успешного захвата")
assert t._identity_anchor_gray is not None, (
    "confirmed identity anchor не установлен после успешного захвата")
print("    следующий же уверенный кадр -> TRACKED, controllable=True, "
      "identity anchor установлен — серия отказов не 'застревает'")

t._nayti_pyatno = _real_nayti_pyatno
t.ACQ_CANDIDATE_CONFIDENT_MULT = _orig_mult

print("\n=== 8. По исходному тексту: отказ по неоднозначности не берёт "
      "другого candidate вместо этого — единственный путь после отказа "
      "return, tx/ty/lw/lh результата не используются ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def estimate_initial_target(gray):")
i_fn_end = src.index("\ndef ", i_fn + 1)
body = src[i_fn:i_fn_end]
assert body.count("return (float(CENTER_X_LORES), float(CENTER_Y_LORES),\n"
                  "                   float(ACQ_DEFAULT_LOCK_W), float(ACQ_DEFAULT_LOCK_H), False)") == 2, (
    "ожидали ровно 2 пути отказа (нет candidate / margin слишком низкий), "
    "текст функции разошёлся с тем, что проверяет этот файл")
print("    ровно 2 пути отказа найдены в исходном тексте, оба возвращают "
      "ok=False без альтернативного candidate")

print("\n=== 9. CSV: колонки acq_candidate_* объявлены и подключены ===")
assert "acq_candidate_present,acq_candidate_dx,acq_candidate_dy,acq_candidate_confirmed," in src
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
for key in ("acq_candidate_present", "acq_candidate_dx", "acq_candidate_dy",
            "acq_candidate_confirmed"):
    assert ('_match_dbg.get("%s")' % key) in row_body, (
        "_match_dbg.get(%r) не найден в _capture_flight_row" % key)
print("    все четыре колонки объявлены и подключены")

print("\nOK: неоднозначный candidate (margin < ACQ_CANDIDATE_CONFIDENT_"
      "MULT) отклоняет захват под управлением ACQ_AMBIGUOUS_REJECT_"
      "ENABLED; полное отсутствие candidate отклоняет БЕЗУСЛОВНО, не "
      "опция; диагностика (acq_candidate_*, events.log) честна; серия "
      "отказов держит track_state=ACQ без единого ложного TRACKED и "
      "устанавливает identity anchor на первом же успешном заходе.")
