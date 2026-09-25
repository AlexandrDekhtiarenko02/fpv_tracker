"""AUTO TEMPLATE REFRESH — первый LIVE-эффект поверх Tracking Shadow
Template Identity (ревью 24.09.2026: "хватит делать только диагностику,
нужен видимый эффект на трекере").

ОСНОВАНИЕ. Четыре независимых ручных reanchor (#13, #10 x2, #14) в разборе
14 заходов воспроизведены кадр-в-кадр: свежий template на той же позиции
чинит score/PSR почти мгновенно. Temporal fresh (после фикса self-match
bias, 3c47c58) умеет проверять ровно это — "переживёт ли новый template
время" — на будущем кадре, не на самом себе. Раньше это было только
числом в CSV (shadow_track_psr); теперь то же измерение, взятое сериями
(AUTO_TEMPLATE_REFRESH_CONFIRM_N подряд идущих fresh-оценок), управляет
живым template_gray.

СТРОГО ТОЛЬКО ШАБЛОН. Не двигает lock_cx/lock_cy/lock_w/lock_h, не рвёт
geometry_epoch, не трогает prev_pts/prev_gray/template_base/
template_scale_acc — см. докстроку константы AUTO_TEMPLATE_REFRESH_
ENABLED в tracker.py для полного обоснования каждого пункта.

Этот файл проверяет: (1) триггер срабатывает РОВНО на N-м подтверждении
подряд, не раньше; (2) один "плохой" голос сбрасывает серию целиком, не
декрементирует; (3) cooldown блокирует повторное срабатывание, даже если
серия набралась снова, и отпускает его СРАЗУ по истечении (без нужды
набирать N заново); (4) geometry_epoch discontinuity сбрасывает серию;
(5) полный reset_tracking() сбрасывает серию/cooldown-таймер/окно
оверлея, но НЕ накопительный счётчик; (6) ENABLED=False — ни одного
триггера, вообще; (7) на кадре срабатывания box/geometry/template_base не
трогаются — меняется только template; (8) по исходному тексту — то же
самое, но статически; (9) событие AUTO_TEMPLATE_REFRESH логируется с
верными числами; (10) оверлей "TREF" держится заданное окно и гаснет;
(11) PSR-margin и flow_gap-порог реально гейтят голос, а не любой ok=True.
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
CX, CY = t.LORES_W // 2, t.LORES_H // 2


def make_scene(cx, cy, size, offset=0, seed=7):
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    rng = np.random.default_rng(seed)
    obj = (rng.random((size, size)) * 90 + 110).astype(np.uint8)
    cv2.circle(obj, (size // 2, size // 2), max(2, size // 3), 40, -1)
    x0, y0 = cx + offset - size // 2, cy - size // 2
    frame[y0:y0 + size, x0:x0 + size] = obj
    return frame


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()
    t.reset_tracking(to_acq=False)


def acquire():
    force_reset()
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    with t.state_lock:
        t.app_state["rc_channels"] = [1500] * 8
        t.app_state["rc_link_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(make_scene(CX, CY, 30))
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


_off = [0]
# Секция 7 сравнивает ENABLED=True/False на ОДНОЙ последовательности
# кадров — offset обязан быть детерминирован НЕЗАВИСИМО от того, сколько
# именно tick() потребовалось next_fresh_slot(), чтобы дойти до нужного
# fresh-слота (это число само по себе может отличаться между прогонами
# из-за чередования base/fresh). Статичная сцена (offset всегда 0) это
# снимает целиком.
_off_frozen = [False]


def tick(cb_t0=None):
    with t.state_lock:
        t.app_state["fc_pitch_deg"] = 10.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    if _off_frozen[0]:
        _off[0] = 0
    else:
        _off[0] = (_off[0] % 20) + 1
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(make_scene(CX, CY, 30, offset=_off[0]), cb_t0=cb_t0)


def next_active_slot(max_frames=64):
    """Гонит кадры, пока Tracking Shadow не отработает активный слот
    (variant заполнен) — не полагаемся на чётность frame_index."""
    for _ in range(max_frames):
        tick()
        if t._shadow_track_dbg.get("variant") is not None:
            return t._shadow_track_dbg.get("variant")
    raise AssertionError("не дождались активного слота")


def next_fresh_slot(max_frames=64):
    """Гонит кадры до следующего fresh-слота С валидным candidate
    (active=True) — именно те, что голосуют за AUTO_TEMPLATE_REFRESH."""
    for _ in range(max_frames):
        v = next_active_slot()
        if v == "fresh" and t._shadow_track_dbg.get("active"):
            return
    raise AssertionError("не дождались валидного fresh-слота")


def fake_match(ok, score, psr, second, gap_px=0.0):
    """Подменяет _shadow_match_against_template фиксированным, полностью
    управляемым результатом — для AUTO_TEMPLATE_REFRESH важны именно
    ok/psr/gap, не реальная картинка. mx/my считаются от pred_cx/pred_cy,
    переданных ЭТИМ ЖЕ вызовом, чтобы gap_px был точным независимо от
    текущего pred_cx/pred_cy."""
    def _f(gray, tmpl, tmpl_w, tmpl_h, tmpl_std, pred_cx, pred_cy, flow_motion):
        return ok, score, psr, second, pred_cx + gap_px, pred_cy
    return _f


def fake_live(psr, score=0.7, second=0.1):
    """Подменяет template_match_locked фиксированным live-результатом и
    честно пишет _match_dbg['psr'] — ровно так, как это делает реальная
    функция (см. tracker.py: _match_dbg['psr'] = psr на каждом вызове)."""
    def _f(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
        t._match_dbg["psr"] = psr
        t._match_dbg["second"] = second
        t._match_dbg["margin"] = 20.0
        return True, pred_cx, pred_cy, score
    return _f


# FREEZE_TEMPLATE=True: гасит НЕСВЯЗАННЫЙ существующий механизм —
# плавную addWeighted-адаптацию template_gray на каждом confident-матче
# (score>=0.60, см. process_locked_tracker). fake_live() ниже намеренно
# держит match_ok=True/score стабильным каждый кадр (нужно для
# tracked_ok), что иначе ТОЖЕ потихоньку меняло бы template_gray —
# найдено этим же тестом: без FREEZE_TEMPLATE секция 2 ловила ложное
# "template_gray изменился" от addWeighted, а не от AUTO_TEMPLATE_
# REFRESH. Сам AUTO_TEMPLATE_REFRESH на FREEZE_TEMPLATE не завязан —
# он вызывает build_template() напрямую, не addWeighted.
_orig_freeze_template = t.FREEZE_TEMPLATE
t.FREEZE_TEMPLATE = True
# Третий, тоже несвязанный механизм: примерка масштаба (SIZE_ADAPT_*)
# пересобирает template_gray ИЗ template_base на накопленном
# template_scale_acc, если её измерение размера чуть уедет от 1.0 —
# гасим и её, оставляя live-пишущим внутри этого теста только сам
# AUTO_TEMPLATE_REFRESH.
_orig_size_adapt = t.SIZE_ADAPT_ENABLED
t.SIZE_ADAPT_ENABLED = False

t.TRACKING_SHADOW_ENABLED = True
t.TRACKING_SHADOW_EVERY_N_FRAMES = 4
t.AUTO_TEMPLATE_REFRESH_ENABLED = True
t.AUTO_TEMPLATE_REFRESH_CONFIRM_N = 3
t.AUTO_TEMPLATE_REFRESH_PSR_MARGIN = 1.0
t.AUTO_TEMPLATE_REFRESH_COOLDOWN_S = 4.0
t.AUTO_TEMPLATE_REFRESH_OVERLAY_S = 0.6
N = t.AUTO_TEMPLATE_REFRESH_CONFIRM_N
LIVE_PSR = 2.0
GOOD_FRESH_PSR = LIVE_PSR + t.AUTO_TEMPLATE_REFRESH_PSR_MARGIN + 0.5   # уверенно выше margin

_orig_shadow_match = t._shadow_match_against_template
_orig_live_match = t.template_match_locked
t._shadow_match_against_template = fake_match(True, 0.9, GOOD_FRESH_PSR, 0.1, gap_px=0.0)
t.template_match_locked = fake_live(LIVE_PSR)

print("=== 1. Триггер срабатывает РОВНО на %d-м подтверждении подряд, "
      "не раньше ===" % N)
acquire()
_tg_before = t.template_gray.copy()
for i in range(N - 1):
    next_fresh_slot()
    assert t._auto_tref_confirm_streak == i + 1, (
        "после %d-го хорошего голоса ожидали streak=%d, получили %d"
        % (i + 1, i + 1, t._auto_tref_confirm_streak))
    assert np.array_equal(t.template_gray, _tg_before), (
        "template_gray не должен меняться раньше %d-го подтверждения" % N)
_count_before = t._auto_tref_total_count
next_fresh_slot()   # N-е подтверждение
assert t._auto_tref_confirm_streak == 0, (
    "после срабатывания streak обязан сброситься в 0, получили %d"
    % t._auto_tref_confirm_streak)
assert t._auto_tref_total_count == _count_before + 1, (
    "auto_template_refresh_count обязан вырасти ровно на 1")
assert not np.array_equal(t.template_gray, _tg_before), (
    "template_gray обязан измениться после %d-го подтверждения" % N)
print("    после %d-1 голосов streak рос 1..%d, template_gray не менялся; "
      "на %d-м — сработал, streak сброшен, count=%d"
      % (N, N - 1, N, t._auto_tref_total_count))

print("\n=== 2. Один плохой голос сбрасывает серию целиком (не "
      "декремент) ===")
acquire()
for i in range(N - 1):
    next_fresh_slot()
assert t._auto_tref_confirm_streak == N - 1
# Плохой голос: psr ниже margin.
t._shadow_match_against_template = fake_match(
    True, 0.9, LIVE_PSR + 0.1, 0.1, gap_px=0.0)
next_fresh_slot()
assert t._auto_tref_confirm_streak == 0, (
    "плохой голос обязан сбросить серию в 0, получили %d"
    % t._auto_tref_confirm_streak)
t._shadow_match_against_template = fake_match(True, 0.9, GOOD_FRESH_PSR, 0.1, gap_px=0.0)
_tg_after_bad = t.template_gray.copy()
for i in range(N - 1):
    next_fresh_slot()
assert np.array_equal(t.template_gray, _tg_after_bad), (
    "после сброса нужно СНОВА %d подряд хороших голосов — %d недостаточно"
    % (N, N - 1))
print("    плохой голос обнулил серию; для повторного срабатывания "
      "нужны все %d заново, не %d" % (N, N - 1))

print("\n=== 3. Cooldown блокирует повторное срабатывание даже при "
      "повторно набранной серии — и отпускает СРАЗУ по истечении ===")
acquire()
_count0 = t._auto_tref_total_count
for i in range(N):
    next_fresh_slot()
assert t._auto_tref_total_count == _count0 + 1
_tg_trigger1 = t.template_gray.copy()
_t_trigger1 = t._auto_tref_last_t
assert _t_trigger1 is not None
# Серия набирается снова, но cooldown ещё не истёк (мокнутые часы почти
# не сдвинулись за N слотов).
for i in range(N):
    next_fresh_slot()
assert t._auto_tref_total_count == _count0 + 1, (
    "cooldown обязан заблокировать 2-е срабатывание, count=%d"
    % t._auto_tref_total_count)
assert np.array_equal(t.template_gray, _tg_trigger1), (
    "template_gray не должен был измениться под cooldown")
assert t._auto_tref_confirm_streak >= N, (
    "серия НЕ должна сбрасываться, пока сброс заблокирован именно "
    "cooldown'ом (не голосами) — иначе 'отпустить сразу' ниже неверно")
# Отодвигаем часы за cooldown и подаём ОДИН добавочный хороший голос —
# обязан сработать немедленно, без набора новой серии с нуля.
# Малыми шагами (FLOW_RASSH_SVEZH_S=0.20с — порог, после которого разрыв
# МЕЖДУ КАДРАМИ сам считается frame_gap и рвёт geometry_epoch, см.
# _reset_geometry_history "frame_gap %.3fs" в process_locked_tracker).
# Один большой прыжок часов сюда НЕЛЬЗЯ — он сам выглядел бы как реальный
# разрыв непрерывности и сбросил бы серию ДО того, как голос вообще
# случится: смежный по духу баг с самим этим тестом, найден при первом
# прогоне (streak 3->1 вместо роста, из-за незапланированного frame_gap).
_needed = t.AUTO_TEMPLATE_REFRESH_COOLDOWN_S + 0.5
t.AUTO_TEMPLATE_REFRESH_ENABLED = False   # не даём голосам сработать ДО конца ожидания
while _clk.t - _t_trigger1 < _needed:
    tick()
t.AUTO_TEMPLATE_REFRESH_ENABLED = True
next_fresh_slot()
assert t._auto_tref_total_count == _count0 + 2, (
    "по истечении cooldown следующий же голос обязан сработать, count=%d"
    % t._auto_tref_total_count)
assert not np.array_equal(t.template_gray, _tg_trigger1)
print("    2-е срабатывание заблокировано cooldown (count остался 1), "
      "после истечения — сработало на первом же голосе (count=2)")

print("\n=== 4. geometry_epoch discontinuity сбрасывает серию ===")
acquire()
for i in range(N - 1):
    next_fresh_slot()
assert t._auto_tref_confirm_streak == N - 1
t._reset_geometry_history("test_epoch_break")
assert t._auto_tref_confirm_streak == 0, (
    "_reset_geometry_history() обязан сбросить серию AUTO_TEMPLATE_REFRESH")
_tg_after_epoch = t.template_gray
next_fresh_slot()   # был бы N-м, если бы серия пережила разрыв
assert t.template_gray is _tg_after_epoch or np.array_equal(
    t.template_gray, _tg_after_epoch), (
    "1 голос после разрыва не должен сработать — серия обязана была "
    "начаться с нуля")
print("    серия из %d голосов, разрыв эпохи, ещё 1 голос -> НЕ "
      "сработало (серия начата заново, не с %d)" % (N - 1, N - 1))

print("\n=== 5. reset_tracking() сбрасывает серию/cooldown/окно оверлея, "
      "но НЕ накопительный счётчик ===")
acquire()
for i in range(N):
    next_fresh_slot()
_count_before_reset = t._auto_tref_total_count
assert _count_before_reset >= 1
assert t._auto_tref_last_t is not None
force_reset()
assert t._auto_tref_confirm_streak == 0
assert t._auto_tref_last_t is None, "cooldown-таймер обязан сброситься на reset_tracking()"
assert t._auto_tref_overlay_until_t is None
assert t._auto_tref_total_count == _count_before_reset, (
    "накопительный счётчик — как frame_index, НЕ сбрасывается локальным "
    "reset_tracking()")
print("    confirm_streak/last_t/overlay_until сброшены, "
      "auto_template_refresh_count=%d сохранён" % t._auto_tref_total_count)

print("\n=== 6. AUTO_TEMPLATE_REFRESH_ENABLED=False: ни одного "
      "срабатывания, сколько бы хороших голосов ни было ===")
acquire()
t.AUTO_TEMPLATE_REFRESH_ENABLED = False
_tg_disabled = t.template_gray.copy()
_count_disabled = t._auto_tref_total_count
for i in range(3 * N):
    next_fresh_slot()
assert t._auto_tref_confirm_streak == 0, (
    "при ENABLED=False серия не должна даже накапливаться")
assert t._auto_tref_total_count == _count_disabled
assert np.array_equal(t.template_gray, _tg_disabled)
t.AUTO_TEMPLATE_REFRESH_ENABLED = True
print("    %d хороших голосов при ENABLED=False -> 0 срабатываний, "
      "streak не рос" % (3 * N))

print("\n=== 7. A/B: ENABLED=True против ENABLED=False на ОДНОЙ и той же "
      "последовательности кадров — box/geometry/template_base совпадают "
      "бит-в-бит, меняется только template ===")
# lock_cx/cy ДВИЖЕТСЯ каждый TRACKED-кадр обычной flow/match-фузией — это
# не относится к AUTO_TEMPLATE_REFRESH. Единственный чистый способ
# изолировать ИМЕННО его вклад — прогнать ОДНУ И ТУ ЖЕ детерминированную
# последовательность кадров дважды (ENABLED=True/False) и сравнить.
# Сцена заморожена (offset всегда 0, см. _off_frozen) — число tick() до
# нужного fresh-слота само может отличаться между прогонами (base/fresh
# чередование), офсет не должен от этого зависеть.
_off_frozen[0] = True


_orig_build_template = t.build_template
_build_tmpl_calls = [0]


def _counting_build_template(*a, **k):
    _build_tmpl_calls[0] += 1
    return _orig_build_template(*a, **k)


def _run_to_trigger_point(enabled):
    """geometry_epoch — счётчик за ВЕСЬ процесс, у него нет "нуля" в
    начале прогона (сама acquire() уже увеличивает его на фиксированное
    число событий acquisition). Сравнивать нужно ПРИРОСТ за прогон, не
    абсолютное значение — иначе тест сравнивал бы два разных прогона
    acquire(), а не вклад AUTO_TEMPLATE_REFRESH.

    Сцена статична (offset всегда 0) — на ней "свежий" build_template()
    даёт пиксель-в-пиксель тот же результат, что и исходный (нечего
    "устаревать"). Поэтому факт срабатывания проверяем ПРЯМО — счётчиком
    вызовов build_template(), а не сравнением содержимого template_gray."""
    t.AUTO_TEMPLATE_REFRESH_ENABLED = enabled
    acquire()
    _epoch_after_acquire = t.geometry_epoch
    _build_tmpl_calls[0] = 0
    for i in range(N - 1):
        next_fresh_slot()
    next_fresh_slot()   # N-е — точка, где ENABLED=True сработал бы
    return t.geometry_epoch - _epoch_after_acquire, _build_tmpl_calls[0]


t.build_template = _counting_build_template

_epoch_delta_off, _builds_off = _run_to_trigger_point(False)
_lock_cx_off, _lock_cy_off = t.lock_cx, t.lock_cy
_lock_w_off, _lock_h_off = t.lock_w, t.lock_h
_tpl_base_off = t.template_base.copy()
_tpl_scale_acc_off = t.template_scale_acc

_epoch_delta_on, _builds_on = _run_to_trigger_point(True)
assert t.lock_cx == _lock_cx_off and t.lock_cy == _lock_cy_off, (
    "lock_cx/cy обязаны совпасть бит-в-бит с ENABLED=False — на этой же "
    "последовательности кадров AUTO_TEMPLATE_REFRESH не должен был внести "
    "никакой вклад в позицию")
assert t.lock_w == _lock_w_off and t.lock_h == _lock_h_off, "lock_w/h разошлись между ENABLED=True/False"
assert _epoch_delta_on == _epoch_delta_off, (
    "AUTO_TEMPLATE_REFRESH обязан давать 0 ДОПОЛНИТЕЛЬНЫХ geometry_epoch "
    "приращений за прогон — получили дельту %d против %d у ENABLED=False"
    % (_epoch_delta_on, _epoch_delta_off))
assert _builds_on == _builds_off + 1, (
    "ENABLED=True обязан вызвать build_template() РОВНО на 1 раз больше, "
    "чем ENABLED=False (сам триггер) — получили %d против %d"
    % (_builds_on, _builds_off))
t.build_template = _orig_build_template
assert np.array_equal(t.template_base, _tpl_base_off), "template_base разошёлся между ENABLED=True/False"
assert t.template_scale_acc == _tpl_scale_acc_off, "template_scale_acc разошёлся между ENABLED=True/False"
assert t._adapt_frozen_posle_reanchor is True, (
    "после refresh адаптация обязана заморозиться до подтверждения "
    "свежим потоком — как после reanchor_tracker_at_current_box")
t.AUTO_TEMPLATE_REFRESH_ENABLED = True
_off_frozen[0] = False
print("    lock_cx/cy/w/h, geometry_epoch, template_base, "
      "template_scale_acc — без изменений; _adapt_frozen_posle_reanchor="
      "True (заморозка addWeighted-адаптации)")

print("\n=== 8. По исходному тексту: внутри AUTO TEMPLATE REFRESH меняются "
      "ТОЛЬКО разрешённые live-имена ===")
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i0 = src.index("# ===== AUTO TEMPLATE REFRESH =====")
i1 = src.index("# ===== /AUTO TEMPLATE REFRESH =====")
body = src[i0:i1]
_FORBIDDEN = ("lock_cx", "lock_cy", "lock_w", "lock_h", "geometry_epoch",
              "prev_pts", "prev_gray", "template_base", "template_scale_acc",
              "track_state", "last_match_score")
for name in _FORBIDDEN:
    for op in (" = ", " += ", " -= "):
        pattern = name + op
        idx = 0
        while True:
            idx = body.find(pattern, idx)
            if idx < 0:
                break
            before = body[idx - 1] if idx > 0 else " "
            if before.isalnum() or before == "_":
                idx += 1
                continue
            assert False, (
                "AUTO TEMPLATE REFRESH присваивает запрещённому имени %s"
                % name)
_REQUIRED_TO_APPEAR = ("template_gray = build_template",
                       'overlay_text = "TREF"',
                       "_adapt_frozen_posle_reanchor = True")
for frag in _REQUIRED_TO_APPEAR:
    assert frag in body, (
        "ожидали найти %r внутри AUTO TEMPLATE REFRESH — блок, похоже, "
        "переписан, тест нужно свериться с новым текстом" % frag)
print("    ни одно из %d запрещённых имён не присваивается; ожидаемые "
      "live-записи (template_gray/overlay_text/adapt_frozen) на месте"
      % len(_FORBIDDEN))

print("\n=== 9. Событие AUTO_TEMPLATE_REFRESH логируется с верными "
      "числами ===")
acquire()
_events = []
t.flight_log.event = _events.append
for i in range(N):
    next_fresh_slot()
_tref_events = [e for e in _events if e.startswith("AUTO_TEMPLATE_REFRESH")]
assert len(_tref_events) == 1, (
    "ожидали ровно 1 событие AUTO_TEMPLATE_REFRESH, получили %d: %s"
    % (len(_tref_events), _tref_events))
_ev = _tref_events[0]
assert ("old_psr=%.2f" % LIVE_PSR) in _ev, _ev
assert ("fresh_psr=%.2f" % GOOD_FRESH_PSR) in _ev, _ev
assert "old_size=" in _ev and "new_size=" in _ev, _ev
print("    событие: %s" % _ev)

print("\n=== 10. Оверлей 'TREF' держится AUTO_TEMPLATE_REFRESH_OVERLAY_S "
      "секунд, затем гаснет ===")
acquire()
for i in range(N):
    next_fresh_slot()
with t.state_lock:
    _ov_trigger = t.overlay_text
assert _ov_trigger == "TREF", "на кадре срабатывания ожидали TREF, получили %r" % _ov_trigger
tick()   # ещё кадр внутри окна (окно ощутимо больше одного FRAME_DT)
with t.state_lock:
    _ov_next = t.overlay_text
assert _ov_next == "TREF", (
    "окно оверлея ещё не истекло — ожидали TREF, получили %r" % _ov_next)
_clk.tick(t.AUTO_TEMPLATE_REFRESH_OVERLAY_S + 0.5)
tick()
with t.state_lock:
    _ov_after = t.overlay_text
assert _ov_after == "TRACKED", (
    "после истечения окна оверлей обязан вернуться к TRACKED, получили %r"
    % _ov_after)
print("    TREF на кадре срабатывания и следующем кадре внутри окна; "
      "после истечения — обратно TRACKED")

print("\n=== 11. PSR-margin и flow_gap-порог реально гейтят голос ===")
acquire()
# psr выше live, но margin не набран (ровно +0.5 вместо +margin=1.0).
t._shadow_match_against_template = fake_match(
    True, 0.9, LIVE_PSR + 0.5, 0.1, gap_px=0.0)
for i in range(N):
    next_fresh_slot()
assert t._auto_tref_confirm_streak == 0, (
    "psr выше live, но меньше required margin — голос обязан быть 'нет' "
    "на каждом из %d кадров" % N)
# margin набран, но flow_gap далеко за MAX_LOCK_STEP.
t._shadow_match_against_template = fake_match(
    True, 0.9, GOOD_FRESH_PSR, 0.1, gap_px=t.MAX_LOCK_STEP + 5.0)
for i in range(N):
    next_fresh_slot()
assert t._auto_tref_confirm_streak == 0, (
    "psr в порядке, но flow_gap > MAX_LOCK_STEP — голос обязан быть 'нет'")
print("    psr-margin недобор -> голосов нет; flow_gap > MAX_LOCK_STEP -> "
      "голосов нет, несмотря на хороший psr")

t._shadow_match_against_template = _orig_shadow_match
t.template_match_locked = _orig_live_match

print("\nOK: AUTO TEMPLATE REFRESH — серия из %d подтверждений (PSR-margin "
      "+ flow_gap-порог), cooldown между срабатываниями, честный сброс на "
      "geometry_epoch discontinuity и на reset_tracking (кроме "
      "накопительного счётчика), событие в лог, оверлей TREF на заданное "
      "окно — и на триггерящем кадре трогает ТОЛЬКО template_gray/tmpl_w/"
      "tmpl_h/template_std/_adapt_frozen_posle_reanchor/overlay_text, "
      "подтверждено и рантаймом, и по исходному тексту" % N)

t.FREEZE_TEMPLATE = _orig_freeze_template
t.SIZE_ADAPT_ENABLED = _orig_size_adapt
