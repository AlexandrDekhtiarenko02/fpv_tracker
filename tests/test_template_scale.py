"""Шаблон обязан следовать за масштабом цели при сближении.

Раньше при росте коробки формы шаблонов не совпадали и обновление молча
пропускалось — шаблон застревал в старом мелком масштабе, покрывал всё
меньшую долю цели, и матчер уползал к её краю, а затем на фон. При
удалении сбоя не было: шаблон крупнее цели захватывает фон вокруг и
остаётся заякоренным. Тест закрывает обе стороны этой асимметрии.
"""
import io, numpy as np, cv2, sys

ALPHA = 0.010


def blend_old(tmpl, cur):
    """Как было: при несовпадении форм обновление пропускается."""
    if cur.shape == tmpl.shape:
        return cv2.addWeighted(tmpl, 1 - ALPHA, cur, ALPHA, 0)
    return tmpl


def blend_new(tmpl, cur):
    """Как стало: шаблон пересчитывается в новый масштаб."""
    if cur.shape != tmpl.shape:
        tmpl = cv2.resize(tmpl, (cur.shape[1], cur.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
    return cv2.addWeighted(tmpl, 1 - ALPHA, cur, ALPHA, 0)


def make_scene(size):
    """Цель заданного размера на текстурном фоне."""
    rng = np.random.default_rng(11)
    frame = (rng.random((480, 640)) * 90 + 40).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (7, 7), 0)
    cx, cy = 320, 240
    h = size // 2
    obj = np.zeros((size, size), np.uint8)
    cv2.rectangle(obj, (0, 0), (size - 1, size - 1), 220, -1)
    cv2.circle(obj, (size // 3, size // 3), max(2, size // 6), 60, -1)
    cv2.line(obj, (0, size - 1), (size - 1, 0), 30, max(1, size // 12))
    frame[cy - h:cy - h + size, cx - h:cx - h + size] = obj
    return frame, cx, cy


def run(blend, sizes):
    """Ведём шаблон через приближение и меряем, куда встаёт матч."""
    frame, cx, cy = make_scene(sizes[0])
    t = frame[cy - sizes[0] // 2:cy + sizes[0] // 2,
              cx - sizes[0] // 2:cx + sizes[0] // 2].copy()
    err = 0.0
    for s in sizes[1:]:
        frame, cx, cy = make_scene(s)
        cur = frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2].copy()
        t = blend(t, cur)
        # где матчер находит шаблон в кадре
        if t.shape[0] >= frame.shape[0] or t.shape[1] >= frame.shape[1]:
            continue
        res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(res)
        found = (loc[0] + t.shape[1] / 2, loc[1] + t.shape[0] / 2)
        err = max(err, abs(found[0] - cx) + abs(found[1] - cy))
    return err


sizes = [14, 18, 24, 30, 38, 48, 60, 74, 90]
print("Сценарий: цель приближается, растёт с %d до %d px" % (sizes[0], sizes[-1]))
e_old = run(blend_old, sizes)
e_new = run(blend_new, sizes)
print("  БЫЛО (обновление пропускалось): промах матча до %.0f px" % e_old)
print("  СТАЛО (шаблон масштабируется):  промах матча до %.0f px" % e_new)

sizes_away = list(reversed(sizes))
print("\nСценарий: цель удаляется (там сбоя и не было)")
print("  БЫЛО:  %.0f px | СТАЛО: %.0f px"
      % (run(blend_old, sizes_away), run(blend_new, sizes_away)))

assert e_new < e_old, "новая логика обязана промахиваться меньше"
assert e_new <= 2.0, "после исправления матч должен стоять по центру цели: %.1f" % e_new
print("\nOK: при сближении шаблон следует за масштабом, матч держится по центру")
