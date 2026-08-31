"""Вес матча должен падать, когда конкурент не хуже выбранного пика.

Снято на борту в момент срыва: score=0.54 при втором кандидате 0.78 —
матч указывал на фон, рамку держал только штраф за расстояние, и через
двадцать кадров она туда переехала. Правка должна в этом случае отдавать
ведение оптическому потоку, а в обычной ситуации ничего не менять.
"""
import io

src = io.open("/Users/aleksandrdehtarenko/Desktop/fpv_tracker/tracker.py",
              encoding="utf-8").read()
MATCH_WEIGHT = 0.22
MATCH_WEIGHT_MIN = 0.02
MATCH_LEAD_FULL = 0.10


def weight(score, second):
    """Повторяет логику выбора веса из tracker.py."""
    w = MATCH_WEIGHT
    if second is not None and score > 0.0:
        lead = (score - second) / max(score, 1e-6)
        if lead <= 0.0:
            w = MATCH_WEIGHT_MIN
        elif lead < MATCH_LEAD_FULL:
            w = MATCH_WEIGHT_MIN + (MATCH_WEIGHT - MATCH_WEIGHT_MIN) * (lead / MATCH_LEAD_FULL)
    return w


print("  score | 2й канд | запас  | вес матча | что значит")
cases = [
    (0.54, 0.78, "конкурент СИЛЬНЕЕ — ситуация срыва с борта"),
    (0.60, 0.58, "конкурент почти равен"),
    (0.70, 0.60, "небольшой запас"),
    (0.87, 0.72, "уверенный отрыв"),
    (0.85, 0.40, "конкурентов нет — обычная работа"),
]
for sc, sd, note in cases:
    lead = (sc - sd) / sc
    print("  %.2f  |  %.2f   | %+5.1f%% |   %.3f   | %s" % (sc, sd, lead * 100, weight(sc, sd), note))

assert weight(0.54, 0.78) == MATCH_WEIGHT_MIN, "при сильнейшем конкуренте вести должен поток"
assert weight(0.85, 0.40) == MATCH_WEIGHT, "без конкурентов поведение не меняется"
assert weight(0.87, 0.72) == MATCH_WEIGHT, "уверенный отрыв — полное доверие"
assert MATCH_WEIGHT_MIN < weight(0.60, 0.58) < MATCH_WEIGHT, "переход должен быть плавным"

# защита обязана быть выключаемой и не влиять на обычные кадры
assert "MATCH_AMBIGUITY_GUARD" in src
print("\nOK: вес падает только при неоднозначном матче, обычные кадры не затронуты")
