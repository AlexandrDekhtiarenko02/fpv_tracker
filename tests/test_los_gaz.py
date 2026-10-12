"""Растущий угол визирования правит ПРИЦЕЛ, а не газ.

На курсе столкновения с неподвижной целью угол визирования стоит. Растёт —
цель уходит под нас, пройдём выше.

ЭТУ РАБОТУ СНАЧАЛА ОТДАЛИ ГАЗУ, И ЭТО БЫЛА ОШИБКА. В поле вышло ровно то,
чего и следовало ждать от лечения геометрии энергией: угол продолжал расти
(1-5 °/с, перелёт в пяти заходах из восьми), а газ проседал до 91 PWM ниже
стика — почти до холостого. Убирая тягу, квад не только снижается, но и
теряет скорость: вектор скорости к цели не приближается, аппарат проваливается.

Прицел при этом стоял на цели с точностью до пары пикселей. Значит нос
смотрел верно, а ЛЕТЕЛ аппарат выше линии визирования — это ошибка
прицеливания, и лечится она опусканием точки прицеливания. Ровно это пилот
делал руками и называл «держать нос ниже цели».
"""
import ast
import io
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

zn = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        imya = getattr(node.targets[0], "id", None)
        if imya:
            try:
                zn[imya] = ast.literal_eval(node.value)
            except Exception:
                pass

assert zn["LOS_AIM_ENABLED"] is True, "поправка прицела по углу выключена"
assert zn["LOS_THROTTLE_ENABLED"] is False, (
    "газ снова держит угол визирования: это лечение геометрии энергией, и в "
    "поле оно дало растущий угол при просевшем газе")

ZAMER_DPS = 2.13   # замеренная скорость роста на промахе
sdvig = min(zn["LOS_AIM_GAIN"] * ZAMER_DPS, zn["LOS_AIM_MAX_PX"])
print("на замеренных %.2f °/с прицел опускается на %.0f px" % (ZAMER_DPS, sdvig))
OBYCHNAYA_OSHIBKA_PX = 15.0   # замеренный коридор ошибки прицела
assert sdvig > OBYCHNAYA_OSHIBKA_PX, (
    "поправка %.0f px теряется в обычной ошибке прицела (%.0f px) — она не "
    "изменит траекторию" % (sdvig, OBYCHNAYA_OSHIBKA_PX))
# Предел держится ЖЁСТКО. Прежние 110 — почти половина кадра по вертикали;
# поправка упиралась в них и подменяла собой прицеливание, давая рывок.
assert zn["LOS_AIM_MAX_PX"] <= 60.0, (
    "предел %.0f px слишком велик: поправка перестанет быть поправкой и "
    "подменит прицеливание" % zn["LOS_AIM_MAX_PX"])
assert zn["LOS_AIM_MAX_PX"] > OBYCHNAYA_OSHIBKA_PX, "предел ниже самой ошибки"
# Мёртвая зона обязана применяться и к прицелу, а не только к газу.
# С введением проверки свежести (LOS_RATE_STALE_S) чтение идёт через
# промежуточную _los_val — но само вычитание мёртвой зоны должно остаться.
assert "- LOS_RATE_DEADBAND_DPS" in src, (
    "мёртвая зона к прицелу не применяется: поправка пойдёт на остаточном шуме")
assert "izbytok = _los_val - LOS_RATE_DEADBAND_DPS" in src or \
       "izbytok = _los_skorost - LOS_RATE_DEADBAND_DPS" in src, (
    "изменилось имя переменной, проверить логику мёртвой зоны в прицеле")
# И ограничение скорости самой поправки — последняя преграда рывку.
assert "LOS_AIM_SLEW_PX_S" in src, "поправка прицела может прыгать"
_shag = zn["LOS_AIM_SLEW_PX_S"] / zn["CAM_FPS"]
print("поправка движется не быстрее %.1f px за кадр (полный ход за %.1f с)"
      % (_shag, zn["LOS_AIM_MAX_PX"] / zn["LOS_AIM_SLEW_PX_S"]))
