"""Скачок рамки не должен расти вместе с расхождением матча и потока.

ЗДЕСЬ ИСТОЧНИК ДЁРГАНЬЯ, и это не закон управления. Разложение команды на
слагаемые по 872 кадрам показало: рамка прыгает до 49 px за кадр, а
прицельный член честно превращает это в 170 PWM. Контур — усилитель шума
трекера, а не его причина.

Замер в кадрах со скачком >=15 px:
    расхождение матча с потоком  12.49 px против 0.39 в обычных (в 32 раза)
    однозначность пика psr        2.17 против 4.41 (вдвое)
    match_score                   0.76 против 0.85 (почти не меняется)

Отсюда два следствия. Первое: скачок рамки равен весу матча, умноженному на
расхождение, — значит гасить надо вес. Второе: доверие, опиравшееся на score,
слепо ровно к этим кадрам (в них оно показывало 1.00).
"""
import ast
import io
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


def skachok(gap):
    """Насколько подвинется рамка при таком расхождении."""
    w = zn["MATCH_WEIGHT"]
    if zn["MATCH_GAP_SOFT"] > 0.0 and gap > zn["MATCH_GAP_SOFT"]:
        w *= max(zn["MATCH_GAP_MIN_K"], zn["MATCH_GAP_SOFT"] / gap)
    return w * gap


print("=== 1. Скачок перестаёт расти с расхождением ===")
print("  %-12s %8s %10s" % ("расхожд.", "скачок", "было"))
predel = 0.0
for gap in (0.4, 4.0, 8.0, 12.5, 25.0, 50.0):
    sk = skachok(gap)
    predel = max(predel, sk)
    print("  %-12.1f %8.2f %10.2f" % (gap, sk, zn["MATCH_WEIGHT"] * gap))
assert predel < 3.0, (
    "наибольший скачок %.1f px всё ещё велик: при 24 к/с это %.0f px/с "
    "постороннего движения рамки" % (predel, predel * 24))

# И до порога поведение ПРЕЖНЕЕ: согласный с потоком матч не ослабляется.
assert abs(skachok(0.4) - zn["MATCH_WEIGHT"] * 0.4) < 1e-9, (
    "ослабление трогает кадры, где матч и поток согласны — это регресс")
print("    при согласии матча с потоком вес не тронут")

print("\n=== 2. Матч не отключается совсем ===")
assert zn["MATCH_GAP_MIN_K"] > 0.0, (
    "вес матча падает до нуля: останемся на одном потоке, а он копит "
    "собственный увод")
print("    наименьшая доля веса %.2f" % zn["MATCH_GAP_MIN_K"])

print("\n=== 3. Доверие видит расхождение, а не только score ===")
assert "TRUST_GAP_FULL" in zn, (
    "доверие снова опирается лишь на score, а он в этих кадрах не меняется")
ZAMER_GAP_OBYCHNO, ZAMER_GAP_SKACHOK = 0.39, 12.49
d_ob = min(1.0, zn["TRUST_GAP_FULL"] / max(ZAMER_GAP_OBYCHNO, zn["TRUST_GAP_FULL"]))
d_sk = min(1.0, zn["TRUST_GAP_FULL"] / max(ZAMER_GAP_SKACHOK, zn["TRUST_GAP_FULL"]))
print("    доверие по расхождению: обычно %.2f, при скачке %.2f" % (d_ob, d_sk))
assert d_ob >= 0.99, "в обычных кадрах доверие урезается — это регресс"
assert d_sk <= 0.45, (
    "при замеренном расхождении 12.5 px доверие остаётся %.2f — контур почти "
    "не убавит усиление" % d_sk)

print("\n=== 4. Порог ослабления лежит между обычным и скачком ===")
assert ZAMER_GAP_OBYCHNO < zn["MATCH_GAP_SOFT"] < ZAMER_GAP_SKACHOK, (
    "порог %.1f px не разделяет обычные кадры (%.2f) и скачки (%.2f)"
    % (zn["MATCH_GAP_SOFT"], ZAMER_GAP_OBYCHNO, ZAMER_GAP_SKACHOK))
assert ZAMER_GAP_OBYCHNO < zn["TRUST_GAP_FULL"] < ZAMER_GAP_SKACHOK

print("\nOK: скачок ограничен, согласный матч не тронут, доверие прозрело")
