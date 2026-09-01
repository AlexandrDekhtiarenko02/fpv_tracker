"""Заголовок CSV и строка данных обязаны совпадать по числу полей.

Мы уже обожглись на том, что вставка колонок в середину списка молча сдвинула
индексы разбора. Здесь проверяется более грубая, но более важная вещь: если
колонку добавили в заголовок и забыли в строку (или наоборот), весь лог
съезжает и все прежние скрипты разбора врут, ничего не сообщая.
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tree = ast.parse(io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read())

header = None
row_len = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
    if isinstance(node, ast.FunctionDef) and node.name == "_capture_flight_row":
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "row"
                    and call.args
                    and isinstance(call.args[0], ast.Tuple)):
                row_len = len(call.args[0].elts)

assert header is not None, "не нашёл _COLS"
assert row_len is not None, "не нашёл кортеж строки в _capture_flight_row"
cols = header.rstrip(",").split(",")
print("колонок в заголовке: %d" % len(cols))
print("полей в строке:      %d" % row_len)
assert len(cols) == row_len, (
    "расхождение: заголовок %d, строка %d — лог съедет" % (len(cols), row_len))
assert len(cols) == len(set(cols)), "дублирующиеся имена колонок: %s" % (
    [c for c in cols if cols.count(c) > 1],)
print("OK: заголовок и строка совпадают, имена уникальны")
