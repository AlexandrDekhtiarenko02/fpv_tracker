"""Исход захода расслаивается на причины, а не сводится к «попал / не попал».

Знать, что попаданий 70%, мало: тридцать процентов промахов — это не одна
болезнь, а несколько, и лечатся они по-разному. Срыв слежения, перелёт,
недолёт и несведённый прицел требуют противоположных правок, и без
разделения любая из них с равной вероятностью делает хуже.
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
L = t.LockLogger(tempfile.mkdtemp())


def stroka(state="TRACKED", los=0.0, ady=0.0, adx=0.0, rost=4.0,
           final=False, match=0.8):
    r = [None] * len(t._COLS)
    r[t._C_STATE] = state
    r[t._C_LOS_RATE] = los
    r[t._C_ADY] = ady
    r[t._C_ADX] = adx
    r[t._C_ROST] = rost
    r[t._C_FINAL_HOLD] = final
    r[t._C_MATCH] = match
    return tuple(r)


def verdikt(stroki):
    L._hvost.clear()
    for r in stroki:
        L._hvost.append(r)
    out = L._razbor_ishoda()
    return out["вердикт"] if out else None


sluchai = [
    ("попадание",        [stroka()] * 24,                              "СОШЛОСЬ"),
    ("перелёт",          [stroka(los=2.1)] * 24,                       "ПЕРЕЛЁТ"),
    ("недолёт",          [stroka(los=-1.4)] * 24,                      "НЕДОЛЁТ"),
    ("срыв слежения",    [stroka(state="LOST")] * 24,                  "СЛЕЖЕНИЕ СОРВАЛОСЬ"),
    ("не сблизились",    [stroka(rost=1.2)] * 24,                      "НЕ СБЛИЗИЛИСЬ"),
    ("прицел верт.",     [stroka(ady=70.0)] * 24,                      "по вертикали"),
    ("прицел гориз.",    [stroka(adx=-65.0)] * 24,                     "по горизонтали"),
]
print("%-18s %s" % ("сцена", "вердикт"))
for imya, stroki, zhdem in sluchai:
    v = verdikt(stroki)
    print("  %-16s %s" % (imya, v))
    assert zhdem in v, "%s: ожидалось «%s», вышло «%s»" % (imya, zhdem, v)

print("\n=== порядок важнее отдельных признаков ===")
# Срыв слежения перекрывает всё: при нём остальные величины бессмысленны.
v = verdikt([stroka(state="LOST", los=3.0, ady=90.0, rost=1.0)] * 24)
print("    срыв + перелёт + несведён -> %s" % v)
assert "СЛЕЖЕНИЕ СОРВАЛОСЬ" in v, (
    "срыв слежения обязан перекрывать прочие признаки: при нём угол и прицел "
    "меряются по выдуманной цели")

# «Не сблизились» перекрывает угол: заход прерван далеко, угол ни при чём.
v = verdikt([stroka(rost=1.1, los=3.0)] * 24)
print("    далеко + растущий угол   -> %s" % v)
assert "НЕ СБЛИЗИЛИСЬ" in v

print("\n=== пустой хвост не роняет разбор ===")
L._hvost.clear()
assert L._razbor_ishoda() is None, "пустой заход обязан давать None, а не падать"

print("\n=== медиана, а не последний кадр ===")
# Один выброс в самом конце не должен переворачивать вердикт.
stroki = [stroka(los=0.0)] * 23 + [stroka(los=9.0)]
v = verdikt(stroki)
print("    23 спокойных кадра + выброс -> %s" % v)
assert "СОШЛОСЬ" in v, (
    "одиночный выброс перевернул вердикт: разбор обязан брать медиану")

print("\nOK: исход расслаивается, порядок соблюдён, выброс не решает")
