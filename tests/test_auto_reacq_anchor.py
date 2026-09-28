"""LOST -> AUTO_REACQ -> TRACKED не трогает confirmed identity anchor
(отчёт 25.09, разбор поверх 64949ec, п.H: "auto-reacquire после LOST через
template_match_locked() приемлем для кратковременной потери видимости... уже
частично реализовано — сохранить").

ОТЛИЧИЕ ОТ IDENTITY_UNCERTAIN. LOST auto-reacq ищет РАНЕЕ ПОДТВЕРЖДЁННУЮ
цель заново (template_match_locked против уже существующего template_gray,
расширенное окно) — это восстановление ЖИВОГО отслеживания после короткого
перерыва (occlusion/шум), не decision о новой identity. Точка H прямо
разрешает эту ветку — в отличие от IDENTITY_UNCERTAIN, где auto-reacquire
запрещён категорически (test_identity_uncertain.py §5). Секции 1-2
проверяют именно это разграничение: живой lock/prev_gray/prev_pts легитимно
переустанавливаются, а _identity_anchor_gray — НЕТ, ни на кадре HOLD/LOST,
ни на самом кадре успешного REACQ.

СЕКЦИЯ 3 — РЕАЛЬНАЯ ДЫРА, НАЙДЕННАЯ ОПЕРАТОРОМ ПРИ ЧТЕНИИ КОДА КОММИТА
71d6e00 (не поймана первой версией этого файла): секции 1-2 доказывают
только, что САМ anchor не мутирует. Они НЕ доказывают, что reacq-кандидат
(найденный через mutable template_gray) действительно соответствует
confirmed identity — а именно это и есть hard-lock invariant. Живой
template_gray способен со временем утечь от anchor (addWeighted-адаптация,
рескейл — см. mutation-site #2/#10/#11/#14 в отчёте), и до этой правки
AUTO_REACQ возвращал TRACKED/controllable=True по одному лишь score против
УЖЕ УЕХАВШЕГО template_gray, ни разу не сверяясь с anchor. Секция 3 строит
ровно такой сценарий (A -> live template тихо "уехал" на D -> LOST ->
рядом появляется B, точная копия D, а не A) и доказывает, что теперь
AUTO_REACQ его отклоняет, а не возвращается в TRACKED на чужом patch.
"""
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


def make_blank():
    return np.full((t.LORES_H, t.LORES_W), 120, np.uint8)


scene = make_scene()
blank = make_blank()

assert t.AUTO_REACQ_ENABLED, "тест сам по себе негоден без AUTO_REACQ_ENABLED"
assert t.REQUIRE_AUX_TOGGLE_AFTER_LOST, (
    "тест рассчитан именно на ветку LOST+AUTO_REACQ (REQUIRE_AUX_TOGGLE_"
    "AFTER_LOST=True) — см. tracker.py:11033")


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


capture()
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
_lock_at_capture = (t.lock_cx, t.lock_cy)
_events = []
t.flight_log.event = _events.append

print("=== 1. HOLD -> LOST: честная потеря (пустая сцена, flow убит "
      "детерминированно) — anchor не тронут ни на одном кадре ===")
t.prev_pts = None   # см. комментарий у аналогичного приёма в
                     # test_manual_nudge.py §E — без точек flow_predict
                     # падает собственным ранним выходом, детерминированно
_states = []
for i in range(t.LOST_LIMIT - 1):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(blank)
    _states.append(t.track_state)
    assert t._identity_anchor_gray is _anchor_ref, (
        "_identity_anchor_gray стал другим объектом на кадре %d потери "
        "(track_state=%s)" % (i, t.track_state))
    assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
        "байты _identity_anchor_gray изменились на кадре %d потери" % i)
    assert t._match_dbg.get("identity_anchor_changed") == 0, (
        "identity_anchor_changed=1 на кадре %d потери" % i)
assert t.TRACK_STATE_HOLD in _states, "тест сам по себе негоден: HOLD не встретился"
assert t.TRACK_STATE_LOST in _states, "тест сам по себе негоден: LOST не встретился"
print("    %d кадров HOLD/LOST — anchor не тронут ни разу (states: %s..%s)"
      % (len(_states), _states[0], _states[-1]))

