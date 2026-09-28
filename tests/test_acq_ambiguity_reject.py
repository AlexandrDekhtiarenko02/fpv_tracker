"""Неоднозначный patch под прицелом — отказ, а не подтверждённый захват
(отчёт 25.09, п.3).

КОНТЕКСТ. estimate_initial_target() ДО этой правки ВСЕГДА возвращал
ok=True — даже когда _nayti_pyatno() не нашёл в зоне поиска вообще
ничего выраженного (тогда брался сырой прицел с размером по умолчанию).
Ровно тезис отчёта "tracker too easily calls itself TRACKED", только на
шаг раньше — в момент самого захвата, а не во время слежения.

ЧТО ПРОВЕРЯЕТ ЭТОТ ФАЙЛ. Новый гейт в estimate_initial_target()
переиспользует margin, который теперь возвращает _nayti_pyatno() (см.
test_zona_poiska.py §6-7 для самой margin-механики): если patch под
прицелом ("прицел") превысил порог ACQ_SNAP_YADRO_MIN_ABS, но недостаточно
уверенно (margin < ACQ_YADRO_CONFIDENT_MULT — калибровка по реальным
цифрам бенча в комментарии у константы, не выдумка), acquisition
ОТКАЗЫВАЕТ (ok=False) вместо того чтобы подтвердить сомнительный лок.
Отказ НЕ означает "возьми соседний" — соседей здесь вообще нет в
рассмотрении (в отличие от случая "пятно", который эта правка не
трогает). Отказ приводит РОВНО туда же, куда уже приводило "измерение не
удалось" до этой правки — track_state остаётся ACQ, process_locked_
tracker пробует заново на следующем кадре.

_nayti_pyatno подменяется контролируемой заглушкой — точные числовые
margin для конкретных синтетических сцен уже проверены в
test_zona_poiska.py; здесь важна ТОЛЬКО логика решения estimate_initial_
target/process_locked_tracker по данному margin, не сама CV-математика.
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


def fake_pyatno(reason, margin):
    """Подменяет _nayti_pyatno фиксированным (x,y,reason,margin) на
    крестике — точная CV-математика самого пятна не имеет значения для
    этого файла, важна только реакция estimate_initial_target на
    reason/margin."""
    def _f(gray):
        return (float(t.CENTER_X_LORES), float(t.CENTER_Y_LORES),
               reason, margin)
    return _f


assert t.ACQ_AMBIGUOUS_REJECT_ENABLED, "тест сам по себе негоден без ENABLED"
MULT = t.ACQ_YADRO_CONFIDENT_MULT

print("=== 1. estimate_initial_target: margin ниже порога -> ok=False "
      "(прямая проверка decision-логики) ===")
t._nayti_pyatno = fake_pyatno("прицел", MULT - 0.5)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert not ok, "margin=%.2f < %.2f должен был отклонить захват" % (MULT - 0.5, MULT)
print("    margin=%.2f (< %.2f) -> ok=False" % (MULT - 0.5, MULT))

print("\n=== 2. margin выше порога -> ok=True, как раньше ===")
t._nayti_pyatno = fake_pyatno("прицел", MULT + 0.5)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "margin=%.2f >= %.2f должен был подтвердить захват" % (MULT + 0.5, MULT)
assert tx == t.CENTER_X_LORES and ty == t.CENTER_Y_LORES
print("    margin=%.2f (>= %.2f) -> ok=True" % (MULT + 0.5, MULT))

print("\n=== 3. Граница: margin РОВНО на пороге -> принимается (>=, не >) "
      "===")
t._nayti_pyatno = fake_pyatno("прицел", MULT)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "margin РОВНО на пороге обязан приниматься (граница включительно)"
print("    margin=%.2f (== порог) -> ok=True" % MULT)

print("\n=== 4. Ничего не найдено в зоне вовсе (_nayti_pyatno -> None) -> "
      "ok=False (раньше был ok=True с размером по умолчанию — то самое "
      "'tracker too easily calls itself TRACKED', на шаг раньше) ===")
t._nayti_pyatno = lambda gray: None
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert not ok, (
    "отсутствие ЛЮБОГО выраженного patch в зоне должно отклонять захват, "
    "а не подставлять размер по умолчанию с ok=True")
print("    _nayti_pyatno=None -> ok=False")

print("\n=== 5. Случай 'пятно' НЕ отклоняется независимо от margin — вне "
      "области этой правки (нет калиброванных чисел для этого случая, "
      "см. докстроку _nayti_pyatno) ===")
t._nayti_pyatno = fake_pyatno("пятно", 0.01)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, (
    "случай 'пятно' отклонён несмотря на то, что эта правка его "
    "сознательно не трогает (margin=0.01)")
print("    margin='пятно'=0.01 (сколь угодно низкий) -> ok=True, как и "
      "до этой правки")

print("\n=== 6. Kill-switch ACQ_AMBIGUOUS_REJECT_ENABLED=False: старое "
      "поведение полностью восстановлено ===")
t.ACQ_AMBIGUOUS_REJECT_ENABLED = False
t._nayti_pyatno = fake_pyatno("прицел", 0.01)
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, "ENABLED=False должен был отключить отказ целиком"
t.ACQ_AMBIGUOUS_REJECT_ENABLED = True
print("    margin=0.01 при ENABLED=False -> ok=True (реакция выключена)")

print("\n=== 6б. Kill-switch обязан ТАК ЖЕ восстанавливать старое "
      "поведение для случая 'ничего не найдено вовсе' — САМОСТОЯТЕЛЬНЫЙ "
      "путь кода (не через margin), пойманный отдельным багом при первом "
      "прогоне: kill-switch проверялся ТОЛЬКО у margin-ветки, эта "
      "оставалась безусловным отказом (поймано test_adaptation_gate.py, "
      "который случайно опирается именно на этот путь) ===")
t.ACQ_AMBIGUOUS_REJECT_ENABLED = False
t._nayti_pyatno = lambda gray: None
tx, ty, lw, lh, ok = t.estimate_initial_target(scene)
assert ok, (
    "ENABLED=False не восстановил старое поведение для 'ничего не "
    "найдено' — этот путь отказывал безусловно, независимо от kill-switch")
assert lw == t.ACQ_DEFAULT_LOCK_W and lh == t.ACQ_DEFAULT_LOCK_H, (
    "при ENABLED=False и отсутствии pyatno ожидали размер по умолчанию, "
    "получили %r x %r" % (lw, lh))
t.ACQ_AMBIGUOUS_REJECT_ENABLED = True
print("    _nayti_pyatno=None при ENABLED=False -> ok=True, размер по "
      "умолчанию (реакция выключена целиком, не частично)")

print("\n=== 7. Диагностика (отчёт 25.09, п.3: 'method, uniqueness/"
      "ambiguity, rejection reason') в events.log ===")
_events = []
t.flight_log.event = _events.append
t._nayti_pyatno = fake_pyatno("прицел", MULT - 0.5)
t.estimate_initial_target(scene)
_rej = [e for e in _events if e.startswith("ЗАХВАТ ОТКЛОНЁН")]
assert len(_rej) == 1, "ожидали ровно 1 событие отказа, получили %d: %s" % (len(_rej), _rej)
assert "method=прицел" in _rej[0] or "прицел" in _rej[0], _rej[0]
assert ("margin=%.2f" % (MULT - 0.5)) in _rej[0], _rej[0]
print("    событие отказа: %s" % _rej[0])
_events.clear()
t._nayti_pyatno = fake_pyatno("прицел", MULT + 0.5)
t.estimate_initial_target(scene)
_ok_ev = [e for e in _events if e.startswith("захват по прицелу")]
assert len(_ok_ev) == 1
assert ("margin=%.2f" % (MULT + 0.5)) in _ok_ev[0], _ok_ev[0]
print("    событие принятия: %s" % _ok_ev[0])

t._nayti_pyatno = _real_nayti_pyatno

print("\n=== 8. ИНТЕГРАЦИЯ, ПОСЛЕДОВАТЕЛЬНОСТЬ КАДРОВ (не одна функция): "
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
t._nayti_pyatno = fake_pyatno("прицел", MULT - 0.3)
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

t._nayti_pyatno = fake_pyatno("прицел", MULT + 1.0)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "уверенный кадр после серии отказов не привёл к захвату, track_state=%r"
    % t.track_state)
with t.state_lock:
    assert t.target_controllable, (
        "controllable не стал True сразу после успешного захвата")
print("    следующий же уверенный кадр -> TRACKED, controllable=True — "
      "серия отказов не 'застревает', не портит следующую попытку")

t._nayti_pyatno = _real_nayti_pyatno

print("\n=== 9. По исходному тексту: отказ по неоднозначности не берёт "
      "соседнее пятно вместо прицела — ветка 'пятно' структурно "
      "недостижима в этом случае (elif, не отдельная попытка) ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def estimate_initial_target(gray):")
i_fn_end = src.index("\ndef ", i_fn + 1)
body = src[i_fn:i_fn_end]
i_reject = body.index("ЗАХВАТ ОТКЛОНЁН: patch под прицелом неоднозначен")
i_return_false = body.index("return tx, ty, float(ACQ_DEFAULT_LOCK_W)"
                            ", float(ACQ_DEFAULT_LOCK_H), False", i_reject)
i_pyatno_branch = body.index('tx, ty = float(pyatno[0]), float(pyatno[1])')
assert i_reject < i_return_false < i_pyatno_branch, (
    "отказ по неоднозначности обязан возвращаться (False) РАНЬШЕ ветки, "
    "которая присвоила бы tx/ty от pyatno — иначе это уже не отказ, а "
    "молчаливый переход на что-то другое")
print("    отказ возвращается ДО того, как код мог бы взять tx/ty "
      "откуда-либо ещё")

print("\nOK: неоднозначный patch под прицелом (margin < "
      "ACQ_YADRO_CONFIDENT_MULT) и полное отсутствие выраженности в зоне "
      "теперь отклоняют захват (ok=False) вместо того, чтобы подтвердить "
      "его с размером по умолчанию; случай 'пятно' сознательно не "
      "тронут; kill-switch восстанавливает старое поведение; диагностика "
      "(method/margin/reason) видна в events.log; серия отказов держит "
      "track_state=ACQ без единого ложного перехода в TRACKED и не "
      "мешает следующей уверенной попытке.")
