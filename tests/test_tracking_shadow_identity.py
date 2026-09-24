"""Tracking Shadow v1: Template Identity — первый срез Tracking Shadow
(разбор качества трекинга по 14 заходам crash-полёта, 24.09.2026).

ПОВОД. Независимая проверка разбора (все числа сверены напрямую с
tracker.py и CSV, не с чужих слов) подтвердила: median PSR по 12 длинным
заходам 2.9, PSR<3 в 52% TRACKED-кадров, template/box mismatch>25% в 56%
кадров коррелирует с сильно худшим PSR/score (3.71/0.831 при mismatch<=25%
против 2.26/0.630 при >25%). А 4 независимых ручных reanchor (#13, #10 x2,
#14) воспроизведены кадр-в-кадр: свежий template на той же позиции чинит
score/PSR почти мгновенно (например #13: 0.290/0.76 -> 0.974/6.90 ровно на
следующем кадре после reanchor). TEMPLATE_RESCALE_ON_SIZE_CHANGE=False —
коробка растёт с целью, template остаётся старого масштаба.

ГЛАВНЫЙ ВОПРОС ПЕРВОГО СРЕЗА (сформулирован в разборе): когда live
template уже плох, существует ли УЖЕ В ЭТОМ ЖЕ КАДРЕ альтернативный
template (снятый заново на текущей позиции, либо template_base,
приведённый к текущему размеру), который отождествляет объект существенно
лучше — то есть было бы видно ДО того, как оператор вручную это исправит?

ЧТО ЭТО. Диагностика-only внутри process_locked_tracker. Не пишет
template_gray/tmpl_w/tmpl_h/template_std/lock_cx/lock_cy/lock_w/lock_h/
template_base/_match_dbg. "live" не пересчитывается — уже посчитанные этим
же кадром last_match_score/_match_dbg["psr"/"second"/"flow_gap"], уже в
CSV как match_score/match_psr/match_second/match_flow_gap. "fresh"/"base"
ищутся вокруг ТОЙ ЖЕ pred_cx/pred_cy, что и live match — иначе разница
объяснялась бы разным местом поиска, а не разным template. Намеренно БЕЗ
color/motion guard (см. коммент у _shadow_match_against_template) — по
разбору оба реально влияли в 0.00%/0.76% TRACKED-кадров.
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

print("\n=== 2. _shadow_match_against_template: ПРАВИЛЬНЫЙ (свежий, на "
      "месте объекта) template даёт заметно лучший score/PSR, чем "
      "template, снятый С ДРУГОГО (текстурного) места — синтетическая "
      "версия того, что 4 реальных reanchor показали на бортовых логах ===")
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
print("    fresh-на-объекте однозначно лучше template-с-фона — тот же "
      "эффект, что reanchor показал на #13/#10/#14")

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

print("\nOK: Tracking Shadow v1 (Template Identity) корректно измеряет "
      "разрыв fresh-vs-live на синтетике, не влияет на live-путь ни при "
      "штатной работе, ни при внутреннем сбое, и не пишет в "
      "live-переменные по исходному тексту")
