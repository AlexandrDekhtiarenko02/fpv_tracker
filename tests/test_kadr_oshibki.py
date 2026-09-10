"""Сбой в обработке кадра обязан попадать в журнал.

Раньше весь camera_callback был обёрнут в `except: pass`. На борту это
выглядело так: при поднятии тумблера пропадала лупа, не появлялась рамка,
состояние застревало в ACQ — и ни строки в журнале. Всё это один сбой,
проглоченный молча. День разбора ушёл на симптомы вместо трассы.

Ронять поток нельзя — кадры перестанут идти вовсе. Но молчать нельзя тем
более.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "camera_callback")

# Немой обработчик ВНУТРИ другого — законен: он страхует саму запись в
# журнал, чтобы сбой логирования не превратился в бесконечную рекурсию.
# Ищем только те, что ловят работу кадра, а не свою же диагностику.
vnutri = set()
for h in ast.walk(fn):
    if isinstance(h, ast.ExceptHandler):
        for v in ast.walk(h):
            if isinstance(v, ast.ExceptHandler) and v is not h:
                vnutri.add(v.lineno)

nemye = []
for h in ast.walk(fn):
    if isinstance(h, ast.ExceptHandler) and h.lineno not in vnutri:
        # Обработчик, который только `pass`, — это выброшенная диагностика.
        if all(isinstance(x, ast.Pass) for x in h.body):
            nemye.append(h.lineno)
assert not nemye, (
    "в camera_callback снова есть немой except (строки %s): сбой станет "
    "невидим, а выглядеть будет как пропавшая лупа без всякой причины"
    % nemye)

telo = ast.get_source_segment(src, fn) or ""
assert "traceback.format_exc()" in telo, (
    "сбой пишется без трассы — по одному сообщению причину не найти")
assert "flight_log.event" in telo, "сбой не попадает в журнал вылета"
assert "flush=True" in telo, (
    "сбой не выводится в stdout: службу смотрят через journalctl")

# Печать ограничена по частоте: 24 кадра в секунду залили бы журнал.
assert "KADR_OSHIBKA_PERIOD_S" in telo, (
    "нет ограничения частоты: одна и та же трасса вытеснит из журнала всё "
    "остальное")
zn = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        imya = getattr(node.targets[0], "id", None)
        if imya:
            try:
                zn[imya] = ast.literal_eval(node.value)
            except Exception:
                pass
assert 0.5 <= zn["KADR_OSHIBKA_PERIOD_S"] <= 10.0, (
    "период печати %.1f с вне разумного" % zn["KADR_OSHIBKA_PERIOD_S"])

# Счётчик: одна строка раз в две секунды не должна скрывать, что сбоит
# каждый кадр.
assert "_cb_oshibka_n += 1" in telo, (
    "нет счётчика сбоев: по редким строкам не отличить единичный сбой от "
    "непрерывного")
print("сбой кадра: трасса, журнал, stdout, счётчик, не чаще %.0f с"
      % zn["KADR_OSHIBKA_PERIOD_S"])
