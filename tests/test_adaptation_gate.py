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

print("\nOK: адаптация блокируется на неоднозначном (полосатом) кадре и "
      "работает как прежде на однозначном")
