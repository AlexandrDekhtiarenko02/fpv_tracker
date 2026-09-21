"""Нормализация размера матча (ТЗ next-commit spec §3/§8) не должна уводить
центр и не должна давать стоимости матча расти с размером эталона.

Два синтетических сценария, оба напрямую по template_match_locked — самой
рискованной точке правки: она задаёт координаты, которые в итоге двигают
физический аппарат, и любая систематическая ошибка тут — не косметика.

15.1 Zoom без сдвига центра: истинный центр цели неподвижен, эталон растёт
     1.00 -> 1.05 -> ... -> далеко за MATCH_CANONICAL_PX. Ожидание: центр
     не имеет систематического дрейфа, а карта откликов не растёт
     пропорционально площади эталона (иначе нормализация не сработала).

15.2 Translation + zoom: цель одновременно сдвигается и растёт. Проверяем,
     что оценка сдвига не поглощается масштабом и ошибка центра не растёт
     вместе с ростом эталона.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()
# Изолируем ИМЕННО геометрию матчера: цвет и движение — отдельные признаки
# со своими тестами (test_color_guard.py, test_motion_guard.py), здесь они
# были бы шумом, не связанным с нормализацией размера.
t.MOTION_GUARD_ENABLED = False
t.color_active = False

LORES_W, LORES_H = t.LORES_W, t.LORES_H
CX0, CY0 = LORES_W // 2, LORES_H // 2


def make_scene(obj_cx, obj_cy, obj_size):
    """Текстурный фон + непустой объект известного размера в известной точке.

    Объект НЕ ОДНОРОДЕН (иначе template_std уйдёт в TEMPLATE_STARVED и
    сработает другая ветка кода, не та, что здесь проверяется) и не
    симметричен (иначе субпиксельная интерполяция даст ложный ноль ошибки
    независимо от того, работает нормализация или нет).
    """
    rng = np.random.default_rng(42)
    frame = (rng.random((LORES_H, LORES_W)) * 80 + 60).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    s = int(round(obj_size))
    if s < 4:
        s = 4
    obj = np.zeros((s, s), np.uint8)
    cv2.rectangle(obj, (0, 0), (s - 1, s - 1), 210, -1)
    cv2.circle(obj, (s // 3, s // 3), max(2, s // 6), 40, -1)
    cv2.line(obj, (0, s - 1), (s - 1, 0), 20, max(1, s // 14))
    x0 = int(round(obj_cx - s / 2.0))
    y0 = int(round(obj_cy - s / 2.0))
    x1, y1 = x0 + s, y0 + s
    # Обрезаем по границам кадра — на больших масштабах у края объект может
    # частично не влезть, это нормально (реальный кадр тоже обрезает).
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(LORES_W, x1), min(LORES_H, y1)
    if fx1 <= fx0 or fy1 <= fy0:
        return frame
    frame[fy0:fy1, fx0:fx1] = obj[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    return frame


def lock_template_at(scene, cx, cy, size):
    """Смоделировать «эталон только что перестроен под текущий размер цели»
    — ровно то, что делает growth-механизм на реальном борту (не трогаем
    его логику, просто ставим её РЕЗУЛЬТАТ в глобалы, как она бы поставила).
    """
    t.template_gray = t.build_template(scene, cx, cy, size, size)


print("=== 15.1. Zoom без сдвига центра ===")
BASE_SIZE = 16.0
scales = [1.0, 1.3, 1.8, 2.5, 3.5, 5.0, 7.0, 9.0]
max_abs_err = 0.0
map_areas = []
for s in scales:
    size = BASE_SIZE * s
    scene = make_scene(CX0, CY0, size)
    lock_template_at(scene, CX0, CY0, size)
    ok, mcx, mcy, score = t.template_match_locked(scene, float(CX0), float(CY0),
                                                  flow_motion=0.0)
    err = ((mcx - CX0) ** 2 + (mcy - CY0) ** 2) ** 0.5
    map_areas.append(t._match_dbg.get("map_w", 0) * t._match_dbg.get("map_h", 0))
    print("    box=%5.1f px  tmpl=%3dx%-3d  map=%2dx%-2d  err=%.3f px  score=%.3f  ok=%s"
          % (size, t.tmpl_w, t.tmpl_h,
             t._match_dbg.get("map_w", -1), t._match_dbg.get("map_h", -1),
             err, score, ok))
    assert ok, "матч не сошёлся на масштабе %.1f — тест сам по себе негоден" % s
    max_abs_err = max(max_abs_err, err)

print("    максимальная ошибка центра по всем масштабам: %.3f px" % max_abs_err)
assert max_abs_err < 1.0, (
    "систематический дрейф центра при росте эталона: %.2f px — нормализация "
    "масштаба сломала геометрию (ровно тот класс бага, что уже правили "
    "раньше: рамка уезжает пропорционально ошибке размера)" % max_abs_err)

# ГЛАВНОЕ ДОКАЗАТЕЛЬСТВО РАЗВЯЗКИ СТОИМОСТИ. Если нормализация работает,
# площадь карты откликов ПЕРЕСТАЁТ расти вместе с реальным эталоном после
# порога MATCH_CANONICAL_PX. Меряем не время (шумно, зависит от машины), а
# сам размер рабочего массива — прямую причину стоимости matchTemplate.
big_areas = map_areas[3:]      # масштабы, где реальный эталон уже далеко
                                # за MATCH_CANONICAL_PX (16*3.5=56..16*9=144)
print("    площади карты на крупных масштабах:", big_areas)
assert max(big_areas) <= min(big_areas) * 2.0, (
    "площадь карты откликов продолжает расти с размером эталона (%s) — "
    "нормализация к каноническому размеру не сработала, стоимость матча "
    "снова зависит от box_size_px" % big_areas)

print("\n=== 15.2. Translation + zoom ===")
# Цель одновременно сдвигается и растёт. Проверяем, что оценка сдвига не
# поглощается масштабом: ошибка не должна расти вместе с ростом эталона.
true_dx, true_dy = 3.0, -2.0
max_abs_err2 = 0.0
for s in scales:
    size = BASE_SIZE * s
    tcx, tcy = CX0 + true_dx, CY0 + true_dy
    scene_at_lock = make_scene(CX0, CY0, size)
    lock_template_at(scene_at_lock, CX0, CY0, size)
    scene_moved = make_scene(tcx, tcy, size)
    # pred_cx/pred_cy — предсказание потока чуть смещено от истинного нового
    # центра (как в жизни, поток редко угадывает идеально), матчер должен
    # САМ найти истинный центр, а не то, куда его подтолкнули.
    pred_cx, pred_cy = CX0 + true_dx * 0.5, CY0 + true_dy * 0.5
    ok, mcx, mcy, score = t.template_match_locked(
        scene_moved, pred_cx, pred_cy, flow_motion=abs(true_dx) + abs(true_dy))
    err = ((mcx - tcx) ** 2 + (mcy - tcy) ** 2) ** 0.5
    print("    box=%5.1f px  найдено=(%.2f,%.2f)  истина=(%.2f,%.2f)  err=%.3f px  ok=%s"
          % (size, mcx, mcy, tcx, tcy, err, ok))
    assert ok, "матч не сошёлся при сдвиге+масштабе %.1f" % s
    max_abs_err2 = max(max_abs_err2, err)

print("    максимальная ошибка центра по всем масштабам: %.3f px" % max_abs_err2)
assert max_abs_err2 < 1.5, (
    "ошибка оценки сдвига растёт вместе с масштабом (%.2f px) — перевод "
    "координат из канонического представления обратно в реальные пиксели "
    "считает неверно" % max_abs_err2)

print("\nOK: центр не дрейфует с ростом эталона, стоимость карты откликов "
      "не растёт, сдвиг+масштаб считаются верно")
