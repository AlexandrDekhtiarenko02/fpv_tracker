"""Рамка гаснет, если порт полётника завис.

На вылете 142951 опрос ttyACM умер на третьем локе: aux4 навсегда остался
поднятым, стики пилоту вернулись, рамка висела до ребута. Пока MSP_RC
молчит дольше RC_LINK_DEAD_S, тумблер считаем отпущенным. Пока опрос ещё
ни разу не был живым, тумблер не трогаем — стенд без порта.
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
gray = np.zeros((t.LORES_H, t.LORES_W), np.uint8)


def test_bez_oprosa_tumbler_ne_trogaem():
    t.aux4_state = True
    t.app_state["rc_link_zhil"] = False
    t.app_state["rc_link_ts"] = 0.0
    t._svyaz_propal_uzhe = False
    assert t._aux_zhivoy() is True


def test_svezhaya_svyaz_derzhit_lok():
    t.aux4_state = True
    t.app_state["rc_link_zhil"] = True
    t.app_state["rc_link_ts"] = t.time.monotonic()
    t._svyaz_propal_uzhe = False
    assert t._aux_zhivoy() is True


def test_mertvaya_svyaz_snimaet_ramku():
    t.aux4_state = True
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_TRACKED
    t.target_visible = True
    t.target_box_main = (10, 10, 30, 30)
    t.app_state["rc_link_zhil"] = True
    t.app_state["rc_link_ts"] = t.time.monotonic() - 5.0
    t._svyaz_propal_uzhe = False
    t.override_active = True
    t.process_locked_tracker(gray)
    assert t.track_state == t.TRACK_STATE_IDLE, t.track_state
    assert t.target_box_main is None
    assert t.target_visible is False
    assert t.override_active is False
    assert t._svyaz_propal_uzhe is True
    # Повторный кадр не должен снова поднимать захват, пока связь мертва.
    t.process_locked_tracker(gray)
    assert t.track_state == t.TRACK_STATE_IDLE


def test_posle_vozvrata_svyazi_zamok_ne_voskresaet_sam():
    # Связь ожила, но тумблер уже опущен — захвата нет.
    t.aux4_state = False
    t.prev_aux_on = False
    t.app_state["rc_link_ts"] = t.time.monotonic()
    t.process_locked_tracker(gray)
    assert t.track_state == t.TRACK_STATE_IDLE
    assert t._svyaz_propal_uzhe is False


if __name__ == "__main__":
    test_bez_oprosa_tumbler_ne_trogaem()
    test_svezhaya_svyaz_derzhit_lok()
    test_mertvaya_svyaz_snimaet_ramku()
    test_posle_vozvrata_svyazi_zamok_ne_voskresaet_sam()
    print("ok")