print("\n=== 2. Возврат реальной цели -> AUTO_REACQ находит её и "
      "возвращает TRACKED БЕЗ снятия AUX — anchor всё равно не тронут ===")
assert t.track_state == t.TRACK_STATE_LOST, (
    "тест сам по себе негоден: ожидали закончить фазу 1 в LOST, получили %r"
    % t.track_state)
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)   # реальная цель снова в кадре
print("    track_state=%s overlay=%r lock=(%.1f,%.1f)"
      % (t.track_state, t.overlay_text, t.lock_cx, t.lock_cy))
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "AUTO_REACQ не сработал на реальном возврате цели, track_state=%r"
    % t.track_state)
assert t.overlay_text == "REACQ", (
    "track_state=TRACKED, но overlay=%r — ожидали именно ветку AUTO_REACQ "
    "('REACQ'), а не какой-то другой путь к TRACKED" % t.overlay_text)
with t.state_lock:
    _controllable = t.target_controllable
assert _controllable, "controllable не стал True после REACQ"
miss = ((t.lock_cx - _lock_at_capture[0]) ** 2
        + (t.lock_cy - _lock_at_capture[1]) ** 2) ** 0.5
assert miss < 3.0, (
    "AUTO_REACQ сел не на исходную позицию (промах %.1f px) — тест сам по "
    "себе негоден" % miss)

assert t._identity_anchor_gray is _anchor_ref, (
    "_identity_anchor_gray стал другим объектом НА САМОМ кадре AUTO_REACQ")
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes), (
    "байты _identity_anchor_gray изменились на кадре AUTO_REACQ")
assert t._match_dbg.get("identity_anchor_changed") == 0, (
    "identity_anchor_changed=1 на кадре AUTO_REACQ — восстановление "
    "видимости тихо подтвердило 'новую' identity")
_anchor_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
assert not _anchor_events, (
    "событие 'IDENTITY ANCHOR: подтверждена' произошло за весь эпизод "
    "HOLD -> LOST -> AUTO_REACQ: %s" % _anchor_events)
print("    AUTO_REACQ восстановил TRACKED на исходной позиции (промах "
      "%.1f px), БЕЗ снятия AUX4, и anchor не тронут ни объектом, ни "
      "байтами, ни диагностикой" % miss)

print("\n=== 3. A -> живой template 'уехал' на D -> LOST -> рядом B (копия "
      "D, НЕ A) -> AUTO_REACQ ОБЯЗАН отклонить, а не вернуть TRACKED на B "
      "===")
t.reset_tracking(to_acq=False)
capture()
_anchor_ref3 = t._identity_anchor_gray
_anchor_bytes3 = t._identity_anchor_gray.copy()
_lock_before_lost = (t.lock_cx, t.lock_cy)

