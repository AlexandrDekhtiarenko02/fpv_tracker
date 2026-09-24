"""Tracking Shadow: Template Identity — первый срез Tracking Shadow
(разбор качества трекинга по 14 заходам crash-полёта, 24.09.2026).
Переезд после live-этапов + rate-limit — ревью по 5cca02c. TEMPORAL
fresh (эта версия) — ревью, нашедшее SELF-MATCH BIAS в 5cca02c.

ПОВОД (исходный, всё ещё в силе). Median PSR по 12 длинным заходам 2.9,
PSR<3 в 52% TRACKED-кадров, template/box mismatch>25% в 56% кадров
коррелирует с сильно худшим PSR/score. 4 независимых ручных reanchor
(#13, #10 x2, #14) воспроизведены кадр-в-кадр: свежий template на той же
позиции чинит score/PSR почти мгновенно. TEMPLATE_RESCALE_ON_SIZE_
CHANGE=False — коробка растёт с целью, template остаётся старого
масштаба.

SELF-MATCH BIAS (найдено ревью по 5cca02c, подтверждено бенчем на Pi Zero
2W). Первая версия "fresh" резала template ИЗ текущего кадра и ТУТ ЖЕ
искала его В ТОМ ЖЕ кадре — search-окно вокруг pred_cx/pred_cy физически
содержит те же пиксели, из которых template секунду назад вырезан. На
бенче fresh_score был 0.986-1.000 у 99.9-100% TRACKED-кадров — не сигнал
качества template, а гарантированный исход самой постановки опыта.
base_score тем временем показывал реальную дисперсию (p50=0.89,
min=0.058) — настоящий межкадровый тест, template_base снят на
acquisition, задолго до текущего кадра.

ИСПРАВЛЕНО: fresh стал TEMPORAL. candidate снимается на ОДНОМ shadow-
слоте (дёшево — один crop, БЕЗ matchTemplate) и оценивается ТОЛЬКО на
БУДУЩЕМ слоте, против кадра, которого на момент захвата candidate ещё не
существовало — реальная проверка "переживёт ли новый template время", а
не "совпадает ли кусок кадра сам с собой". Candidate сбрасывается на
geometry_epoch discontinuity (_reset_geometry_history — та же точка, что
уже рвёт историю для остальных temporal-shadow полей) и на полном сбросе.

APPLES-TO-APPLES (второй пункт того же ревью). Shadow теперь выполняется
после блока примерки масштаба, которая МОЖЕТ поменять lock_w/lock_h в
этом же кадре — уже ПОСЛЕ того, как live match_score/match_psr были
посчитаны на СТАРОМ размере. shadow_track_geom_box_w/h снимает lock_w/h
РАНЬШЕ (сразу после live match, до примерки), чтобы shadow сравнивался с
той же геометрией, что видел live этого кадра.

ЧТО ЭТО. Диагностика-only внутри process_locked_tracker, СТРОГО после
update_control_from_target(). Не пишет template_gray/tmpl_w/tmpl_h/
template_std/lock_cx/lock_cy/lock_w/lock_h/template_base/_match_dbg.
"live" не пересчитывается — уже посчитанные этим же кадром
last_match_score/_match_dbg["psr"/"second"/"flow_gap"], уже в CSV как
match_score/match_psr/match_second/match_flow_gap. Намеренно БЕЗ color/
motion guard (см. коммент у _shadow_match_against_template) — по разбору
оба реально влияли в 0.00%/0.76% TRACKED-кадров.
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


def make_scene(cx, cy, size, offset=0, seed=7):
    """Текстурная сцена: один объект size x size px с реальной фактурой
    (не гладкое пятно — иначе PSR вырожден: score_map плоский, side.std()~0)."""
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    rng = np.random.default_rng(seed)
    obj = (rng.random((size, size)) * 90 + 110).astype(np.uint8)
    cv2.circle(obj, (size // 2, size // 2), max(2, size // 3), 40, -1)
    x0, y0 = cx + offset - size // 2, cy - size // 2
    frame[y0:y0 + size, x0:x0 + size] = obj
    return frame


CX, CY = t.LORES_W // 2, t.LORES_H // 2


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()
    t.reset_tracking(to_acq=False)


print("=== 1. _shadow_build_fresh_template: та же формула размера, что "
      "build_template(), но НЕ пишет tmpl_w/tmpl_h/template_std ===")
scene = make_scene(CX, CY, 40)
t.tmpl_w, t.tmpl_h, t.template_std = -1, -1, -1.0   # сентинелы
_tmpl, tw, th, tstd = t._shadow_build_fresh_template(scene, CX, CY, 20.0, 20.0)
assert t.tmpl_w == -1 and t.tmpl_h == -1 and t.template_std == -1.0, (
    "_shadow_build_fresh_template не имеет права трогать live tmpl_w/"
    "tmpl_h/template_std — они нужны live template_match_locked на "
    "СЛЕДУЮЩЕМ кадре")
_expected_tw = t.clamp(max(20.0 * t.TEMPLATE_SCALE, t.TEMPLATE_MIN), t.TEMPLATE_MIN, t.TEMPLATE_MAX)
assert abs(tw - _expected_tw) < 1e-6, "размер должен совпасть с формулой build_template()"
print("    tw=%.1f (formula=%.1f), live tmpl_w/tmpl_h/template_std не тронуты" % (tw, _expected_tw))

print("\n=== 2. _shadow_match_against_template: низкоуровневая проверка "
      "самого matchTemplate-примитива — template НА объекте отождествляет "
      "его лучше, чем template С ФОНА рядом. НЕ демонстрация механизма "
      "'fresh' целиком (тот теперь TEMPORAL, см. секцию 11+) — это ниже "
      "ровно тот self-match паттерн (крои и тут же ищи в том же кадре),\n"
      "    который сам по себе и был найденной ревью проблемой; здесь он "
      "оправдан — проверяем не 'фиксит ли fresh reanchor', а что "
      "matchTemplate вообще умеет отличать объект от фона ===")
OBJ_SIZE = 40
scene = make_scene(CX, CY, OBJ_SIZE)
good_tmpl, gw, gh, gstd = t._shadow_build_fresh_template(scene, CX, CY, OBJ_SIZE, OBJ_SIZE)
# "Устаревший" template — снят на смещённом месте (фон + только край
# объекта), той же формы, что стареющий template в реальном drift-сценарии
# (не текстура объекта целиком, а случайный сосед).
bad_tmpl, bw, bh, bstd = t._shadow_build_fresh_template(
    scene, CX + OBJ_SIZE, CY + OBJ_SIZE, OBJ_SIZE, OBJ_SIZE)  # чистый фон 120

ok_good, score_good, psr_good, second_good, _, _ = t._shadow_match_against_template(
    scene, good_tmpl, gw, gh, gstd, CX, CY, 0.0)
ok_bad, score_bad, psr_bad, second_bad, _, _ = t._shadow_match_against_template(
    scene, bad_tmpl, bw, bh, bstd, CX, CY, 0.0)
assert ok_good, "матч правильного template обязан пройти"
print("    good: score=%.3f psr=%.2f | bad(фон): score=%.3f psr=%.2f"
      % (score_good, psr_good, score_bad if ok_bad else float("nan"),
         psr_bad if ok_bad else float("nan")))
assert score_good > 0.9, "template с самого объекта обязан дать высокий score"
if ok_bad:
    assert score_good > score_bad and psr_good > psr_bad, (
        "template объекта обязан отождествлять объект лучше, чем template "
        "с фона — иначе сравнение само по себе не имеет смысла")
print("    matchTemplate-примитив корректно отличает объект от фона — "
      "необходимое, но не достаточное условие для temporal fresh (см. "
      "секцию 11+, где это же используется ПРАВИЛЬНО — между слотами)")

print("\n=== 3. Изоляция: сломанный _shadow_match_against_template не "
      "меняет live-путь (template_gray/tmpl_w/tmpl_h/template_std/"
      "lock_cx/lock_cy/track_state/last_match_score) ===")


def run_sequence():
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
    out = []
    for i in range(1, 8):
        with t.state_lock:
            t.app_state["fc_pitch_deg"] = 10.0
            t.app_state["fc_pitch_ts"] = t.time.monotonic()
            t.app_state["gyro"] = (0, 0, 0)
            t.app_state["imu_ts"] = t.time.monotonic()
        _clk.tick(FRAME_DT)
        t.process_locked_tracker(make_scene(CX, CY, 30, offset=i))
        tg = t.template_gray
        out.append((
            t.track_state, t.lock_cx, t.lock_cy, t.tmpl_w, t.tmpl_h,
            t.template_std, t.last_match_score,
            (tg.shape, tuple(tg.flatten()[:20])) if tg is not None else None,
        ))
    return out


_clk.t = 1000.0
results_normal = run_sequence()
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся в сценарии теста"

_orig_match = t._shadow_match_against_template


def _boom(*a, **k):
    raise RuntimeError("Tracking Shadow намеренно сломан тестом изоляции")


t._shadow_match_against_template = _boom
_clk.t = 1000.0
results_broken = run_sequence()
t._shadow_match_against_template = _orig_match

assert len(results_normal) == len(results_broken) == 7
for i, (a, b) in enumerate(zip(results_normal, results_broken)):
    assert a == b, (
        "кадр %d: live-состояние разошлось между нормальным и сломанным "
        "Tracking Shadow — нарушение изоляции!\n  нормальный: %s\n  "
        "сломанный:  %s" % (i, a, b))
print("    все 7 кадров совпали побитово (track_state, lock_cx/cy, "
      "tmpl_w/h, template_std, last_match_score, содержимое template_gray)")
assert t._shadow_track_dbg.get("active") is False, (
    "после исключения внутри Tracking Shadow _shadow_track_dbg должен "
    "стать {'active': False}, а не тихо оставить прошлые значения")
print("    _shadow_track_dbg={'active': False} после сбоя — честно")

print("\n=== 4. reset_tracking() и переход в LOST/HOLD сбрасывают "
      "_shadow_track_dbg в {'active': False} ===")
force_reset()
assert t._shadow_track_dbg.get("active") is False, (
    "reset_tracking() обязан сбросить _shadow_track_dbg")
print("    после reset_tracking(): active=False")

print("\n=== 5. По исходному тексту: Tracking Shadow не пишет ни в одну "
      "live-переменную ===")
import io  # noqa: E402
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_start = src.index("# ============= TRACKING SHADOW: TEMPLATE IDENTITY =============")
i_end = src.index("# ============ /TRACKING SHADOW: TEMPLATE IDENTITY ============")
shadow_body = src[i_start:i_end]
_LIVE_NAMES = ("template_gray", "tmpl_w", "tmpl_h", "template_std",
              "lock_cx", "lock_cy", "lock_w", "lock_h", "template_base",
              "track_state", "last_match_score")
for name in _LIVE_NAMES:
    for op in (" = ", " += ", " -= "):
        pattern = name + op
        idx = 0
        while True:
            idx = shadow_body.find(pattern, idx)
            if idx < 0:
                break
            before = shadow_body[idx - 1] if idx > 0 else " "
            if before.isalnum() or before == "_":
                idx += 1
                continue
            assert False, (
                "Tracking Shadow-блок присваивает live-переменной %s — "
                "нарушение изоляции" % name)
print("    ни одно из %d live-имён не присваивается внутри Tracking "
      "Shadow-блока в process_locked_tracker" % len(_LIVE_NAMES))
# И сами хелперы — тоже проверим, что они не global-ят ничего живого.
for fn_name in ("_shadow_build_fresh_template", "_shadow_match_against_template"):
    i_fn = src.index("def %s(" % fn_name)
    i_fn_end = src.index("\n\n\n", i_fn)
    fn_body = src[i_fn:i_fn_end]
    assert "global " not in fn_body, (
        "%s не имеет права объявлять global — это должна быть чистая "
        "функция" % fn_name)
print("    _shadow_build_fresh_template/_shadow_match_against_template "
      "не объявляют global — чистые функции")

print("\n=== 6. TRACKING_SHADOW_ENABLED=False: active=False, ноль "
      "вызовов matchTemplate (блок не считает вообще) ===")
_orig_enabled = t.TRACKING_SHADOW_ENABLED
_orig_every_n = t.TRACKING_SHADOW_EVERY_N_FRAMES
t.TRACKING_SHADOW_ENABLED = False
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


def _tick_frame(offset, cb_t0=None):
    with t.state_lock:
        t.app_state["fc_pitch_deg"] = 10.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(make_scene(CX, CY, 30, offset=offset), cb_t0=cb_t0)


_call_count = [0]
_orig_match_2 = t._shadow_match_against_template


def _counting(*a, **k):
    _call_count[0] += 1
    return _orig_match_2(*a, **k)


t._shadow_match_against_template = _counting
_tick_frame(1)
t._shadow_match_against_template = _orig_match_2
assert t._shadow_track_dbg.get("active") is False
assert _call_count[0] == 0, (
    "TRACKING_SHADOW_ENABLED=False обязан пропустить весь блок — 0 "
    "вызовов _shadow_match_against_template, было %d" % _call_count[0])
print("    ENABLED=False: active=False, 0 вызовов matchTemplate")

print("\n=== 7. Редкий слот: TRACKING_SHADOW_EVERY_N_FRAMES=3 — активен "
      "только на frame_index%3==0, остальные кадры active=False БЕЗ "
      "единого вызова matchTemplate (не every-frame — нашли ревью бенча: "
      "even на 24px every-frame BOTH вытеснял ms_primerka с 44 замеров "
      "до 0) ===")
t.TRACKING_SHADOW_ENABLED = True
t.TRACKING_SHADOW_EVERY_N_FRAMES = 3
_slot_results = []
for i in range(9):
    _call_count[0] = 0
    t._shadow_match_against_template = _counting
    _tick_frame(10 + i)
    t._shadow_match_against_template = _orig_match_2
    is_slot = (t.frame_index % t.TRACKING_SHADOW_EVERY_N_FRAMES == 0)
    _slot_results.append((t.frame_index, is_slot, t._shadow_track_dbg.get("active"), _call_count[0]))
for fi, is_slot, active, calls in _slot_results:
    if is_slot:
        assert calls >= 1, "frame_index=%d — слот, но 0 вызовов matchTemplate" % fi
    else:
        assert calls == 0, (
            "frame_index=%d — НЕ слот (не кратен %d), но был вызов "
            "matchTemplate — рано, диагностика должна молчать вне своего "
            "слота" % (fi, t.TRACKING_SHADOW_EVERY_N_FRAMES))
        assert active is False, "не-слот кадр обязан быть active=False"
n_slots = sum(1 for _, s, _, _ in _slot_results if s)
n_active = sum(1 for _, _, a, _ in _slot_results if a)
print("    9 кадров, %d слотов (every %d-й), %d активных — вне слота "
      "вызовов matchTemplate не было ни разу" % (n_slots, t.TRACKING_SHADOW_EVERY_N_FRAMES, n_active))

print("\n=== 8. Чередование fresh/base по слотам — никогда оба в одном "
      "кадре (вдвое дешевле активного слота, чем в первой версии) ===")
_variants = []
for i in range(9, 15):
    _tick_frame(20 + i)
    if t._shadow_track_dbg.get("active"):
        _variants.append(t._shadow_track_dbg.get("variant"))
assert len(_variants) >= 2, "нужно хотя бы 2 активных слота для проверки чередования"
assert set(_variants) <= {"fresh", "base"}
# Чередование: соседние активные слоты не должны быть одним и тем же
# variant подряд ВСЕ разы (иначе это не чередование, а константа).
assert len(set(_variants)) > 1 or len(_variants) < 2, (
    "ожидали чередование fresh/base по слотам, а не один и тот же "
    "variant всё время: %s" % _variants)
print("    variant по активным слотам: %s (чередуется, не одна константа)"
      % _variants)

print("\n=== 9. Budget-gate: если время кадра уже вышло за FRAME_BUDGET_MS "
      "к моменту слота — active=False, skip_reason='budget', НИ ОДНОГО "
      "вызова matchTemplate (проверка бюджета ДО, не ПОСЛЕ вычисления) ===")
# Найти следующий slot-кадр и подать туда заведомо просроченный cb_t0.
_next_slot_offset = 1
while (t.frame_index + _next_slot_offset) % t.TRACKING_SHADOW_EVERY_N_FRAMES != 0:
    _next_slot_offset += 1
for _ in range(_next_slot_offset - 1):
    _tick_frame(40)
_call_count[0] = 0
t._shadow_match_against_template = _counting
_late_cb_t0 = _clk.t - (t.FRAME_BUDGET_MS / 1000.0 + 0.5)  # "начался" давно
_tick_frame(41, cb_t0=_late_cb_t0)
t._shadow_match_against_template = _orig_match_2
assert t.frame_index % t.TRACKING_SHADOW_EVERY_N_FRAMES == 0, "тест обязан был попасть ровно в слот"
sc_budget = t._shadow_track_dbg
assert sc_budget.get("active") is False
assert sc_budget.get("skip_reason") == "budget", (
    "слот был, бюджет исчерпан — обязан быть skip_reason='budget', "
    "получили %r" % sc_budget.get("skip_reason"))
assert _call_count[0] == 0, (
    "budget-gate обязан проверяться ДО вычисления — 0 вызовов "
    "matchTemplate на просроченном кадре, было %d" % _call_count[0])
print("    просроченный cb_t0 на слот-кадре -> active=False, "
      "skip_reason='budget', 0 вызовов matchTemplate")

print("\n=== 10. Расположение: Tracking Shadow стоит ПОСЛЕ update_control_"
      "from_target(), а НЕ до блока адаптации/примерки — не может "
      "вытеснить бюджетом ни один live-этап ===")
src2 = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_utft = src2.rindex("update_control_from_target()",
                     0, src2.index("# ============= TRACKING SHADOW: TEMPLATE IDENTITY ============="))
i_shadow2 = src2.index("# ============= TRACKING SHADOW: TEMPLATE IDENTITY =============")
i_primerka = src2.index('elif SIZE_ADAPT_ENABLED and _primerka_pora and _budget_ok:')
assert i_utft < i_shadow2, (
    "Tracking Shadow обязан идти ПОСЛЕ update_control_from_target()")
assert i_primerka < i_shadow2, (
    "Tracking Shadow обязан идти ПОСЛЕ блока примерки масштаба (scale "
    "adaptation), не до него — иначе он снова вытесняет её бюджетом, "
    "именно эта регрессия и была найдена бенчем на Pi Zero 2W")
print("    Tracking Shadow физически после update_control_from_target() "
      "и после блока примерки масштаба — не может их вытеснить")

t.TRACKING_SHADOW_ENABLED = _orig_enabled
t.TRACKING_SHADOW_EVERY_N_FRAMES = _orig_every_n

print("\n=== 11. TEMPORAL fresh: candidate, оценённый на fresh-слоте, "
      "был захвачен на РАННЕМ слоте — candidate_age_ms доказывает "
      "реальный временной разрыв, не тот же кадр (это и был self-match "
      "bias в 5cca02c: crop и match в одном вызове дают age=0 всегда) ===")
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
assert t.track_state == t.TRACK_STATE_TRACKED

t.TRACKING_SHADOW_ENABLED = True
t.TRACKING_SHADOW_EVERY_N_FRAMES = 4
# frame_index — сквозной модульный счётчик, не сбрасывается ни force_reset,
# ни reset_tracking — к этой точке он уже унаследовал чётность от секций
# 1-10. Поэтому НЕ предполагаем, какой слот "первый" и какой у него
# variant — активно ждём нужный, благо variant строго чередуется между
# соседними активными слотами (_slot = frame_index // N, variant = slot%2).
_off = [0]


def _next_active_slot(max_frames=64):
    for _ in range(max_frames):
        _off[0] = (_off[0] % 20) + 1
        _tick_frame(_off[0])
        v = t._shadow_track_dbg.get("variant")
        if v is not None:
            return v
    raise AssertionError("не дождались активного слота за %d кадров" % max_frames)


def _next_active_slot_with_variant(want, max_attempts=8):
    # По строгому чередованию нужный variant выпадает не позже 2-й
    # попытки; запас до 8 — просто щедрая подстраховка.
    for _ in range(max_attempts):
        v = _next_active_slot()
        if v == want:
            return v
    raise AssertionError("не дождались variant=%r за %d активных слотов" % (want, max_attempts))


v1 = _next_active_slot_with_variant("base")
assert t._shadow_fresh_candidate is not None, (
    "base-слот тоже обязан захватить fresh-candidate для будущего "
    "fresh-слота (см. докстринг: 'каждый активный слот' захватывает)")

# По строгому чередованию следующий активный слот ГАРАНТИРОВАННО "fresh"
# — ровно через TRACKING_SHADOW_EVERY_N_FRAMES кадров от только что
# виденного base.
v2 = _next_active_slot()
assert v2 == "fresh", (
    "чередование нарушено: сразу после 'base' ожидали 'fresh', получили %r" % v2)
sc_slot2 = t._shadow_track_dbg
assert sc_slot2.get("active") is True, (
    "candidate от только что виденного base-слота обязан быть валиден — "
    "active=True, не 'no_candidate'")
_age = sc_slot2.get("candidate_age_ms")
_expected_age = t.TRACKING_SHADOW_EVERY_N_FRAMES * FRAME_DT * 1000.0
assert _age is not None and _age > 0.0, (
    "candidate_age_ms обязан быть положительным — доказательство, что "
    "candidate снят РАНЬШЕ, не в этом же вызове (self-match всегда дал "
    "бы age=0, потому что crop и match происходили бы в одной функции)")
assert abs(_age - _expected_age) < FRAME_DT * 1000.0 * 1.5, (
    "candidate_age_ms=%.1f ожидали около %.1fмс (%d кадров * %.1fмс) — "
    "слишком далеко от ожидаемого временного разрыва"
    % (_age, _expected_age, t.TRACKING_SHADOW_EVERY_N_FRAMES, FRAME_DT * 1000.0))
print("    base-слот (захватил candidate) -> fresh-слот active=True "
      "candidate_age_ms=%.1f (~%.1f ожидали) — candidate реально пережил "
      "%d кадров между захватом и оценкой, это не self-match"
      % (_age, _expected_age, t.TRACKING_SHADOW_EVERY_N_FRAMES))

print("\n=== 12. Candidate инвалидируется на geometry_epoch discontinuity: "
      "fresh-слот сразу после _reset_geometry_history() обязан дать "
      "skip_reason='no_candidate', а не тихо оценить candidate из ДРУГОЙ "
      "(уже недействительной) эпохи ===")
# Снова дождаться "base" (он гарантированно свежо захватит candidate),
# затем СРАЗУ разорвать эпоху — по чередованию следующий слот будет
# "fresh" и обязан НЕ увидеть только что уничтоженный candidate.
_next_active_slot_with_variant("base")
assert t._shadow_fresh_candidate is not None
t._reset_geometry_history("test_epoch_break")
assert t._shadow_fresh_candidate is None, (
    "_reset_geometry_history() обязан сбросить _shadow_fresh_candidate "
    "целиком — не только epoch-метку")

v3 = _next_active_slot()
assert v3 == "fresh", (
    "чередование нарушено: сразу после 'base' ожидали 'fresh', получили %r" % v3)
sc_slot3 = t._shadow_track_dbg
assert sc_slot3.get("active") is False
assert sc_slot3.get("skip_reason") == "no_candidate", (
    "candidate из старой эпохи не должен был просочиться в оценку — "
    "ожидали skip_reason='no_candidate', получили %r" % sc_slot3.get("skip_reason"))
print("    base (candidate захвачен) -> epoch break -> fresh: active=False "
      "skip_reason='no_candidate' — candidate старой эпохи не использован")

print("\n=== 13. geom_box_w/h снимается ДО блока примерки масштаба "
      "(apples-to-apples, ревью по 5cca02c п.2) — по расположению в "
      "исходном тексте, не только по докстрингу ===")
src3 = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
i_geom_snap = src3.index("_shadow_geom_w = lock_w")
i_primerka2 = src3.index(
    'elif SIZE_ADAPT_ENABLED and _primerka_pora and _budget_ok:')
assert i_geom_snap < i_primerka2, (
    "снимок _shadow_geom_w/h обязан идти ДО блока примерки масштаба — "
    "иначе он снимет уже изменённый этим же кадром lock_w/h, а не тот, "
    "с которым был посчитан live match_score этого кадра")
print("    _shadow_geom_w/h снимается до блока примерки — apples-to-apples "
      "с live match_score/match_psr этой же строки")

t.TRACKING_SHADOW_ENABLED = _orig_enabled
t.TRACKING_SHADOW_EVERY_N_FRAMES = _orig_every_n

print("\nOK: Tracking Shadow (после бенча 24.09.2026 + ревью self-match "
      "bias) — редкий budget-gated слот СТРОГО ПОСЛЕ всей live "
      "tracking-логики, fresh стал TEMPORAL (candidate переживает реальное "
      "время между слотами, не self-match), candidate корректно "
      "инвалидируется на geometry_epoch discontinuity, geom_box_w/h "
      "снимается до примерки для честного сравнения с live — не влияет "
      "на live-путь ни при штатной работе, ни при внутреннем сбое, и не "
      "пишет в live-переменные по исходному тексту")
