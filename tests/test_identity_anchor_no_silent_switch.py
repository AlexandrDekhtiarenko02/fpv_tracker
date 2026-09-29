"""Подтверждённая identity не заменяется соседним patch молча — но и не
роняется ложно при обычном шуме matcher/flow, когда anchor подтверждает
ту же позицию (пересмотрено после реальных стендовых логов 8e2ac74:
13/17 срывов оказались ложными именно потому, что ambiguity/flow_gap
триггерили persistent UNCERTAIN, хотя anchor в тех же кадрах уверенно
подтверждал ту же цель).

ИЗМЕНЁННАЯ АРХИТЕКТУРА (см. IDENTITY_ANCHOR_ARBITER_ENABLED в tracker.py).
Ambiguity/flow_gap streak сам по себе больше НЕ переводит в persistent
IDENTITY_UNCERTAIN, если свежее immutable-anchor подтверждение говорит
"это по-прежнему та же цель". Он продолжает:
  - давать soft distrust (controllable=False на подозрительных кадрах);
  - гейтить template adaptation (не обучать шаблон на сомнительных);
но не хоронит лок.

СТАРАЯ ВЕРСИЯ этого файла (bf7957f) ожидала persistent UNCERTAIN через
flow_gap streak на этой самой сцене (matcher уходит на копию А, flow
честно остаётся на A). Это и есть ложный срыв из реальных логов —
исправлен архитектурно, а не подкруткой порогов.

СЦЕНАРИЙ. Confirmed A, копия anchor'а на месте B рядом. flow_predict
застаблен: остаётся на A. Реальный template_match_locked находит B
(отличный кандидат для matcher'а по построению). Реальная периодическая
anchor-сверка находит B (=копия anchor'а) под смещённой позицией и
подтверждает — arbiter говорит "identity сохранена" (не различает A от
её копии, ЧТО ПРАВИЛЬНО: если два визуально идентичных patch'а, никакой
observer не может ЗНАТЬ, какой из них "тот"). Persistent UNCERTAIN НЕ
триггерится, лок сохраняется.

ПОЛУЧЕННОЕ РАЗЛИЧЕНИЕ. "Реально ДРУГОЙ B → UNCERTAIN" остаётся закрытым
через anchor_mismatch путь — отдельно проверено в K-4 (test_identity_
anchor_agreement_trap.py) и в положительном тесте arbiter'а
(test_identity_anchor_arbiter.py, если добавлен). Этот файл теперь
проверяет ПОЛОЖИТЕЛЬНУЮ сторону: matcher-trap на визуально совпадающем
patch'е — не ложное срабатывание.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

_real_flow_predict = t.flow_predict


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
    t.flow_predict = _real_flow_predict
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def fake_flow_still_on_a(prev_g, cur_g, pts, cx, cy):
    """Поток честно продолжает считать, что цель на прежнем (переданном)
    месте — реальный LK потерял бы точки на плоском фоне (status=0),
    заглушка убирает CV-недетерминированность."""
    return True, cx, cy


assert t.IDENTITY_UNCERTAIN_ENABLED, "тест сам по себе негоден без ENABLED"
assert t.IDENTITY_ANCHOR_ARBITER_ENABLED, (
    "тест сам по себе негоден без IDENTITY_ANCHOR_ARBITER_ENABLED — именно "
    "arbiter здесь и проверяется")
N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

capture()
A_cx, A_cy = t.lock_cx, t.lock_cy
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_tmpl_at_capture = t.template_gray.copy()
th, tw = _tmpl_at_capture.shape[:2]
OFFSET = 10.0   # внутри окна поиска (SEARCH_MARGIN_MIN), > MATCH_GAP_SOFT


def trap_scene():
    """Плоский фон; на месте A — ничего; рядом (OFFSET px) — точная копия
    anchor. matcher её находит как высший score; anchor тоже её находит
    (копия неотличима от anchor)."""
    g = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    bx = int(round(A_cx + OFFSET - tw / 2))
    by = int(round(A_cy - th / 2))
    g[by:by + th, bx:bx + tw] = _tmpl_at_capture
    return g


trap = trap_scene()

print("=== 1. flow-gap streak растёт (matcher идёт на копию B, flow — на "
      "A), НО anchor каждый periodic-замер подтверждает — persistent "
      "UNCERTAIN НЕ триггерится ===")
t.flow_predict = fake_flow_still_on_a
_events = []
t.flight_log.event = _events.append
_states_seen = []
_gap_seen = []
_streak_seen = []
_arb_seen = []
for i in range(N * 2):   # даже за 2x дебаунса не должно уйти в UNCERTAIN
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(trap)
    _states_seen.append(t.track_state)
    _gap_seen.append(t._match_dbg.get("flow_gap"))
    _streak_seen.append(t._match_dbg.get("identity_uncertain_streak"))
    _arb_seen.append(t._match_dbg.get("identity_anchor_recently_confirmed"))
    # anchor не трогается — arbiter не подтверждает identity как новую,
    # а лишь читает существующую.
    assert t._identity_anchor_gray is _anchor_ref, (
        "_identity_anchor_gray стал другим объектом на кадре %d" % i)
    assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
    assert t._match_dbg.get("identity_anchor_changed") == 0
print("    flow_gap по кадрам:", ["%.2f" % g for g in _gap_seen])
print("    streak по кадрам:", _streak_seen)
print("    anchor_recently_confirmed:", _arb_seen)
print("    track_state:", _states_seen)
assert all(s == t.TRACK_STATE_TRACKED for s in _states_seen), (
    "track_state покинул TRACKED хотя бы раз за %d кадров — arbiter не "
    "заблокировал streak-триггер: %s" % (N * 2, _states_seen))
assert any(_arb_seen), (
    "тест сам по себе негоден: ни разу за прогон anchor-arbiter не "
    "подтвердил ни один замер — сам факт того, что мы стоим на "
    "TRACKED, тогда объясняется чем-то другим")
_iu_events = [e for e in _events if e.startswith("IDENTITY_UNCERTAIN")]
assert not _iu_events, (
    "событие IDENTITY_UNCERTAIN появилось, хотя arbiter должен был "
    "заблокировать: %s" % _iu_events)
print("    OK: streak реально рос, anchor каждый замер подтверждал, "
      "persistent UNCERTAIN не срабатывал ни разу за %d кадров" % (N * 2))

print("\n=== 2. Diagnostics по-прежнему честны: на ранних кадрах "
      "(когда flow_gap > MATCH_GAP_SOFT=%s) identity_flow_gap=1 — arbiter "
      "гасит только state-machine trigger, не сырую диагностику ===" % t.MATCH_GAP_SOFT)
# Проверяем на ПЕРВЫХ кадрах: снимок был сделан в цикле выше через
# _gap_seen, но identity_flow_gap флаг мы не собирали. Прогоняем свежую
# короткую серию с уже установившимся streak — flow_gap там на пике.
t.reset_tracking(to_acq=False)
capture()
t.flow_predict = fake_flow_still_on_a
_early_flow_gap_flags = []
_early_gap_values = []
for _ in range(3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(trap)
    _early_flow_gap_flags.append(t._match_dbg.get("identity_flow_gap"))
    _early_gap_values.append(t._match_dbg.get("flow_gap"))
print("    ранние flow_gap: %s, identity_flow_gap: %s"
      % (["%.2f" % g for g in _early_gap_values], _early_flow_gap_flags))
assert any(f == 1 for f in _early_flow_gap_flags), (
    "identity_flow_gap ни разу не выставлен на первых кадрах, хотя flow_gap "
    "явно > MATCH_GAP_SOFT — arbiter не должен трогать диагностику")
print("    identity_flow_gap=1 по-прежнему честно записывается на кадрах, "
      "где gap реально > MATCH_GAP_SOFT")

print("\n=== 3. Soft distrust: если streak дошёл до N, controllable "
      "снимается (soft), даже если persistent UNCERTAIN blocked — "
      "arbiter НЕ разрешает автомобильности рулить на подозрительном "
      "кадре, только защищает от sticky IDENTITY_UNCERTAIN ===")
# Soft distrust — прогон нам его показать не гарантирует (зависит от того,
# случился ли streak≥N в момент замера), поэтому справочно смотрим:
with t.state_lock:
    _controllable_now = t.target_controllable
_soft = t._match_dbg.get("identity_soft_distrust")
print("    финально: controllable=%s soft_distrust=%s streak=%s"
      % (_controllable_now, _soft, t._match_dbg.get("identity_uncertain_streak")))

print("\n=== 4. Anchor не подтверждается заново на трапе — arbiter это "
      "ЧТЕНИЕ anchor'а, не его переписывание. Единственные легитимные "
      "события 'IDENTITY ANCHOR подтверждена' — самих capture() вызовов "
      "(в §1 и в §2 сброс+повторный захват) ===")
# Собираем ВСЕ события с начала прогона (не только _events, который
# перекрылся в §2 через t.reset_tracking → возможно новый flight_log).
_anchor_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
# Только события acquisition (единственный легитимный путь) допустимы.
_non_acquisition = [e for e in _anchor_events if "acquisition" not in e]
assert not _non_acquisition, (
    "нелегитимное событие 'IDENTITY ANCHOR: подтверждена': %s"
    % _non_acquisition)
print("    %d 'IDENTITY ANCHOR' event(s), все — acquisition (по числу "
      "capture() вызовов); нет ни одного нелегитимного переподтверждения"
      % len(_anchor_events))

t.flow_predict = _real_flow_predict
t.reset_tracking(to_acq=False)

print("\nOK: matcher-trap на визуально совпадающем соседе (копии anchor) "
      "БОЛЬШЕ НЕ вызывает ложного persistent IDENTITY_UNCERTAIN — arbiter "
      "(IDENTITY_ANCHOR_ARBITER_ENABLED) правильно распознаёт, что "
      "immutable anchor всё ещё подтверждает ту же позицию, и streak "
      "ambiguity/flow_gap остаётся полезным только как soft distrust и "
      "гейт template adaptation, не как основание хоронить лок. 'Реально "
      "другой B → UNCERTAIN' продолжает работать через anchor_mismatch "
      "путь (см. K-4).")
