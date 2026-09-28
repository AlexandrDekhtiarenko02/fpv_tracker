"""Полный аудит: единственный легитимный путь смены confirmed identity
anchor (отчёт 25.09, разбор поверх 64949ec, п.K-9 и явное закрывающее
требование того же разбора: "перечислить все места в tracker.py,
способные менять template_base, confirmed identity или координаты
anchor... не принимать следующую правку, пока не показан полный список
всех mutation sites identity/template").

ДВЕ НЕЗАВИСИМЫЕ ПРОВЕРКИ ОДНОГО И ТОГО ЖЕ УТВЕРЖДЕНИЯ:

  A. СТАТИЧЕСКАЯ (по исходному тексту). Ровно ТРИ места во всём файле
     присваивают _identity_anchor_gray напрямую: модульная инициализация
     (None), reset_tracking() (сброс в None) и тело самой
     _commit_confirmed_identity(). Ровно ОДНО место во всём файле зовёт
     _commit_confirmed_identity(...). Если правка когда-нибудь добавит
     второй вызов (Auto Template Refresh, size-adapt, LOST auto-reacq,
     reanchor и т.п. решат "тоже подтвердить identity") — эта проверка
     упадёт СРАЗУ, не дожидаясь полёта.

  B. ДИНАМИЧЕСКАЯ (по поведению). Один длинный смешанный сценарий,
     прогоняющий ПОДРЯД: первичный захват, циклы ручной коррекции,
     эпизод VISUAL_UNSTABLE, набор IDENTITY_UNCERTAIN -> explicit reset
     -> повторный захват, окно, пригодное для Auto Template Refresh.
     Считаем, сколько раз _match_dbg["identity_anchor_changed"] реально
     всплыл 1 и сколько раз в лог легло "IDENTITY ANCHOR: подтверждена" —
     оба числа обязаны СОВПАСТЬ с числом явных вызовов capture() (2), не
     больше и не меньше, независимо от того, что происходило между ними.

Список остальных мест, дописывающих ЖИВОЕ представление (template_gray/
template_base/lock_cx/lock_cy/prev_gray/prev_pts) — т.е. не identity, а её
текущее рабочее отображение — приведён в docstring _commit_confirmed_
identity() и в сопроводительном отчёте к коммиту; этот файл проверяет
только неприкосновенность САМОЙ identity (anchor), не живого отображения.
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

AUX2_IDX = t.MANUAL_NUDGE_ROLL_AUX_IDX
AUX3_IDX = t.MANUAL_NUDGE_PITCH_AUX_IDX

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked


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

print("=== A. СТАТИЧЕСКАЯ: ровно 3 присваивания _identity_anchor_gray, "
      "ровно 1 вызов _commit_confirmed_identity ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

import re
_assigns = re.findall(r"^\s*_identity_anchor_gray\s*=\s*\S", src, re.M)
print("    присваиваний _identity_anchor_gray = ...: %d" % len(_assigns))
assert len(_assigns) == 3, (
    "ожидали ровно 3 присваивания _identity_anchor_gray (модульная "
    "инициализация, reset_tracking, тело _commit_confirmed_identity), "
    "нашли %d — если это новое место, ОНО ОБЯЗАНО быть объяснено в "
    "отчёте о правке как явное подтверждение пилота, не тихая мутация"
    % len(_assigns))

def _is_real_call(pos):
    """Реальный вызов функции, а не упоминание в комментарии/докстроке —
    по тому же принципу, что и остальные source-проверки в этом наборе
    тестов (test_acq_ambiguity_reject.py §8 и т.п.): комментарий узнаём по
    '#' как первому непробельному символу строки ДО этой позиции."""
    line_start = src.rfind("\n", 0, pos) + 1
    prefix = src[line_start:pos]
    return not prefix.lstrip().startswith("#")


_calls = [m.start() for m in re.finditer(r"_commit_confirmed_identity\(", src)]
_def_start = src.index("def _commit_confirmed_identity(")
# def-строка начинается с 'def ' сразу перед найденным именем.
_def_call_pos = _def_start + len("def ")
_real_calls = [p for p in _calls if p != _def_call_pos and _is_real_call(p)]
print("    вызовов _commit_confirmed_identity(...): %d" % len(_real_calls))
assert len(_real_calls) == 1, (
    "ожидали ровно 1 вызывающее место _commit_confirmed_identity (первичный "
    "захват), нашли %d — новый вызывающий означает новый способ тихо "
    "сменить identity, что явно запрещено отчётом" % len(_real_calls))

_call_pos = _real_calls[0]
_call_line_start = src.rfind("\n", 0, _call_pos) + 1
_call_line_end = src.index("\n", _call_pos)
_call_line = src[_call_line_start:_call_line_end].strip()
print("    единственный вызов: %r" % _call_line)
# Он обязан быть внутри process_locked_tracker, в ветке первичного захвата
# (после estimate_initial_target -> ok=True), а не где-то в reanchor/
# adaptation/refresh/auto-reacq кодe.
i_fn = src.index("def process_locked_tracker(")
i_fn_end = src.index("\ndef ", i_fn + 1)
assert i_fn < _call_pos < i_fn_end, (
    "единственный вызов _commit_confirmed_identity находится ВНЕ "
    "process_locked_tracker — неожиданное место")
i_reanchor_def = src.index("def reanchor_tracker_at_current_box(")
i_reanchor_end = src.index("\ndef ", i_reanchor_def + 1)
assert not (i_reanchor_def < _call_pos < i_reanchor_end), (
    "вызов _commit_confirmed_identity найден ВНУТРИ "
    "reanchor_tracker_at_current_box — это ровно та регрессия (п.G), "
    "которую сам автор правки поймал и убрал ДО коммита")
print("    вызов внутри process_locked_tracker, НЕ внутри "
      "reanchor_tracker_at_current_box")

print("\n=== B. ДИНАМИЧЕСКАЯ: смешанный сценарий, identity_anchor_changed "
      "и события 'IDENTITY ANCHOR' считаются ТОЛЬКО на явных захватах ===")

_events = []
t.flight_log.event = _events.append
_anchor_changed_frames = 0


def capture():
    global _anchor_changed_frames
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
    if t._match_dbg.get("identity_anchor_changed") == 1:
        _anchor_changed_frames += 1


def tick():
    global _anchor_changed_frames
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    if t._match_dbg.get("identity_anchor_changed") == 1:
        _anchor_changed_frames += 1


def set_stick(roll_us, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def stable_cam():
    t._cam_shadow_dbg = {
        "sample_t": _clk.t, "jump": False, "top_saturated": False}


def unstable_cam(reason="jump"):
    d = {"sample_t": _clk.t, "jump": False, "top_saturated": False}
    d[reason] = True
    t._cam_shadow_dbg = d


def fake_flow_predict(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def make_fake_match(score, second, dist_from_pred=0.0):
    def fake_template_match_locked(gray, pred_cx, pred_cy, flow_motion=0.0,
                                    tgt_dx=0.0, tgt_dy=0.0):
        t._match_dbg["second"] = second
        return True, pred_cx + dist_from_pred, pred_cy, score
    return fake_template_match_locked


AMBIGUOUS = dict(score=0.90, second=0.88, dist_from_pred=0.0)

print("    --- шаг 1: первичный захват #1 ---")
capture()
stable_cam()
print("    --- шаг 2: 4 цикла ручной коррекции (отклонить/отпустить) ---")
for roll, pitch in ((300.0, 0.0), (-200.0, 150.0), (0.0, -250.0), (180.0, 180.0)):
    set_stick(roll, pitch)
    for _ in range(3):
        tick()
    set_stick(0)
    tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
print("    --- шаг 3: эпизод VISUAL_UNSTABLE (12 кадров) + восстановление ---")
unstable_cam("jump")
for _ in range(12):
    tick()
assert t.track_state == t.TRACK_STATE_VISUAL_UNSTABLE, "тест сам по себе негоден"
stable_cam()
tick()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
print("    --- шаг 4: серия IDENTITY_UNCERTAIN (неоднозначный матч) до "
      "срабатывания ---")
t.flow_predict = fake_flow_predict
t.template_match_locked = make_fake_match(**AMBIGUOUS)
N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES
for _ in range(N):
    tick()
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "тест сам по себе негоден: серия неоднозначных кадров не довела до "
    "IDENTITY_UNCERTAIN")
print("    --- шаг 5: explicit reset (пилот) + первичный захват #2 ---")
t.reset_tracking(to_acq=False)
assert t._identity_anchor_gray is None, "тест сам по себе негоден"
capture()
assert t.track_state == t.TRACK_STATE_TRACKED, "тест сам по себе негоден"
print("    --- шаг 6: ещё 40 обычных кадров (окно, где Auto Template "
      "Refresh технически МОГ БЫ сработать) ---")
stable_cam()
for _ in range(40):
    tick()

_anchor_events = [e for e in _events if e.startswith("IDENTITY ANCHOR")]
print("    identity_anchor_changed=1 подряд по всему сценарию: %d раз"
      % _anchor_changed_frames)
print("    событий 'IDENTITY ANCHOR: подтверждена' в логе: %d"
      % len(_anchor_events))
assert _anchor_changed_frames == 2, (
    "identity_anchor_changed=1 всплыл %d раз(а) за весь смешанный "
    "сценарий (nudge x4, VISUAL_UNSTABLE, IDENTITY_UNCERTAIN+reset, 40 "
    "обычных кадров) — ожидали РОВНО 2 (по числу явных capture()), "
    "значит какой-то механизм ТИХО подтвердил новую identity"
    % _anchor_changed_frames)
assert len(_anchor_events) == 2, (
    "событие 'IDENTITY ANCHOR: подтверждена' легло в лог %d раз(а), "
    "ожидали ровно 2" % len(_anchor_events))
print("    ОБА числа совпали с числом явных захватов (2) — ни один из "
      "промежуточных механизмов (4 цикла nudge, camera jump, "
      "IDENTITY_UNCERTAIN, 40 обычных кадров) не подтвердил identity "
      "тихо")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: и по исходному тексту (ровно 3 присваивания, ровно 1 вызов, "
      "вне reanchor), и по поведению (identity_anchor_changed/события "
      "'IDENTITY ANCHOR' считаются РОВНО по числу явных захватов через "
      "весь смешанный сценарий) — единственный легитимный путь смены "
      "confirmed identity anchor подтверждён с двух независимых сторон.")