# Живой template "утёк" от anchor — имитируем НАПРЯМУЮ (не гоняя реальную
# addWeighted-адаптацию десятками кадров: сам механизм дрейфа уже отдельно
# проверен в других файлах, здесь важен ТОЛЬКО факт "live template != anchor"
# как предпосылка сценария из отчёта оператора).
_rng3 = np.random.default_rng(999)
_drifted = (_rng3.random(t.template_gray.shape) * 255).astype(np.uint8)
cv2.circle(_drifted, (_drifted.shape[1] // 2, _drifted.shape[0] // 2),
           min(_drifted.shape) // 3, 200, -1)
assert not np.array_equal(_drifted, t.template_gray), (
    "тест сам по себе негоден: 'дрейфующий' patch случайно совпал с A")
t.template_gray = _drifted
t.sync_template_metadata()

t.prev_pts = None
for _ in range(t.LOST_LIMIT - 1):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(blank)
assert t.track_state == t.TRACK_STATE_LOST, (
    "тест сам по себе негоден: не удалось загнать в LOST")

th, tw = _drifted.shape[:2]
B_OFFSET = min(20.0, t.AUTO_REACQ_SEARCH_MARGIN * 0.4)


def trap_scene_b():
    """B — ТОЧНАЯ копия уехавшего live template (не A!), рядом с прежней
    позицией лока, в пределах окна AUTO_REACQ_SEARCH_MARGIN. Больше нигде
    в кадре нет ни A, ни D — только плоский фон."""
    g = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    bx = int(round(_lock_before_lost[0] + B_OFFSET - tw / 2))
    by = int(round(_lock_before_lost[1] - th / 2))
    g[by:by + th, bx:bx + tw] = _drifted
    return g


trap = trap_scene_b()
_events3 = []
t.flight_log.event = _events3.append
_clk.tick(FRAME_DT)
t.process_locked_tracker(trap)
print("    track_state=%s overlay=%r controllable=%s lock=(%.1f,%.1f)"
      % (t.track_state, t.overlay_text, t.target_controllable,
         t.lock_cx, t.lock_cy))

assert t.track_state != t.TRACK_STATE_TRACKED, (
    "AUTO_REACQ вернул TRACKED на B — patch, который совпадает с УЕХАВШИМ "
    "live template, но НЕ с confirmed identity anchor: ровно та дыра, что "
    "нашёл оператор при чтении кода 71d6e00")
with t.state_lock:
    _controllable3 = t.target_controllable
assert not _controllable3, "controllable стал True при возврате на B"
assert (t.lock_cx, t.lock_cy) == _lock_before_lost, (
    "lock_cx/cy сдвинулись на позицию B (%r), хотя коммит должен был быть "
    "отклонён anchor-проверкой" % ((t.lock_cx, t.lock_cy),))
assert t._identity_anchor_gray is _anchor_ref3
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes3)
_reacq_rej = [e for e in _events3 if e.startswith("AUTO_REACQ отклонён")]
assert len(_reacq_rej) == 1, (
    "ожидали ровно 1 событие 'AUTO_REACQ отклонён', получили %d: %s"
    % (len(_reacq_rej), _events3))
print("    событие: %s" % _reacq_rej[0])
print("    AUTO_REACQ НЕ вернул TRACKED на B — anchor-проверка сработала")

print("\n    --- контроль: ЭТА ЖЕ сцена B БЕЗ anchor-проверки "
      "действительно давала бы TRACKED (значит live-матч был реально "
      "силён, тест не вырожден) ---")
_orig_shadow_match = t._shadow_match_against_template


def _fake_anchor_always_confirms(gray, tmpl, tmpl_w, tmpl_h, tmpl_std,
                                  pred_cx, pred_cy, flow_motion):
    return True, 0.99, 5.0, 0.1, pred_cx, pred_cy


t._shadow_match_against_template = _fake_anchor_always_confirms
_clk.tick(FRAME_DT)
t.process_locked_tracker(trap)
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "тест сам по себе негоден: даже с ЗАВЕДОМО подтверждающим anchor-"
    "чеком сцена B не дала TRACKED — значит дело не в anchor-проверке, "
    "сценарий сконструирован неверно")
print("    подтверждено: без anchor-гейта (застаблен на 'всегда согласен') "
      "та же сцена B -> TRACKED — именно anchor-проверка блокирует "
      "реальный сценарий, не какая-то другая случайность")
t._shadow_match_against_template = _orig_shadow_match

t.reset_tracking(to_acq=False)

print("\nOK: живой lock/prev_gray/prev_pts легитимно переустанавливаются "
      "через HOLD -> LOST -> AUTO_REACQ (восстановление РАНЕЕ "
      "подтверждённой цели после кратковременной потери видимости), но "
      "confirmed identity anchor не трогается ни на одном кадре всего "
      "эпизода — отличие от IDENTITY_UNCERTAIN (где auto-reacquire "
      "запрещён категорически) сохранено верно. И, главное (секция 3): "
      "reacq-кандидат, который совпадает с утёкшим live template, но НЕ "
      "с confirmed identity anchor, корректно ОТКЛОНЯЕТСЯ — hard-lock "
      "invariant больше не имеет обходного пути через LOST.")
