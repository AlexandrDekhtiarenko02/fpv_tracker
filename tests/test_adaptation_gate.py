"""Запрет адаптации на сомнительных кадрах (ТЗ next-commit spec §9).

Высокий score сам по себе не значит «кадр надёжен» — он может быть
одновременно у нескольких кандидатов (низкий PSR/lead). Шаблон не должен
на таком кадре учиться — иначе он рискует медленно съехать к конкуренту,
даже когда позиция ещё держится потоком.

НЕОДНОЗНАЧНОСТЬ — ЧЕРЕЗ ДЕТЕРМИНИРОВАННЫЙ ФЕЙК template_match_locked, А НЕ
ЧЕРЕЗ ОРГАНИЧЕСКУЮ CV-СЦЕНУ (пересмотрено после сужения ACQ_SNAP_RADIUS_MAIN
60->36, разбор оператора о selection/sensitivity). Раньше секция A клала
периодические полосы прямо под прицел и полагалась на то, что matchTemplate
сам найдёт равного конкурента через период — специально откалиброванная
амплитуда (110..130) и период. Более узкая зона захвата изменила саму
геометрию настолько, что прежняя калибровка перестала держаться (проверено
прямой инъекцией состояния, минуя acquisition вовсе: тот же period=7 на
сколь угодно большом шаблоне давал lead~0.12-0.40, а не <MATCH_LEAD_FULL=
0.1 — упёрлось в структурный предел самой схемы, не в подбор чисел).
Гоняться за новой калибровкой синтетической периодики — точно тот
"подбор порога под один тест", которого явно просят избегать. Вместо этого:
захват — ВСЕГДА на обычной (гарантированно однозначной) сцене, а
неоднозначность матча на кадрах СЛЕЖЕНИЯ задаётся напрямую через
_match_dbg["second"] — тот же единственный сигнал, которым
_template_adaptation_gate реально пользуется (MATCH_LEAD_FULL), без
зависимости от CV-нюансов конкретной синтетической текстуры.

Два сценария, оба с независимым шумом по кадрам (без него addWeighted было
бы неотличимо от «не изменилось вовсе» — цель сама по себе не движется):
  A. Каждый TRACKED-кадр — гарантированный конкурент (lead<MATCH_LEAD_FULL).
     Шаблон обязан остаться БИТ-В-БИТ неизменным все кадры подряд.
  B. Обычный однозначный лок. Шаблон ОБЯЗАН меняться — иначе гейт просто
     блокирует адаптацию всегда, а не по делу.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()
t.MOTION_GUARD_ENABLED = False
t.color_active = False
# НАЙДЕНО (ревью по 3acbd77): секция D доказывает корректность std ПОСЛЕ
# addWeighted конкретно, но Auto Template Refresh (по умолчанию ENABLED)
# и примерка масштаба (SIZE_ADAPT_ENABLED) тоже умеют независимо
# переписать template_gray за эти же 29 кадров — эвристика "adaptation_
# allowed==1 + та же форма + содержимое изменилось" их не отличает от
# настоящего addWeighted. Выключаем оба явно, чтобы секция D проверяла
# ровно то, что заявлено, а не "какой-то из нескольких механизмов".
t.AUTO_TEMPLATE_REFRESH_ENABLED = False
t.SIZE_ADAPT_ENABLED = False
# IDENTITY_UNCERTAIN (отдельный, независимый механизм) переиспользует ТЕ ЖЕ
# признаки (ambiguous/flow_gap), которыми управляет этот файл. Секция A
# нарочно держит кадр неоднозначным ВСЕ 15 кадров подряд — без изоляции это
# через IDENTITY_UNCERTAIN_CONFIRM_FRAMES=6 увело бы track_state в
# IDENTITY_UNCERTAIN на середине прогона, что этот файл не проверяет и не
# должен проверять (см. test_identity_uncertain.py — там же отдельно).
t.IDENTITY_UNCERTAIN_ENABLED = False

CX, CY = t.LORES_W // 2, t.LORES_H // 2
R = 12  # внутри узкой зоны захвата — надёжный однозначный пик


def make_scene(frame_seed):
    """Обычный шум, один уверенный пик — ГАРАНТИРОВАННО однозначная сцена
    (та же роль, что раньше играла ветка periodic=False). Неоднозначность
    для секций A/C задаётся ОТДЕЛЬНО, фейком template_match_locked, не
    сценой (см. докстроку файла)."""
    rng = np.random.default_rng(1000 + frame_seed)
    frame = (rng.random((t.LORES_H, t.LORES_W)) * 70 + 50).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    y0, y1 = CY - R, CY + R
    x0, x1 = CX - R, CX + R
    patch = (rng.random((y1 - y0, x1 - x0)) * 120 + 60).astype(np.uint8)
    cv2.circle(patch, (R, R), R // 3, 40, -1)
    cv2.line(patch, (0, 2 * R - 1), (2 * R - 1, 0), 20, max(2, R // 8))
    noise = rng.integers(-6, 7, patch.shape)
    patch = np.clip(patch.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    frame[y0:y1, x0:x1] = patch
    return frame


_real_template_match_locked = t.template_match_locked


def fake_ambiguous_match(score=0.90, lead_gap=0.02):
    """ДЕТЕРМИНИРОВАННАЯ неоднозначность: _match_dbg["second"] выставлен
    так, что lead=lead_gap<MATCH_LEAD_FULL на КАЖДОМ кадре — ровно
    единственный сигнал, которым _template_adaptation_gate реально
    пользуется для решения "ambiguous_peak" (см. её код в tracker.py).
    match_cx/cy = predicted (dist_fm=0) — секция проверяет ИМЕННО lead-
    путь гейта, не flow_gap (тот отдельно проверен в test_identity_
    uncertain.py)."""
    def _f(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
        t._match_dbg["second"] = score * (1.0 - lead_gap)
        return True, pred_cx, pred_cy, score
    return _f


def run_scenario(ambiguous, n_frames=15):
    t.reset_tracking(to_acq=True)
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    # Захват — ВСЕГДА на обычной, гарантированно однозначной сцене:
    # неоднозначность (если нужна) вступает в силу ТОЛЬКО на кадрах
    # слежения, ниже.
    scene0 = make_scene(0)
    t.process_locked_tracker(scene0)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"
    tmpl0 = t.template_gray.copy()
    if ambiguous:
        t.template_match_locked = fake_ambiguous_match()
    changed_any = False
    allowed_seen = []
    for i in range(1, n_frames + 1):
        scene = make_scene(i)
        t.process_locked_tracker(scene)
        allowed_seen.append(t._match_dbg.get("template_adaptation_allowed"))
        if (t.track_state == t.TRACK_STATE_TRACKED
                and t.template_gray.shape == tmpl0.shape
                and not np.array_equal(t.template_gray, tmpl0)):
            changed_any = True
    t.template_match_locked = _real_template_match_locked
    return changed_any, allowed_seen, t._match_dbg.get("adapt_skip_reason")


print("=== A. Гарантированный конкурент каждый кадр — шаблон не учится ===")
changed, allowed, reason = run_scenario(ambiguous=True)
print("    template_adaptation_allowed по кадрам:", allowed)
print("    template_gray менялся:", changed, " причина запрета:", reason)
assert not any(allowed), (
    "конкурент через lead<MATCH_LEAD_FULL на каждом кадре, а гейт хоть раз "
    "разрешил адаптацию — ambiguity guard не подключён к запрету обучения "
    "шаблона")
assert not changed, (
    "template_gray изменился, хотя гейт держал adaptation_allowed=0 весь "
    "прогон — запрет не долистался до реального вызова addWeighted")
assert reason == "ambiguous_peak", (
    "причина запрета должна называться ambiguous_peak, получено %r" % reason)

print("\n=== B. Обычная текстура — однозначный лок, адаптация должна идти ===")
changed2, allowed2, _ = run_scenario(ambiguous=False)
print("    template_adaptation_allowed по кадрам:", allowed2)
print("    template_gray менялся:", changed2)
assert any(allowed2), (
    "без искусственной неоднозначности гейт всё равно держит adaptation_"
    "allowed=0 — либо порог MATCH_LEAD_FULL проверяется неверно, либо gate "
    "закрыт навсегда")
assert changed2, (
    "template_gray не изменился ни разу за несколько кадров при "
    "разрешённой адаптации — либо шум кадра недостаточен, либо addWeighted "
    "не вызывается вовсе")

print("\n=== C. НАЙДЕНО (ревью по 9da5f65): build_template() пишет "
      "tmpl_w/tmpl_h/template_std КАК ПОБОЧНЫЙ ЭФФЕКТ безусловно — даже "
      "когда cur_tmpl ниже выброшен гейтом. tmpl_w/tmpl_h/template_std "
      "обязаны всё это время описывать РЕАЛЬНЫЙ template_gray, не "
      "выброшенный cur_tmpl ===")
t.reset_tracking(to_acq=True)
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
scene0 = make_scene(0)
t.process_locked_tracker(scene0)
assert t.track_state == t.TRACK_STATE_TRACKED
t.template_match_locked = fake_ambiguous_match()
_mismatches = []
for i in range(1, 16):
    scene = make_scene(i)
    t.process_locked_tracker(scene)
    if t.track_state != t.TRACK_STATE_TRACKED:
        continue
    assert t._match_dbg.get("template_adaptation_allowed") == 0, (
        "сценарий C предполагает гейт-отказ каждый кадр — иначе эта "
        "проверка ничего не показывает, allowed=%r reason=%r"
        % (t._match_dbg.get("template_adaptation_allowed"),
           t._match_dbg.get("adapt_skip_reason")))
    # lead < 0.15 больше не двигает рамку на полный шаг потока. На шумной
    # сцене этого теста LK из-за этого один кадр может потерять пару
    # (reason=no_flow). Отказ всё равно отказ: build_template уже вызван,
    # метаданные обязаны описывать живой шаблон, а не выброшенный.
    assert t._match_dbg.get("adapt_skip_reason") in (
        "ambiguous_peak", "no_flow"), (
        "сценарий C предполагает отказ гейта (ambiguous_peak или no_flow), "
        "получено %r" % t._match_dbg.get("adapt_skip_reason"))
    _real_w, _real_h = t.template_gray.shape[1], t.template_gray.shape[0]
    _real_std = float(np.std(t.template_gray))
    if (t.tmpl_w, t.tmpl_h) != (_real_w, _real_h):
        _mismatches.append("frame %d: tmpl_w/h=(%s,%s) != реальный "
                           "template_gray.shape=(%s,%s)"
                           % (i, t.tmpl_w, t.tmpl_h, _real_w, _real_h))
    if abs(t.template_std - _real_std) > 1e-6:
        _mismatches.append("frame %d: template_std=%.4f != реальный "
                           "np.std(template_gray)=%.4f"
                           % (i, t.template_std, _real_std))
t.template_match_locked = _real_template_match_locked
assert not _mismatches, (
    "tmpl_w/tmpl_h/template_std разошлись с реальным template_gray после "
    "того, как cur_tmpl был отвергнут гейтом:\n  " + "\n  ".join(_mismatches))
print("    %d кадров подряд с гейт-отказом — tmpl_w/tmpl_h/template_std "
      "всё время совпадали с реальным template_gray" % 15)

print("\n=== D. НАЙДЕНО (ревью по b4234e6): секция C проверяла только "
      "ОТВЕРГНУТЫЙ cur_tmpl. Отдельный, более коварный случай — "
      "РАЗРЕШЁННАЯ same-size адаптация: build_template() пишет "
      "template_std от cur_tmpl, а реальный template_gray после "
      "addWeighted — уже СМЕСЬ старого template и cur_tmpl, другой "
      "массив с другим std ===")
t.reset_tracking(to_acq=True)
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
scene0 = make_scene(0)
t.process_locked_tracker(scene0)
assert t.track_state == t.TRACK_STATE_TRACKED
_checked_addweighted_frames = 0
_mismatches_d = []
_tg_prev = t.template_gray.copy()
for i in range(1, 30):
    scene = make_scene(i)
    t.process_locked_tracker(scene)
    if t.track_state != t.TRACK_STATE_TRACKED:
        continue
    _tg_now = t.template_gray
    _was_blended = (t._match_dbg.get("template_adaptation_allowed") == 1
                    and _tg_now.shape == _tg_prev.shape
                    and not np.array_equal(_tg_now, _tg_prev))
    if _was_blended:
        _checked_addweighted_frames += 1
        _real_std = float(np.std(_tg_now))
        if abs(t.template_std - _real_std) > 1e-6:
            _mismatches_d.append(
                "frame %d: template_std=%.4f != реальный "
                "np.std(template_gray)=%.4f (после РАЗРЕШЁННОГО "
                "addWeighted)" % (i, t.template_std, _real_std))
    _tg_prev = _tg_now.copy()
assert _checked_addweighted_frames > 0, (
    "сценарий D должен был застать хотя бы один реально смешанный "
    "(addWeighted) кадр за 29 — иначе проверка ничего не показывает")
assert not _mismatches_d, (
    "template_std разошёлся с реальным template_gray после разрешённого "
    "addWeighted:\n  " + "\n  ".join(_mismatches_d))
print("    %d кадров с реальным addWeighted-смешиванием — template_std "
      "каждый раз совпадал с np.std(смешанного template_gray)"
      % _checked_addweighted_frames)

t.IDENTITY_UNCERTAIN_ENABLED = True

print("\nOK: адаптация блокируется на неоднозначном (lead<MATCH_LEAD_FULL) "
      "кадре, работает как прежде на однозначном, и tmpl_w/tmpl_h/"
      "template_std не расходятся с реальным template_gray — ни когда "
      "cur_tmpl выброшен целиком (C), ни когда он смешан через addWeighted "
      "(D)")