assert _shag <= 3.0, "%.1f px за кадр — это всё ещё рывок" % _shag

# Окно оценки: длиннее периода раскачки, короче самого захода.
print("окно оценки угла %.1f с, не менее %d точек"
      % (zn["LOS_FIT_WINDOW_S"], zn["LOS_FIT_MIN_POINTS"]))
assert zn["LOS_FIT_WINDOW_S"] >= 1.0 / 1.3 * 2, (
    "окно %.1f с короче двух периодов раскачки (1.3 Гц) — колебание пройдёт "
    "в оценку как тренд" % zn["LOS_FIT_WINDOW_S"])
assert zn["LOS_FIT_WINDOW_S"] <= 4.0, (
    "окно %.1f с слишком длинное: оценка не успеет за геометрией захода"
    % zn["LOS_FIT_WINDOW_S"])
assert zn["LOS_FIT_MIN_POINTS"] >= 8, "слишком мало точек для наклона"

# Знак. Угол растёт -> пройдём выше -> целимся НИЖЕ -> прицел опускается.
assert "hochu = LOS_AIM_GAIN * izbytok" in src, (
    "знак поправки прицела не тот: при растущем угле целиться надо ниже")
assert "+ los_aim_px) - CENTER_Y" in src, (
    "поправка не входит в прицельную ошибку")

# Величина обязана СЧИТАТЬСЯ вне выключенной ветки газа, иначе замрёт.
i_gaz = src.index("elif LOS_THROTTLE_ENABLED")
i_rasch = src.index("_dep_hist.append((now_mono, float(_dep_tek)))")
assert i_rasch < i_gaz, (
    "скорость угла снова считается внутри ветки газа, а та выключена — "
    "прицел получит вечный ноль и никак этого не покажет")

# Признак промаха обязан быть виден в логе.
header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
kol = header.rstrip(",").split(",")
for imya in ("los_rate", "los_aim_px"):
    assert imya in kol, "нет колонки %s — промах нечем будет разобрать" % imya

# Состояние сбрасывается между заходами.
assert src.count("_los_ugol = None") >= 2, (
    "угол визирования не сбрасывается при потере управления")
print()
print("=== СТУПЕНИ ТАНГАЖА НЕ ДОЛЖНЫ ДАВАТЬ ЛОЖНУЮ СКОРОСТЬ ===")
# Это та самая беда, что стоила вылета: тангаж приходит от полётника
# ступенями (в логе 14.3 -> 16.8 -> 17.6 -> 18.1 -> 21.1), и разность соседних
# кадров превращает ступень 2.5° в 60 °/с при настоящем тренде около 2 °/с.
import collections as _c

FPS = zn["CAM_FPS"]
OKNO = zn["LOS_FIT_WINDOW_S"]
MINP = zn["LOS_FIT_MIN_POINTS"]


def naklon_po_oknu(ryad):
    """Так теперь считает трекер."""
    h = _c.deque()
    out = []
    for tt, v in ryad:
        h.append((tt, v))
        while h and (tt - h[0][0]) > OKNO:
            h.popleft()
        if len(h) >= MINP:
            xs = [a for a, _ in h]
            ys = [b for _, b in h]
            n = len(xs)
            mx = sum(xs) / n
            my = sum(ys) / n
            den = sum((x - mx) ** 2 for x in xs)
            if den > 1e-6:
                out.append(sum((xs[i] - mx) * (ys[i] - my)
                               for i in range(n)) / den)
    return out


def raznost_s_filtrom(ryad, alpha):
    """Так считал раньше — разность соседних кадров плюс фильтр."""
    out = []
    sk = 0.0
    for i in range(1, len(ryad)):
        dt = ryad[i][0] - ryad[i - 1][0]
        if dt <= 0:
            continue
        sk += alpha * ((ryad[i][1] - ryad[i - 1][1]) / dt - sk)
        out.append(sk)
    return out


