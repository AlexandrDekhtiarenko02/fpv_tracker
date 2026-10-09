"""Задержка control на время ручной коррекции рамки (прямая просьба
оператора: "мало отвожу стик - рамка не реагирует, сильно отвожу - резко
перелетает", нужна пауза, пока рамка ещё двигается, чтобы было удобно
целиться) — ЧЕТВЁРТАЯ версия, после ревью по c73aa24 и разбора бортового
лога 25.09 (5 CSV, ~110k строк, 96 заходов).

ПЕРВАЯ ВЕРСИЯ держала заморозку ЕЩЁ 1с ПОСЛЕ отпускания стика (окно
устоя). Ревью нашло это архитектурно неверным — убрано (c6fb464).

ВТОРАЯ/ТРЕТЬЯ ВЕРСИИ (a2f5fe0, dfdde00, c73aa24) свели ЛЮБОЕ прерывание
активной правки, не подтверждённое дедбендом при свежих AUX/RC, к ОДНОМУ
и тому же персистентному "MANUAL_NUDGE abort" (track_state=HOLD,
controllable=False до explicit reset_tracking()) — устраняя дыру, при
которой HOLD не был ранним выходом и controllable мог тихо вернуться
сам. Бортовой лог 25.09 показал побочный эффект: сессия 14:53:56 дала
MANUAL_NUDGE start=64, end=4, abort=59 — обычный transport jitter MSP
(RC/AUX кратко устаревает на пару кадров) на борту читался тем же кодом,
что и настоящая потеря eligibility, и почти ВСЕГДА эскалировал в
персистентный abort, хотя пилот стик не отпускал. Оператор явно исключил
"просто увеличить timeout" — нужна ГРАДАЦИЯ, не более длинный порог.

ЭТА ВЕРСИЯ (четвёртая) вводит Tier 1 между "активная правка" и
"персистентный abort": короткий AUX/RC transport-разрыв ПРИ track_state
всё ещё TRACKED приостанавливает control (тот же безопасный ответ, что
и на аварию) БЕЗ reanchor и БЕЗ персистентного abort — серия правок
остаётся "в процессе" (_nudge_frozen_box/_nudge_was_active не трогаются)
и разрешается САМА на первом же кадре с подтверждёнными данными: либо
чистым отпусканием (стик в дедбенде), либо возобновлением активной
правки (стик всё ещё отклонён) — в обоих случаях без explicit reset,
override не остаётся защёлкнутым. Настоящая потеря eligibility
(track_state сам ушёл из TRACKED, или MANUAL_NUDGE выключили на лету)
по-прежнему эскалирует НЕМЕДЛЕННО, минуя Tier 1 — это решение самой
системы, ждать нечего. Tier-1 разрыв, который тянется дольше
MANUAL_NUDGE_SUSPEND_TIMEOUT_S, эскалирует в тот же персистентный abort,
что и раньше был единственным исходом.

Заморозка control (снимок ДО-nudge box) по-прежнему живёт СТРОГО пока
стик реально отклонён ИЛИ идёт Tier-1 разрыв внутри той же серии правок —
снимается В ТОТ ЖЕ МОМЕНТ, что и сам reanchor, без отдельного таймера.
Переход на новую позицию доверен уже существующему, уже проверенному
механизму reanchor_tracker_at_current_box() -> _reset_geometry_history():
тот обнуляет tau/LOS-rate/prev_box_cx,cy/target_vx,vy_smoothed — то
есть ровно то, что не даёт скачку позиции превратиться в ложную
скорость. _slew_roll/pitch/yaw НЕ сбрасываются и сглаживают сам шаг
команды.

nudge_control_frozen в CSV пишется ПОСЛЕ проверки смены lock_sequence, не
до — иначе кадр самой смены мог соврать в логе (ревью по c6fb464).
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


def set_stick(roll_us, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick(0)
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def tick():
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)


ROLL_US = 450.0   # за MANUAL_NUDGE_DEADBAND_US=300
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = True

print("=== 1. Покой: заморозки нет ===")
capture()
assert t._nudge_frozen_box is None
assert t._match_dbg.get("nudge_control_frozen") in (0, None)
print("    _nudge_frozen_box=None, nudge_control_frozen=0")

print("\n=== 2. Первый активный кадр nudge: заморозка включается СРАЗУ, "
      "снимок = box ДО этого кадра ===")
with t.state_lock:
    _box_before = t.target_box_main
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1
assert t._nudge_frozen_box == _box_before, (
    "снимок заморозки обязан совпадать с box ДО начала правки")
assert t._match_dbg.get("nudge_control_frozen") == 1, (
    "control обязан считаться замороженным уже на первом активном кадре nudge")
print("    nudge_control_frozen=1, _nudge_frozen_box == box до правки")

print("\n=== 3. Серия кадров активного nudge: заморозка держится, снимок "
      "НЕ меняется. Удержание стика рамку не везёт дальше, больший ход — "
      "везёт, обратный ход пружины — нет ===")
_frozen_snapshot = t._nudge_frozen_box
_held = []
for _ in range(3):
    tick()
    assert t._match_dbg.get("nudge_control_frozen") == 1
    assert t._nudge_frozen_box == _frozen_snapshot, (
        "снимок заморозки сдвинулся ВО ВРЕМЯ активного nudge — обязан "
        "оставаться от самого начала правки")
    with t.state_lock:
        _held.append(t.target_box_main)
assert len(set(_held)) == 1, (
    "удержание стика на месте продолжило двигать рамку")
assert _held[-1] != _frozen_snapshot, (
    "target_box_main (живой) совпал с замороженным control-box")
set_stick(500.0)
tick()
with t.state_lock:
    _further = t.target_box_main
assert _further != _held[-1], (
    "больший ход стика не сдвинул рамку дальше — пилоту нечем довести")
set_stick(ROLL_US)
tick()
with t.state_lock:
    _back = t.target_box_main
assert _back == _further, (
    "обратный ход стика вернул рамку, защёлка не удержала дальний край")
assert t._nudge_frozen_box == _frozen_snapshot
print("    заморозка держится, упор уводит рамку дальше, пружина назад — нет")

print("\n=== 4. НАЙДЕНО ревью (п.1): отпускание стика снимает заморозку "
      "В ТОТ ЖЕ КАДР, что и reanchor — не секундой позже. control сразу "
      "использует НОВЫЙ (только что переустановленный) target_box_main ===")
_epoch_before_release = t.geometry_epoch
set_stick(0)
tick()
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
assert t.geometry_epoch == _epoch_before_release + 1, (
    "reanchor не сработал на кадре отпускания")
assert t._nudge_frozen_box is None, (
    "заморозка пережила reanchor — control ещё кадр читал бы старый box "
    "одновременно с уже новой geometry/template/flow (ровно то, что "
    "нашло ревью)")
assert t._match_dbg.get("nudge_control_frozen") == 0, (
    "nudge_control_frozen обязан стать 0 РОВНО на кадре reanchor, не позже")
print("    geometry_epoch %d -> %d (reanchor) И nudge_control_frozen=0 "
      "ОДНИМ И ТЕМ ЖЕ кадром — заморозка не переживает reanchor ни на "
      "кадр" % (_epoch_before_release, t.geometry_epoch))

print("\n=== 5. target_vx/vy_smoothed реально 0.0 сразу после reanchor — "
      "скачок box НЕ прочитан регулятором как мгновенная скорость (та "
      "самая защита от 'ложной скорости', на которую опирается фикс "
      "п.1). prev_box_cx/cy НЕ проверяем на None: _update_control_from_"
      "target_impl() в ЭТОМ ЖЕ кадре законно переустанавливает их под "
      "НОВЫЙ box — это база для СЛЕДУЮЩЕГО сравнения, не утечка старого ===")
assert t.target_vx_smoothed == 0.0 and t.target_vy_smoothed == 0.0, (
    "target_vx/vy_smoothed не обнулены после reanchor — скачок box мог "
    "быть прочитан как реальная скорость цели")
print("    target_vx/vy_smoothed=0.0 сразу после reanchor — "
      "_reset_geometry_history() внутри reanchor реально это делает, а "
      "не только по комментарию")

print("\n=== 6. НАЙДЕНО (борт 25.09, ревью по c73aa24): устаревший RC "
      "ПОСРЕДИ активной правки — это Tier 1 (краткая приостановка), а "
      "НЕ сразу авария. 59 из 63 бортовых сессий коррекции раньше ложно "
      "доходили до персистентного abort из-за обычного transport jitter "
      "MSP (RC/AUX устаревали на пару кадров), хотя пилот стик не "
      "отпускал и track_state не терял TRACKED — MANUAL_NUDGE_RC_FRESH_S "
      "просто короче типичного зазора опроса ===")
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1, "тест сам по себе негоден"
_frozen_before_gap = t._nudge_frozen_box
assert _frozen_before_gap is not None, "тест сам по себе негоден"
_events = []
t.flight_log.event = _events.append
# RC не обновляется — rc_link_ts стареет естественно с мокнутыми часами.
# МЕЛКИМИ шагами (FRAME_DT каждый), не одним прыжком: один большой прыжок
# сам пересёк бы НЕСВЯЗАННЫЙ порог frame_gap (FLOW_RASSH_SVEZH_S=0.20с в
# _update_control_from_target_impl) и вызвал бы _reset_geometry_history
# по СОВСЕМ ДРУГОЙ причине (тот же класс аккуратности, что уже
# потребовался для cam_jump_dt_ms в Camera Jump Shadow). Останавливаемся
# РОВНО на кадре, где manual_nudge впервые падает до 0 — это и есть
# первый кадр, где данные не подтвердились.
_elapsed = 0.0
while t._match_dbg.get("manual_nudge") == 1:
    _clk.tick(FRAME_DT)
    _elapsed += FRAME_DT
    t.process_locked_tracker(scene)
    assert _elapsed < 2.0, "тест сам по себе негоден: RC не устарел за 2с"
print("    RC устарел на кадре t+%.3fs — manual_nudge упал до 0" % _elapsed)

print("\n=== 6.1 Первый неподтверждённый кадр: Tier 1 — control "
      "приостановлен, но БЕЗ reanchor, БЕЗ персистентного abort, "
      "track_state остаётся TRACKED, замороженный box НЕ сброшен (серия "
      "правок жива и может разрешиться сама, без explicit reset) ===")
assert t._nudge_suspended_since_t is not None, (
    "Tier-1 таймер не запустился на первом же неподтверждённом кадре")
assert t._nudge_frozen_box == _frozen_before_gap, (
    "Tier 1 не обязан трогать замороженный box — правка ещё не завершена")
assert not t._nudge_abort_pending, (
    "персистентный abort выставился СРАЗУ на первом неподтверждённом "
    "кадре — Tier 1 обязан дать короткую отсрочку прежде чем считать "
    "это аварией (нельзя просто увеличить timeout — нужна отсрочка)")
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "track_state ушёл из TRACKED на Tier-1 кадре — Tier 1 не авария")
with t.state_lock:
    _controllable_tier1 = t.target_controllable
assert not _controllable_tier1, (
    "control не приостановлен на Tier-1 кадре — тот же безопасный ответ, "
    "что и на аварию, обязан сработать и здесь")
assert t._match_dbg.get("nudge_suspended") == 1
_suspend_events = [e for e in _events
                   if e.startswith("MANUAL_NUDGE suspend") and "timeout" not in e]
assert len(_suspend_events) == 1, (
    "ожидали ровно 1 событие MANUAL_NUDGE suspend, получили %d: %s"
    % (len(_suspend_events), _suspend_events))
print("    Tier 1: controllable=False, track_state=TRACKED, "
      "_nudge_frozen_box не тронут, _nudge_abort_pending=False, "
      "событие: %s" % _suspend_events[0])

print("\n=== 6.2 Разрыв тянется дольше MANUAL_NUDGE_SUSPEND_TIMEOUT_S — "
      "ТОЛЬКО теперь эскалация в персистентный abort (тот же путь, что и "
      "раньше был единственным: track_state=HOLD, controllable=False, "
      "explicit reset потребуется) ===")
_tier1_entry_t = t._nudge_suspended_since_t
_gap = 0.0
while _gap <= t.MANUAL_NUDGE_SUSPEND_TIMEOUT_S:
    _clk.tick(FRAME_DT)
    _gap = _clk.t - _tier1_entry_t
    t.process_locked_tracker(scene)
    assert _gap < t.MANUAL_NUDGE_SUSPEND_TIMEOUT_S + 2.0, (
        "тест сам по себе негоден: таймаут Tier 1 не наступил")
assert t._nudge_frozen_box is None, (
    "заморозка не снялась на эскалации Tier 1 -> abort")
assert t._nudge_suspended_since_t is None, (
    "Tier-1 таймер не сброшен на эскалации в персистентный abort")
with t.state_lock:
    _controllable_after_abort = t.target_controllable
assert not _controllable_after_abort, (
    "target_controllable остался True после эскалации в abort")
assert t.track_state == t.TRACK_STATE_HOLD, (
    "track_state=%r после эскалации, ожидали HOLD" % t.track_state)
assert t._nudge_abort_pending, "abort не выставился после таймаута Tier 1"
_abort_events = [e for e in _events if e.startswith("MANUAL_NUDGE abort")]
assert len(_abort_events) == 1, (
    "ожидали ровно 1 событие MANUAL_NUDGE abort после таймаута, "
    "получили %d: %s" % (len(_abort_events), _abort_events))
_timeout_events = [e for e in _events
                   if e.startswith("MANUAL_NUDGE suspend timeout")]
assert len(_timeout_events) == 1, (
    "ожидали событие эскалации 'MANUAL_NUDGE suspend timeout', "
    "получили %d: %s" % (len(_timeout_events), _timeout_events))
_reanchor_events = [e for e in _events if e.startswith("REANCHOR")]
assert not _reanchor_events, (
    "REANCHOR всё-таки случился при эскалации Tier 1 -> abort: %s"
    % _reanchor_events)
print("    разрыв длился %.2fs (>%.1fs): события suspend -> suspend "
      "timeout -> abort; track_state=HOLD, target_controllable=False, "
      "REANCHOR не вызывался" % (_gap, t.MANUAL_NUDGE_SUSPEND_TIMEOUT_S))
set_stick(0)
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
tick()   # вернуть в TRACKED для дальнейших секций

print("\n=== 6b. НАЙДЕНО ревью (та самая находка, ради которой сделан "
      "_nudge_genuine_release): track_state сам ушёл из TRACKED ПОСРЕДИ "
      "активной правки — RC при этом ИДЕАЛЬНО свежий, стик всё ещё "
      "отклонён. Старая узкая _nudge_rc_stale_abort пропускала ЭТОТ "
      "случай целиком (она проверяла только RC) — код падал в generic "
      "'оператор закончил', reanchor'ил И САМ возвращал track_state="
      "TRACKED/controllable=True, оживляя слежение, которое система "
      "только что сама сочла ненадёжным ===")
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1, "тест сам по себе негоден"
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
# Стик ОСТАЁТСЯ отклонённым, set_stick() не трогаем — единственное, что
# меняется, это track_state, имитируя трекер, который сам решил, что
# больше не уверен (реальный путь для этого — отдельный вопрос, здесь
# важно само условие "track_state != TRACKED при живом стике"). RC/AUX
# в диагностике покажется False не потому, что связь пропала, а потому,
# что _nudge_eligible падает РАНЬШЕ, чем код вообще их проверяет — это
# ожидаемо: важно здесь именно то, что track_state сам по себе, без
# какого-либо участия RC, уже обязан вести к abort, а не к reanchor.
with t.state_lock:
    t.track_state = t.TRACK_STATE_HOLD
_events2 = []
t.flight_log.event = _events2.append
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._nudge_frozen_box is None, (
    "заморозка пережила потерю eligibility по track_state")
with t.state_lock:
    _controllable_6b = t.target_controllable
assert not _controllable_6b, (
    "target_controllable=True после потери eligibility — nudge оживил "
    "слежение, которое трекер сам считал ненадёжным (ровно та ошибка, "
    "которую нашло ревью)")
_reanchor_6b = [e for e in _events2 if e.startswith("REANCHOR")]
assert not _reanchor_6b, (
    "REANCHOR вызван на кадре потери eligibility (не RC!) — старый "
    "узкий фикс пропускал именно этот путь: %s" % _reanchor_6b)
_abort_6b = [e for e in _events2 if e.startswith("MANUAL_NUDGE abort")]
assert len(_abort_6b) == 1, (
    "ожидали ровно 1 abort-событие на потерю eligibility, получили %d"
    % len(_abort_6b))
print("    track_state=TRACKED->HOLD при живом стике и свежем RC -> "
      "abort (не reanchor), controllable=False — nudge не оживил "
      "слежение: %s" % _abort_6b[0])
assert t._nudge_abort_pending, "тест сам по себе негоден: флаг не выставлен"
# НАЙДЕНО (ревью по c73aa24, разбор борта): Tier 1 существует ТОЛЬКО для
# transport jitter (AUX/RC), а не для настоящей потери eligibility —
# track_state сам ушёл из TRACKED здесь, а не устарели данные. Эскалация
# обязана быть НЕМЕДЛЕННОЙ, Tier-1 таймер вообще не должен был завестись.
_suspend_6b = [e for e in _events2 if e.startswith("MANUAL_NUDGE suspend")]
assert not _suspend_6b, (
    "Tier 1 завёлся на потере eligibility (track_state) — обязан "
    "эскалировать немедленно, минуя Tier 1: %s" % _suspend_6b)
assert t._nudge_suspended_since_t is None, (
    "_nudge_suspended_since_t выставлен хотя потеря eligibility обязана "
    "была эскалировать немедленно, минуя Tier 1")
print("    Tier 1 не завёлся — потеря eligibility эскалирует немедленно, "
      "в отличие от краткого AUX/RC gap (см. секцию 6)")

print("\n=== 6c. ГЛАВНАЯ НАХОДКА ЭТОГО РЕВЬЮ: после abort НИЧЕГО не "
      "восстанавливаем руками — гоним ЕСТЕСТВЕННЫЕ следующие кадры (тем "
      "же неизменным scene, где flow/match вполне может само найти "
      "tracked_ok=True на промежуточной nudge-позиции) и проверяем, что "
      "controllable НЕ включился обратно САМ. HOLD не ранний выход — до "
      "этой правки именно тут automatic control мог вернуться после "
      "ровно одного 'удачного' кадра, без единого явного действия "
      "пилота ===")
set_stick(0)   # стик отпущен по-настоящему — как сделал бы пилот
_controllable_seen = []
_track_states_seen = []
for _ in range(15):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    with t.state_lock:
        _controllable_seen.append(t.target_controllable)
    _track_states_seen.append(t.track_state)
    assert t._nudge_abort_pending, (
        "_nudge_abort_pending снялся сам, без explicit pilot-действия "
        "(reset_tracking) — флаг обязан быть персистентным")
assert not any(_controllable_seen), (
    "target_controllable стал True хотя бы на одном из %d естественных "
    "кадров ПОСЛЕ abort без единого explicit pilot-действия — ровно та "
    "дыра, которую нашло ревью: %s" % (len(_controllable_seen), _controllable_seen))
print("    %d естественных кадров подряд (track_state по кадрам: %s) — "
      "target_controllable НИ РАЗУ не стал True без явного действия "
      "пилота" % (len(_controllable_seen), _track_states_seen))

print("\n=== 6d. Явное действие пилота (reset_tracking — тот же AUX4-"
      "toggle UX, что уже выводит из LOST/TOGGLE) снимает флаг и "
      "возвращает нормальную жизнь трекера ===")
t.reset_tracking(to_acq=False)
assert not t._nudge_abort_pending, (
    "reset_tracking() не снял _nudge_abort_pending")
assert t._nudge_suspended_since_t is None, (
    "reset_tracking() не снял _nudge_suspended_since_t")
capture()
with t.state_lock:
    _controllable_after_reset = t.target_controllable
assert _controllable_after_reset, (
    "после explicit pilot-действия (reset + новый захват) controllable "
    "обязан снова заработать нормально")
print("    reset_tracking() снял _nudge_abort_pending; новый захват "
      "controllable=True — нормальная жизнь восстановлена явным "
      "действием, не сама по себе")

print("\n=== 6e. Краткий gap, который РАЗРЕШАЕТСЯ САМ: активная правка -> "
      "недолгий RC/AUX разрыв (меньше MANUAL_NUDGE_SUSPEND_TIMEOUT_S) -> "
      "свежие данные снова, стик НЕЙТРАЛЕН -> чистое отпускание БЕЗ "
      "explicit reset. Прямое требование по разбору борта 25.09: 'в "
      "normal-release сценарии override не должен оставаться "
      "защёлкнутым' ===")
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1, "тест сам по себе негоден"
_frozen_6e = t._nudge_frozen_box
assert _frozen_6e is not None, "тест сам по себе негоден"
_events_6e = []
t.flight_log.event = _events_6e.append
# Короткий разрыв: тикаем МЕЛКИМИ шагами, как в секции 6, до кадра, где
# RC впервые читается несвежим (manual_nudge падает до 0) — тот же приём,
# не жёстко заданное число кадров (зависит от MANUAL_NUDGE_RC_FRESH_S).
_elapsed_6e = 0.0
while t._match_dbg.get("manual_nudge") == 1:
    _clk.tick(FRAME_DT)
    _elapsed_6e += FRAME_DT
    t.process_locked_tracker(scene)
    assert _elapsed_6e < 2.0, "тест сам по себе негоден: RC не устарел за 2с"
assert _elapsed_6e < t.MANUAL_NUDGE_SUSPEND_TIMEOUT_S, (
    "тест сам по себе негоден: RC устарел уже после таймаута Tier 1 — "
    "это больше не 'короткий' gap")
assert t._nudge_suspended_since_t is not None, (
    "короткий разрыв обязан завести Tier-1 таймер")
assert t._nudge_frozen_box == _frozen_6e, (
    "Tier 1 не обязан трогать замороженный box")
with t.state_lock:
    assert not t.target_controllable, (
        "control обязан быть на паузе во время Tier 1")
# Данные снова свежие, стик В ДЕДБЕНДЕ — подтверждённое отпускание.
set_stick(0)
_epoch_before_6e = t.geometry_epoch
tick()
assert t._nudge_suspended_since_t is None, (
    "Tier-1 таймер не сброшен на кадре подтверждённого отпускания")
assert not t._nudge_abort_pending, (
    "короткий gap ложно дошёл до персистентного abort — ровно та "
    "ошибка, из-за которой 59/63 бортовых сессий 25.09 false-abort'ились "
    "без Tier 1 (см. секцию 6)")
assert t._nudge_frozen_box is None, (
    "заморозка пережила подтверждённое отпускание после короткого gap")
assert t.geometry_epoch == _epoch_before_6e + 1, (
    "reanchor не сработал на кадре чистого отпускания после Tier 1")
assert t.track_state == t.TRACK_STATE_TRACKED
with t.state_lock:
    _controllable_6e = t.target_controllable
assert _controllable_6e, (
    "controllable не вернулся в True на кадре чистого отпускания")
_reanchor_6e = [e for e in _events_6e if e.startswith("REANCHOR")]
assert _reanchor_6e, "REANCHOR не залогирован на чистом отпускании"
_abort_6e = [e for e in _events_6e if e.startswith("MANUAL_NUDGE abort")]
assert not _abort_6e, (
    "MANUAL_NUDGE abort залогирован на нормальном отпускании после "
    "короткого gap: %s" % _abort_6e)
print("    короткий gap (%.3fs) -> подтверждённый нейтральный стик -> "
      "reanchor, controllable=True, БЕЗ persistent abort и БЕЗ explicit "
      "reset" % _elapsed_6e)

# Override реально ЖИВОЙ на дальнейших естественных кадрах — контраст с
# 6c/6d, где после НАСТОЯЩЕГО abort controllable остаётся заблокирован на
# любом числе кадров без explicit действия пилота. Здесь проверяем не
# общее качество слежения (это вне того, что меняет эта правка), а
# именно то, что МЕХАНИЗМ nudge ничего не защёлкивает повторно: abort-
# флаг и Tier-1-таймер остаются снятыми на протяжении естественной жизни.
for _ in range(10):
    tick()
    assert not t._nudge_abort_pending, (
        "_nudge_abort_pending выставился САМ на естественном кадре после "
        "чистого release через Tier 1 — override оказался защёлкнут "
        "задним числом")
    assert t._nudge_suspended_since_t is None, (
        "Tier-1 таймер завёлся сам на естественном кадре после чистого "
        "release — не должно быть активного nudge, которому он нужен")
print("    10 естественных кадров подряд после release — ни abort, ни "
      "Tier-1 таймер не завелись сами по себе (override не защёлкнут "
      "механизмом nudge)")

print("\n=== 6f. Краткий gap, а на выходе стик ВСЁ ЕЩЁ отклонён: "
      "продолжение той же правки, не новое начало — снимок заморозки НЕ "
      "меняется (control возвращается к ТОЙ ЖЕ до-nudge позиции, что и "
      "была до gap, а не к позиции в момент возобновления) ===")
set_stick(ROLL_US)
tick()
assert t._match_dbg.get("manual_nudge") == 1, "тест сам по себе негоден"
_frozen_6f = t._nudge_frozen_box
assert _frozen_6f is not None, "тест сам по себе негоден"
_elapsed_6f = 0.0
while t._match_dbg.get("manual_nudge") == 1:
    _clk.tick(FRAME_DT)
    _elapsed_6f += FRAME_DT
    t.process_locked_tracker(scene)
    assert _elapsed_6f < 2.0, "тест сам по себе негоден: RC не устарел за 2с"
assert _elapsed_6f < t.MANUAL_NUDGE_SUSPEND_TIMEOUT_S, (
    "тест сам по себе негоден: RC устарел уже после таймаута Tier 1 — "
    "это больше не 'короткий' gap")
assert t._nudge_suspended_since_t is not None, (
    "короткий разрыв обязан завести Tier-1 таймер")
with t.state_lock:
    assert not t.target_controllable, (
        "control обязан быть на паузе во время Tier 1")
# Данные снова свежие, стик ВСЁ ЕЩЁ отклонён — пилот его не отпускал,
# это была просто пауза в телеметрии.
set_stick(ROLL_US)
_epoch_before_6f = t.geometry_epoch
_events_6f = []
t.flight_log.event = _events_6f.append
tick()
assert t._match_dbg.get("manual_nudge") == 1, (
    "активный nudge не возобновился при свежих данных и отклонённом стике")
assert t._nudge_suspended_since_t is None, (
    "Tier-1 таймер не сброшен при возобновлении активного nudge")
assert t._nudge_frozen_box == _frozen_6f, (
    "снимок заморозки изменился при возобновлении — обязан оставаться "
    "от САМОГО ПЕРВОГО начала этой серии правок, gap её не прерывал")
assert t.geometry_epoch == _epoch_before_6f, (
    "reanchor сработал при возобновлении активного nudge — gap не "
    "должен был расцениваться как отпускание")
with t.state_lock:
    _controllable_6f = t.target_controllable
assert _controllable_6f, (
    "controllable не вернулся в True при возобновлении активного nudge")
_reanchor_6f = [e for e in _events_6f if e.startswith("REANCHOR")]
assert not _reanchor_6f, (
    "REANCHOR залогирован при возобновлении активного nudge: %s"
    % _reanchor_6f)
print("    короткий gap (%.3fs) -> стик всё ещё отклонён -> активный "
      "nudge возобновился С ТЕМ ЖЕ замороженным box, без reanchor"
      % _elapsed_6f)
set_stick(0)
tick()   # чистое отпускание, вернуть в спокойное TRACKED для секции 7

print("\n=== 7. НАЙДЕНО ревью (п.4): nudge_control_frozen в CSV не "
      "врёт на кадре смены lock_sequence — пишется ПОСЛЕ safety-сброса ===")
set_stick(ROLL_US)
tick()
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
t.lock_sequence += 1   # имитация быстрого reacq
t.update_control_from_target()
assert t._nudge_frozen_box is None, (
    "смена lock_sequence не сбросила заморозку")
assert t._match_dbg.get("nudge_control_frozen") == 0, (
    "nudge_control_frozen соврал '1' на кадре, где заморозка уже снята "
    "сменой lock_sequence — диагностика писалась ДО safety-сброса")
print("    nudge_control_frozen=0 корректно на кадре смены lock_sequence "
      "(диагностика пишется после safety-сброса)")
set_stick(0)
with t.state_lock:
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_controllable = True
tick()

print("\n=== 8. MANUAL_NUDGE_CONTROL_DELAY_ENABLED=False: заморозки нет "
      "никогда, поведение как до этой правки ===")
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = False
set_stick(ROLL_US)
for _ in range(5):
    tick()
    assert t._nudge_frozen_box is None
    assert t._match_dbg.get("nudge_control_frozen") in (0, None)
set_stick(0)
tick()
t.MANUAL_NUDGE_CONTROL_DELAY_ENABLED = True
print("    5 активных кадров nudge при ENABLED=False -> заморозка не "
      "включилась ни разу")

print("\n=== 9. reset_tracking() снимает заморозку И Tier-1 таймер — "
      "следующий лок не наследует чужое состояние ===")
set_stick(ROLL_US)
tick()
assert t._nudge_frozen_box is not None, "тест сам по себе негоден"
# Заводим Tier-1 таймер ПЕРЕД reset_tracking() — иначе проверка ниже была
# бы тривиально верна и тогда, когда reset_tracking() вообще не трогает
# _nudge_suspended_since_t (он и так был бы None, если Tier 1 не начат).
_elapsed_9 = 0.0
while t._match_dbg.get("manual_nudge") == 1:
    _clk.tick(FRAME_DT)
    _elapsed_9 += FRAME_DT
    t.process_locked_tracker(scene)
    assert _elapsed_9 < 2.0, "тест сам по себе негоден: RC не устарел за 2с"
assert t._nudge_suspended_since_t is not None, (
    "тест сам по себе негоден: Tier-1 таймер не завёлся")
t.reset_tracking(to_acq=False)
assert t._nudge_frozen_box is None, (
    "_nudge_frozen_box пережил reset_tracking()")
assert t._nudge_suspended_since_t is None, (
    "_nudge_suspended_since_t пережил reset_tracking() — новый лок мог "
    "бы унаследовать чужой, уже бессмысленный отсчёт Tier 1")
set_stick(0)
capture()
print("    reset_tracking() очищает и _nudge_frozen_box, и "
      "_nudge_suspended_since_t (проверено из реально заведённого "
      "состояния Tier 1, не из уже-пустого)")

print("\n=== 10. По исходному тексту: заморозка читается ДО ветки "
      "'not controllable or box is None' — существующая защита не "
      "обходится ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_fn = src.index("def _update_control_from_target_impl():")
i_freeze = src.index(
    "box = _nudge_frozen_box if _nudge_frozen_box is not None else _live_box",
    i_fn)
i_safety = src.index("if not controllable or box is None:", i_fn)
assert i_freeze < i_safety
print("    заморозка читается раньше safety-ветки 'not controllable'")

print("\n=== 11. CSV: nudge_control_frozen/nudge_abort_pending/"
      "nudge_suspended на месте (nudge_settle_remaining_ms убран вместе "
      "с окном устоя) ===")
assert "nudge_control_frozen," in src
assert "nudge_abort_pending," in src
assert "nudge_suspended," in src
assert "nudge_settle_remaining_ms" not in src, (
    "убранное окно устоя оставило след в CSV/коде — nudge_settle_"
    "remaining_ms всё ещё где-то упоминается")
i_row = src.index("def _capture_flight_row")
i_row_end = src.index("\ndef ", i_row + 1)
row_body = src[i_row:i_row_end]
assert '_match_dbg.get("nudge_control_frozen")' in row_body
assert '_match_dbg.get("nudge_abort_pending")' in row_body
assert '_match_dbg.get("nudge_suspended")' in row_body
print("    все три колонки на месте, окно устоя нигде не осталось")

print("\nOK: заморозка control на время ручной коррекции живёт строго "
      "пока стик отклонён, снимается ОДНИМ кадром с reanchor (не смешивая "
      "старый box с уже новой geometry-историей), переход сглажен уже "
      "существующим _reset_geometry_history()+slew; краткий AUX/RC "
      "transport-разрыв ПОСРЕДИ правки — Tier 1 (пауза control без "
      "reanchor и без persistent abort, разрешается сам на первом же "
      "подтверждённом кадре), эскалирует в persistent abort только если "
      "тянется дольше MANUAL_NUDGE_SUSPEND_TIMEOUT_S; настоящая потеря "
      "eligibility (track_state) эскалирует немедленно, минуя Tier 1; "
      "диагностика в CSV не врёт на кадре смены лока")
