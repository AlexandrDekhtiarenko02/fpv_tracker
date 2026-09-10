"""Каждый закон газа обязан ДОВЕСТИ поправку до команды.

Этот тест ЗАПУСКАЕТ управление, а не читает исходник. Статические проверки
были зелёными, пока борт падал каждый кадр: закон газа по углу визирования
считал поправку, но не доводил её до target_throttle — общий хвост с
ограничением и сглаживанием лежал внутри соседней ветки. Каждый кадр с локом
кидал UnboundLocalError, и выглядело это как «лока нет, лупа пропала, весь
оверлей исчез»: исключение случалось ДО отрисовки.

Поэтому здесь прогоняются ВСЕ ветки газа по очереди, с проверкой, что команда
получилась и лежит в допустимых пределах.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
NASTOYASHCHIY = t._estimate_closure

CENTER_X, CENTER_Y = t.CENTER_X, t.CENTER_Y


def podgotovit(dep_deg, tau_s):
    """Состояние, при котором управление считает полный круг."""
    t.target_box_main = (CENTER_X - 20, CENTER_Y - 10,
                         CENTER_X + 20, CENTER_Y + 30)
    t.target_visible = True
    t.target_controllable = True
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 20.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["fc_roll_deg"] = 0.0
        t.app_state["alt_cm"] = 4000
        t.app_state["alt_ts"] = t.time.monotonic()
        t.app_state["vario_cms"] = -150
        t.app_state["gyro"] = (10, 20, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    # Оценка сближения подсовывается напрямую: сама она требует истории
    # кадров, которой в тесте нет. Настоящая функция запоминается ОДИН раз,
    # иначе подмена оборачивает саму себя и уходит в рекурсию.
    def podmena(*a, **kw):
        out = dict(NASTOYASHCHIY(*a, **kw))
        out["depression_deg"] = dep_deg
        out["tau_s"] = tau_s
        return out
    t._estimate_closure = podmena


vetki = [
    ("газ у пилота", dict(OVERRIDE_THROTTLE=False), 25.0, 7.0),
    ("по углу визирования", dict(OVERRIDE_THROTTLE=True,
                                 LOS_THROTTLE_ENABLED=True), 25.0, 7.0),
    ("по времени до контакта", dict(OVERRIDE_THROTTLE=True,
                                    LOS_THROTTLE_ENABLED=False,
                                    THROTTLE_BY_TAU=True), None, 2.0),
    ("по вертикальной ошибке", dict(OVERRIDE_THROTTLE=True,
                                    LOS_THROTTLE_ENABLED=False,
                                    THROTTLE_BY_TAU=False), None, None),
]

ishodno = {k: getattr(t, k) for k in
           ("OVERRIDE_THROTTLE", "LOS_THROTTLE_ENABLED", "THROTTLE_BY_TAU")}
try:
    for imya, nastroyki, dep, tau in vetki:
        for k, v in ishodno.items():
            setattr(t, k, v)
        for k, v in nastroyki.items():
            setattr(t, k, v)
        podgotovit(dep, tau)
        t._los_ugol = None
        t._los_skorost = 0.0
        # Два вызова: первый задаёт начало отсчёта угла, второй считает по нему.
        t.update_control_from_target()
        t.update_control_from_target()
        gaz = t.global_throttle_cmd
        print("  %-26s газ = %s" % (imya, gaz))
        assert gaz is not None, "%s: команда газа не получена" % imya
        assert t.THROTTLE_MIN_PWM <= gaz <= t.THROTTLE_MAX_PWM, (
            "%s: газ %s вне пределов %d..%d"
            % (imya, gaz, t.THROTTLE_MIN_PWM, t.THROTTLE_MAX_PWM))
finally:
    for k, v in ishodno.items():
        setattr(t, k, v)
    t._estimate_closure = NASTOYASHCHIY

print("все ветки газа доводят поправку до команды")
