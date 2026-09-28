"""IDENTITY_UNCERTAIN — единое состояние безопасности "не уверены, ТА ли
это цель" (отчёт 25.09, п.2).

КОНТЕКСТ. Разбор борта показал: matcher/flow уже умеют распознавать
неоднозначность (конкурирующий пик не хуже выбранного — MATCH_AMBIGUITY_
GUARD/MATCH_LEAD_FULL) и расхождение матча с потоком (MATCH_GAP_SOFT) —
но раньше эти признаки влияли ТОЛЬКО на вес блендинга позиции (w_m) и на
то, разрешена ли адаптация шаблона (_template_adaptation_gate). track_state
оставался TRACKED, target_controllable оставался True независимо от того,
насколько матч на самом деле уверен в себе. "Tracker too easily calls
itself TRACKED" — тезис отчёта.

ЧТО ПРОВЕРЯЕТ ЭТОТ ФАЙЛ. Новая логика в process_locked_tracker
переиспользует РОВНО ТЕ ЖЕ сигналы (никаких новых порогов сравнения):
серия из IDENTITY_UNCERTAIN_CONFIRM_FRAMES подряд идущих dual-signal
кадров (flow_ok и match_ok оба живы, score/dist_fm оба в допуске — та же
ветка, что и у w_m), где матч неоднозначен (ambiguous) или расходится с
потоком (flow_gap), переводит трекер в track_state=IDENTITY_UNCERTAIN:
controllable=False, БЕЗ reanchor, БЕЗ auto-reacq — персистентно, до
explicit reset_tracking() (тот же AUX4-toggle UX, что и у LOST/TOGGLE и
nudge-abort). Одиночный сомнительный кадр — не триггер (дебаунс, тот же
принцип, что уже используют w_m/adaptation_gate).

Тестовый матч/поток подменяются контролируемыми заглушками (t.flow_predict/
t.template_match_locked) — реальный matchTemplate/optical flow не даёт
детерминированно управлять ambiguity/gap на синтетической сцене, а логика,
которую здесь нужно проверить, начинается СТРОГО ПОСЛЕ того, как эти две
функции уже вернули значения.
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

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked
_real_estimate_initial_target = t.estimate_initial_target


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
    t.template_match_locked = _real_template_match_locked
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def fake_flow_predict(prev_g, cur_g, pts, cx, cy):
    # Поток "не видит" движения — предсказание остаётся на текущем lock.
    # Так flow_motion=0.0, и вся геометрия управляется ЗНАЧЕНИЯМИ,
    # которые задаёт fake match ниже, а не побочными эффектами реального CV.
    return True, cx, cy


def make_fake_match(score, second, dist_from_pred=0.0):
    """dual-signal кадр с управляемыми score/second (-> lead) и
    расстоянием матча от предсказания потока (-> dist_fm)."""
    def fake_template_match_locked(gray, pred_cx, pred_cy, flow_motion=0.0,
                                    tgt_dx=0.0, tgt_dy=0.0):
        t._match_dbg["second"] = second
        return True, pred_cx + dist_from_pred, pred_cy, score

    return fake_template_match_locked


def install_fakes(score, second, dist_from_pred=0.0):
    t.flow_predict = fake_flow_predict
    t.template_match_locked = make_fake_match(score, second, dist_from_pred)


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


AMBIGUOUS = dict(score=0.90, second=0.88, dist_from_pred=0.0)   # lead <= 0
DISAGREE = dict(score=0.90, second=0.05, dist_from_pred=10.0)   # lead ok, gap > MATCH_GAP_SOFT
CLEAN = dict(score=0.90, second=0.05, dist_from_pred=0.0)       # ни то, ни другое

assert t.IDENTITY_UNCERTAIN_ENABLED, "тест сам по себе негоден без ENABLED"
N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES
assert N >= 2, "тест предполагает дебаунс хотя бы в пару кадров"

print("=== 1. Одиночный неоднозначный кадр — НЕ триггер (дебаунс) ===")
capture()
install_fakes(**AMBIGUOUS)
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "один сомнительный кадр уже перевёл в IDENTITY_UNCERTAIN — дебаунс не "
    "работает, риск ложных срабатываний на обычном шуме")
assert t._match_dbg.get("identity_ambiguous") == 1
assert t._match_dbg.get("identity_flow_gap") == 0
assert t._match_dbg.get("identity_uncertain_streak") == 1
with t.state_lock:
    assert t.target_controllable, "controllable снят раньше времени"
print("    streak=1 после первого сомнительного кадра, track_state всё ещё "
      "TRACKED, controllable=True")

print("\n=== 2. Streak растёт с каждым подряд идущим неоднозначным кадром "
      "===")
for i in range(2, N):
    tick()
    assert t.track_state == t.TRACK_STATE_TRACKED, (
        "сработало раньше N=%d кадров, на streak=%d" % (N, i))
    assert t._match_dbg.get("identity_uncertain_streak") == i, (
        "streak=%r, ожидали %d" % (t._match_dbg.get("identity_uncertain_streak"), i)
    )
print("    streak дошёл до %d, track_state всё ещё TRACKED (порог N=%d)"
      % (N - 1, N))

print("\n=== 3. N-й подряд кадр — триггер: IDENTITY_UNCERTAIN, "
      "controllable=False, БЕЗ reanchor ===")
_events = []
t.flight_log.event = _events.append
tick()
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "track_state=%r после %d подряд сомнительных кадров, ожидали "
    "IDENTITY_UNCERTAIN" % (t.track_state, N))
with t.state_lock:
    _controllable_3 = t.target_controllable
assert not _controllable_3, "controllable остался True после срабатывания"
assert t._identity_uncertain_pending, "персистентный флаг не выставлен"
# geometry_epoch МОЖЕТ вырасти на этом кадре — это _reset_geometry_history
# ("not_controllable"), существующий, общий для ЛЮБОЙ потери controllable
# механизм (тот же, что уже отрабатывает на HOLD/LOST/ACQ), не reanchor.
# Проверяем именно ОТСУТСТВИЕ REANCHOR-события — это и есть признак того,
# что позиция НЕ была подтверждена как новая точка привязки.
_reanchor_events = [e for e in _events if e.startswith("REANCHOR")]
assert not _reanchor_events, (
    "REANCHOR всё-таки случился при срабатывании IDENTITY_UNCERTAIN: %s"
    % _reanchor_events)
_trig_events = [e for e in _events if e.startswith("IDENTITY_UNCERTAIN")]
assert len(_trig_events) == 1, (
    "ожидали ровно 1 событие IDENTITY_UNCERTAIN, получили %d: %s"
    % (len(_trig_events), _trig_events))
print("    событие: %s; REANCHOR не вызывался" % _trig_events[0])

print("\n=== 4. Ни один флаг не читается по одной проверке: не восстанавливаем "
      "руками, гоним ЕСТЕСТВЕННЫЕ следующие кадры (даже с ЧИСТЫМ матчем — "
      "проверяем, что controllable НЕ включился обратно САМ) ===")
install_fakes(**CLEAN)
_controllable_seen = []
_states_seen = []
for _ in range(15):
    tick()
    with t.state_lock:
        _controllable_seen.append(t.target_controllable)
    _states_seen.append(t.track_state)
    assert t._identity_uncertain_pending, (
        "_identity_uncertain_pending снялся сам, без explicit pilot-действия")
assert not any(_controllable_seen), (
    "target_controllable стал True хотя бы на одном из %d естественных "
    "кадров ПОСЛЕ срабатывания, даже с чистым матчем: %s"
    % (len(_controllable_seen), _controllable_seen))
assert all(s == t.TRACK_STATE_IDENTITY_UNCERTAIN for s in _states_seen), (
    "track_state ушёл из IDENTITY_UNCERTAIN сам: %s" % _states_seen)
print("    %d естественных кадров подряд (чистый матч) — track_state и "
      "controllable не сдвинулись сами" % len(_controllable_seen))

print("\n=== 5. НЕТ auto-reacquire: estimate_initial_target ни разу не "
      "вызывается, пока мы в IDENTITY_UNCERTAIN (прямое требование отчёта) "
      "===")
_acq_calls = []
t.estimate_initial_target = lambda gray: (
    _acq_calls.append(1) or _real_estimate_initial_target(gray))
for _ in range(10):
    tick()
assert not _acq_calls, (
    "estimate_initial_target вызывался %d раз(а) из IDENTITY_UNCERTAIN — "
    "запрещённое само-переприобретение" % len(_acq_calls))
t.estimate_initial_target = _real_estimate_initial_target
print("    10 кадров, estimate_initial_target не вызван ни разу")

print("\n=== 6. Явное действие пилота (reset_tracking) снимает флаг и "
      "streak, возвращает нормальную жизнь ===")
t.reset_tracking(to_acq=False)
assert not t._identity_uncertain_pending, (
    "reset_tracking() не снял _identity_uncertain_pending")
assert t._identity_uncertain_streak == 0, (
    "reset_tracking() не обнулил _identity_uncertain_streak")
capture()
with t.state_lock:
    _controllable_after_reset = t.target_controllable
assert _controllable_after_reset, (
    "после explicit pilot-действия (reset + новый захват) controllable "
    "обязан снова заработать нормально")
print("    reset_tracking() снял флаг и streak; новый захват "
      "controllable=True — нормальная жизнь восстановлена явным действием")

print("\n=== 7. Одиночный ЧИСТЫЙ dual-signal кадр сбрасывает streak "
      "(не тянется бесконечно от старого шума) ===")
install_fakes(**AMBIGUOUS)
for _ in range(N - 1):
    tick()
assert t._match_dbg.get("identity_uncertain_streak") == N - 1, (
    "тест сам по себе негоден: streak не дошёл до N-1")
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
install_fakes(**CLEAN)
tick()
assert t._match_dbg.get("identity_uncertain_streak") == 0, (
    "чистый кадр не сбросил streak")
assert t.track_state == t.TRACK_STATE_TRACKED
install_fakes(**AMBIGUOUS)
tick()
assert t._match_dbg.get("identity_uncertain_streak") == 1, (
    "после сброса streak не начал считать заново с 1, а с %r"
    % t._match_dbg.get("identity_uncertain_streak"))
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "один кадр после сброса уже сработал — сброс streak не подействовал")
print("    N-1 сомнительных -> 1 чистый (streak=0) -> 1 сомнительный "
      "(streak=1, не сработало) — сброс реально работает, не только "
      "косметически")

print("\n=== 8. flow_gap (расхождение матча с потоком) триггерит НАРАВНЕ с "
      "ambiguity — не только конкурирующий пик ===")
t.reset_tracking(to_acq=False)
capture()
install_fakes(**DISAGREE)
for _ in range(N):
    tick()
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "серия кадров с flow_gap (без ambiguity) не привела к IDENTITY_"
    "UNCERTAIN — вторая причина не работает")
assert t._match_dbg.get("identity_ambiguous") == 0, (
    "тест сам по себе негоден: ambiguous не должен был сработать в этом "
    "сценарии")
assert t._match_dbg.get("identity_flow_gap") == 1
print("    расхождение матча с потоком (без ambiguity) само по себе "
      "триггерит IDENTITY_UNCERTAIN")
t.reset_tracking(to_acq=False)

print("\n=== 9. Один сигнал из двух (только поток ИЛИ только матч) — "
      "streak НЕ трогается вообще (сравнивать не с чем, это не "
      "свидетельство ни за, ни против) ===")
capture()
install_fakes(**AMBIGUOUS)
for _ in range(N - 1):
    tick()
_streak_before = t._match_dbg.get("identity_uncertain_streak")
assert _streak_before == N - 1, "тест сам по себе негоден"


def fake_flow_fail(prev_g, cur_g, pts, cx, cy):
    return False, cx, cy


t.flow_predict = fake_flow_fail   # только матч, поток недоступен
tick()
assert t._match_dbg.get("identity_uncertain_streak") == _streak_before, (
    "streak изменился на кадре с одним сигналом из двух (flow_ok=False) — "
    "должен был остаться нетронутым: %r -> %r"
    % (_streak_before, t._match_dbg.get("identity_uncertain_streak")))
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "single-signal кадр (только матч) сам по себе не должен переводить в "
    "IDENTITY_UNCERTAIN")
print("    streak остался на %d после single-signal кадра (только матч, "
      "поток недоступен) — не сброшен и не увеличен" % _streak_before)
t.reset_tracking(to_acq=False)

print("\n=== 10. Центральное принуждение: _update_control_from_target_impl "
      "принудительно возвращает controllable=False, даже если кто-то ещё "
      "успел выставить глобальный target_controllable=True (тот же класс "
      "самопроверки, что уже поймал реальный баг у nudge-abort, ревью по "
      "dfdde00) ===")
capture()
install_fakes(**AMBIGUOUS)
for _ in range(N):
    tick()
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "тест сам по себе негоден")
with t.state_lock:
    t.target_controllable = True   # имитация гипотетической будущей дыры
t.update_control_from_target()
with t.state_lock:
    _forced_back = t.target_controllable
assert not _forced_back, (
    "_update_control_from_target_impl НЕ вернул controllable=False, хотя "
    "_identity_uncertain_pending всё ещё True — центральное принуждение "
    "не сработало")
print("    controllable принудительно возвращён в False центральной "
      "проверкой, несмотря на прямую попытку выставить True")
t.reset_tracking(to_acq=False)

print("\n=== 11. IDENTITY_UNCERTAIN_ENABLED=False: старое поведение "
      "сохранено (kill-switch) ===")
capture()
t.IDENTITY_UNCERTAIN_ENABLED = False
install_fakes(**AMBIGUOUS)
for _ in range(N + 5):
    tick()
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "IDENTITY_UNCERTAIN сработал несмотря на ENABLED=False")
with t.state_lock:
    assert t.target_controllable, (
        "controllable снят несмотря на ENABLED=False")
t.IDENTITY_UNCERTAIN_ENABLED = True
print("    %d сомнительных кадров при ENABLED=False -> track_state "
      "остался TRACKED, controllable не тронут" % (N + 5))
t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\n=== 12. По исходному тексту: ветка IDENTITY_UNCERTAIN в "
      "process_locked_tracker стоит РАНЬШЕ ветки ACQ (не даёт "
      "auto-reacquire в принципе провалиться через неё) ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def process_locked_tracker(")
i_uncertain = src.index(
    "if track_state == TRACK_STATE_IDENTITY_UNCERTAIN:", i_fn)
i_acq = src.index(
    "if track_state == TRACK_STATE_ACQ or "
    "(track_state == TRACK_STATE_LOST and not REQUIRE_AUX_TOGGLE_AFTER_LOST):",
    i_fn)
assert i_uncertain < i_acq, (
    "ветка IDENTITY_UNCERTAIN стоит ПОСЛЕ ветки ACQ — трекер в этом "
    "состоянии мог бы провалиться в обычный захват")
print("    ветка IDENTITY_UNCERTAIN стоит раньше ветки ACQ")

print("\n=== 13. CSV: все четыре колонки на месте и подключены в "
      "_capture_flight_row ===")
for col in ("identity_uncertain,", "identity_ambiguous,",
            "identity_flow_gap,", "identity_uncertain_streak,"):
    assert col in src, "колонка %r отсутствует в _FLIGHT_LOG_COLUMNS" % col
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
for key in ("identity_uncertain", "identity_ambiguous", "identity_flow_gap",
            "identity_uncertain_streak"):
    assert ('_match_dbg.get("%s")' % key) in row_body, (
        "_match_dbg.get(%r) не найден в _capture_flight_row" % key)
print("    все четыре колонки объявлены и подключены")

print("\nOK: IDENTITY_UNCERTAIN переиспользует существующие сигналы "
      "ambiguity/flow_gap (никаких новых порогов сравнения), дебаунс "
      "работает (одиночный кадр не триггерит, streak сбрасывается чистым "
      "кадром), срабатывание персистентно и не отходит само на "
      "естественных кадрах, auto-reacquire из этого состояния "
      "невозможен, explicit reset_tracking() — единственный выход, "
      "центральное принуждение controllable работает, kill-switch "
      "сохраняет старое поведение.")