# Настоящий тренд 2 °/с, а тангаж приходит ступенями по 2.5° раз в 4 кадра.
ryad = []
stupen = 0.0
for i in range(int(FPS * 6)):
    tt = i / FPS
    istina = 2.0 * tt
    if istina - stupen >= 2.5:
        stupen += 2.5
    ryad.append((tt, stupen))

po_oknu = naklon_po_oknu(ryad)
po_raznosti = raznost_s_filtrom(ryad, zn["LOS_RATE_ALPHA"])
m_okno = max(abs(x) for x in po_oknu)
m_razn = max(abs(x) for x in po_raznosti)
print("    истинный тренд 2.0 °/с")
print("    наклон по окну:    наибольшее %.2f °/с" % m_okno)
print("    разность+фильтр:   наибольшее %.2f °/с" % m_razn)
assert m_okno < 4.0, (
    "наклон по окну даёт %.1f °/с при тренде 2.0 — ступени всё ещё проходят "
    "насквозь, и прицел будет рвать" % m_okno)
assert m_razn > m_okno, (
    "разность соседних кадров не хуже окна — значит опыт не воспроизводит "
    "беду, и сравнивать не с чем")
# И главное: во что это превращается в пикселях прицела.
px_okno = min(zn["LOS_AIM_GAIN"] * m_okno, zn["LOS_AIM_MAX_PX"])
px_razn = min(20.0 * m_razn, 110.0)      # прежние коэффициент и предел
print("    прицел: было бы %.0f px, стало %.0f px" % (px_razn, px_okno))
assert px_okno < px_razn * 0.6, (
    "поправка почти не уменьшилась: %.0f против %.0f px" % (px_okno, px_razn))

print()
print("=== ПРИ БЫСТРОМ ВРАЩЕНИИ УГОЛ НЕ СЧИТАЕТСЯ ===")
# Разбор клевка: поправка прицела стояла в упоре (+45 px) весь эпизод, давая
# постоянный нос-вниз, пока контур пытался выровняться. Угол визирования
# держится на том, что тангаж и положение цели в кадре меняются навстречу, —
# но они НЕ синхронны: в логе fc_pitch стоял на 36.8 пять кадров подряд,
# потом прыгнул на 9.2°, а box_cy ехал каждый кадр.
assert "LOS_MAX_VRASH_DPS" in zn, (
    "оценка угла не защищена от рассинхрона: на быстром довороте она соврёт, "
    "и поправка встанет в упор")
VRASH_OBYCHNO, VRASH_KLEVOK = 16.0, 90.0
print("    порог %.0f °/с (обычный заход %.0f, клевок %.0f)"
      % (zn["LOS_MAX_VRASH_DPS"], VRASH_OBYCHNO, VRASH_KLEVOK))
assert VRASH_OBYCHNO < zn["LOS_MAX_VRASH_DPS"] < VRASH_KLEVOK, (
    "порог %.0f не разделяет обычный заход (%.0f) и клевок (%.0f)"
    % (zn["LOS_MAX_VRASH_DPS"], VRASH_OBYCHNO, VRASH_KLEVOK))
assert "abs(_gyro_y_sgl) > LOS_MAX_VRASH_DPS" in src, "порог не применяется"
assert "_dep_hist.clear()" in src, (
    "история угла не очищается при вращении: в окне останутся кадры, снятые "
    "во время рассинхрона, и наклон по ним соврёт")

print()
print("=== ПОПРАВКА ОСТАЁТСЯ ПОПРАВКОЙ ===")
OSHIBKA_90_PX = 25.0   # замеренная ошибка прицела по вертикали
print("    предел %.0f px при обычной ошибке %.0f px"
      % (zn["LOS_AIM_MAX_PX"], OSHIBKA_90_PX))
assert zn["LOS_AIM_MAX_PX"] < OSHIBKA_90_PX, (
    "предел %.0f не ниже самой ошибки (%.0f): поправка сможет подменить "
    "прицеливание, что в клевке и случилось"
    % (zn["LOS_AIM_MAX_PX"], OSHIBKA_90_PX))

print()
print("прицел правится углом, газ этим больше не занят")
