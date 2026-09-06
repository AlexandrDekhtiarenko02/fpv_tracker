"""Закон наведения не имеет права зависеть от GPS.

GPS будет ТОЛЬКО на время замеров. На готовом борту приёмника нет. Значит
закон, выведенный из данных, обязан считаться из величин, которые на серийном
борту ЕСТЬ, — иначе он просто не заработает.

Роль GPS в кампании другая: он не участвует в законе, а ПРОВЕРЯЕТ те величины,
которыми закон будет пользоваться потом, — дальность по высоте и углу и
путевую скорость по бегу земли.

Этот тест следит, чтобы списки не разъехались с действительностью.
"""
import io, os, re, ast

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)


def literal(name):
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError("не нашёл %s" % name)


header = literal("_FLIGHT_LOG_COLUMNS")
cols = [c.strip() for c in header.rstrip(",").split(",") if c.strip()]
prod = literal("PRODUCTION_COLUMNS")
camp = literal("CAMPAIGN_ONLY_COLUMNS")

print("=== 1. Все перечисленные величины действительно пишутся ===")
missing = [c for c in list(prod) + list(camp) if c not in cols]
assert not missing, "в списках есть имена, которых нет в логе: %s" % missing
print("    серийных %d, только для кампании %d — все на месте" % (len(prod), len(camp)))

print("\n=== 2. Списки не пересекаются ===")
both = set(prod) & set(camp)
assert not both, "величина числится и серийной, и кампанийной: %s" % both
print("    пересечений нет")

print("\n=== 3. Всё, что от GPS, отнесено к кампании ===")
gps_like = [c for c in cols if c.startswith("gps")]
wrong = [c for c in gps_like if c in prod]
assert not wrong, (
    "величина из GPS помечена как доступная на серийном борту: %s — "
    "закон, её использующий, там не заработает" % wrong)
print("    величин из GPS: %d, все помечены как кампанийные" % len(gps_like))

print("\n=== 4. Замена GPS есть среди серийных ===")
# Без этих двух закон в поле считать не из чего.
for need, why in (("ground_speed_ms", "путевая скорость без GPS"),
                  ("range_m", "дальность по высоте и углу"),
                  ("tau_s", "время до цели по росту цели")):
    assert need in prod, "%s (%s) обязана быть доступна на серийном борту" % (need, why)
    print("    %-16s %s" % (need, why))

print("\nOK: закон можно строить только из того, что есть на серийном борту")
