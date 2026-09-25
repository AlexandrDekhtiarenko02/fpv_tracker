"""Запрет адаптации на сомнительных кадрах (ТЗ next-commit spec §9).

Высокий score сам по себе не значит «кадр надёжен» — он может быть
одновременно у нескольких кандидатов. На периодической текстуре (полосатый
фон — классический случай, ради которого писан ambiguity guard) рядом с
целью есть почти такой же по силе конкурент через каждый период полосы:
низкий PSR/lead, guard уже режет вес матча при выборе позиции. Шаблон не
должен на таком кадре учиться — иначе он рискует медленно съехать к
соседней полосе, даже когда позиция ещё держится потоком.

Два сценария, оба с независимым шумом по кадрам (без него addWeighted было
бы неотличимо от «не изменилось вовсе» — цель сама по себе не движется):
  A. Периодические полосы вокруг цели (искусственная неоднозначность).
     Шаблон обязан остаться БИТ-В-БИТ неизменным все кадры подряд.
  B. Непериодическая текстура, обычный однозначный лок. Шаблон ОБЯЗАН
     меняться — иначе гейт просто блокирует адаптацию всегда, а не по делу.
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

CX, CY = t.LORES_W // 2, t.LORES_H // 2
R = 40  # покрывает окно поиска при типичном margin


def make_scene(frame_seed, periodic, period=7):
    """periodic=True — полосатая текстура вокруг цели (много равных пиков
    в карте откликов через каждый период); periodic=False — обычный шум
    (один уверенный пик)."""
    rng = np.random.default_rng(1000 + frame_seed)
    frame = (rng.random((t.LORES_H, t.LORES_W)) * 70 + 50).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    y0, y1 = CY - R, CY + R
    x0, x1 = CX - R, CX + R
    if periodic:
        xs = np.arange(x0, x1)
        stripe = ((np.sin(2 * np.pi * xs / period) > 0).astype(np.uint8)
                  * 120 + 60)
        patch = np.tile(stripe, (y1 - y0, 1))
    else:
        patch = (rng.random((y1 - y0, x1 - x0)) * 120 + 60).astype(np.uint8)
        cv2.circle(patch, (R, R), R // 3, 40, -1)
        cv2.line(patch, (0, 2 * R - 1), (2 * R - 1, 0), 20, R // 8)
    noise = rng.integers(-6, 7, patch.shape)
    patch = np.clip(patch.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    frame[y0:y1, x0:x1] = patch
    return frame


def run_scenario(periodic, n_frames=15):
    t.reset_tracking(to_acq=True)
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    scene0 = make_scene(0, periodic)
    t.process_locked_tracker(scene0)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"
    tmpl0 = t.template_gray.copy()
    changed_any = False
    allowed_seen = []
    for i in range(1, n_frames + 1):
        scene = make_scene(i, periodic)
        t.process_locked_tracker(scene)
        allowed_seen.append(t._match_dbg.get("template_adaptation_allowed"))
        if (t.track_state == t.TRACK_STATE_TRACKED
                and t.template_gray.shape == tmpl0.shape
                and not np.array_equal(t.template_gray, tmpl0)):
            changed_any = True
    return changed_any, allowed_seen, t._match_dbg.get("adapt_skip_reason")


print("=== A. Полосатый фон — конкурент через каждый период, шаблон не учится ===")
changed, allowed, reason = run_scenario(periodic=True)
print("    template_adaptation_allowed по кадрам:", allowed)
print("    template_gray менялся:", changed, " причина запрета:", reason)
assert not any(allowed), (
    "полосатый фон рядом с целью даёт равных конкурентов через период, а "
    "гейт хоть раз разрешил адаптацию — ambiguity guard не подключён к "
    "запрету обучения шаблона")
assert not changed, (
    "template_gray изменился, хотя гейт держал adaptation_allowed=0 весь "
    "прогон — запрет не долистался до реального вызова addWeighted")
assert reason == "ambiguous_peak", (
    "причина запрета должна называться ambiguous_peak, получено %r" % reason)

print("\n=== B. Обычная текстура — однозначный лок, адаптация должна идти ===")
changed2, allowed2, _ = run_scenario(periodic=False)
print("    template_adaptation_allowed по кадрам:", allowed2)
print("    template_gray менялся:", changed2)
assert any(allowed2), (
    "без периодической текстуры гейт всё равно держит adaptation_allowed=0 "
    "— либо порог MATCH_LEAD_FULL проверяется неверно, либо gate закрыт "
    "навсегда")
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
scene0 = make_scene(0, periodic=True)
t.process_locked_tracker(scene0)
assert t.track_state == t.TRACK_STATE_TRACKED
_mismatches = []
for i in range(1, 16):
    scene = make_scene(i, periodic=True)
    t.process_locked_tracker(scene)
    if t.track_state != t.TRACK_STATE_TRACKED:
        continue
    assert t._match_dbg.get("adapt_skip_reason") == "ambiguous_peak", (
        "сценарий C предполагает тот же гейт-отказ каждый кадр, что и "
        "секция A — иначе эта проверка ничего не показывает")
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
scene0 = make_scene(0, periodic=False)
t.process_locked_tracker(scene0)
assert t.track_state == t.TRACK_STATE_TRACKED
_checked_addweighted_frames = 0
_mismatches_d = []
_tg_prev = t.template_gray.copy()
for i in range(1, 30):
    scene = make_scene(i, periodic=False)
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

print("\nOK: адаптация блокируется на неоднозначном (полосатом) кадре, "
      "работает как прежде на однозначном, и tmpl_w/tmpl_h/template_std "
      "не расходятся с реальным template_gray — ни когда cur_tmpl "
      "выброшен целиком (C), ни когда он смешан через addWeighted (D)")
