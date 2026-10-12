"""Меню на стиках: только без арма, висение пишется и запоминается."""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()


def sbros():
    t._menu_zakryt()
    for k in t._menu_pred:
        t._menu_pred[k] = 0
    t._hover_pwm = None
    t.HOVER_PWM = None
    t._hover_logged = False


def stiki(gaz=1500, tang=1500, kren=1500, rych=1500):
    return [kren, tang, rych, gaz, 1500, 1500, 1500, 1000]


sbros()
put = os.path.join(tempfile.mkdtemp(), "local_settings.py")
t._nastroiki_put = put

print("=== 1. Под армом меню не открывается ===")
for i in range(8):
    t._menu_tick(1000.0 + i * 0.1, stiki(gaz=2000, tang=2000), True)
assert not t._menu_otkryto, "меню открылось на взведённом борту"

print("\n=== 2. Без арма оба стика вверх открывают сразу ===")
sbros()
t._nastroiki_put = put
assert t._menu_tick(2000.0, stiki(gaz=2000, tang=2000), False)
assert t._menu_stroki()[0][0].startswith("Throttle")

print("\n=== 3. Правый вправо заходит в висение и меняет его ===")
t._menu_tick(2001.0, stiki(), False)
assert t._menu_tick(2001.2, stiki(kren=2000), False)
assert t._menu_stroki()[0][0].startswith("Hover")
t._menu_tick(2001.5, stiki(), False)
assert t._menu_tick(2001.7, stiki(kren=2000), False)
assert t._menu_pravka
tekst = t._menu_stroki()[0][0]
assert "1400" in tekst, tekst
t._menu_tick(2002.0, stiki(), False)
t._menu_tick(2002.2, stiki(tang=2000), False)
assert t._hover_pwm == 1410, t._hover_pwm
assert "HOVER_PWM = 1410" in open(put, encoding="utf-8").read()

print("\n=== 3b. Короткий жест — один шаг, долгий — ровно по 10 ===")
t._menu_tick(2002.7, stiki(tang=2000), False)
assert t._hover_pwm == 1410, t._hover_pwm
t._menu_tick(2002.85, stiki(tang=2000), False)
assert t._hover_pwm == 1420, t._hover_pwm
t._menu_tick(2002.90, stiki(tang=1750), False)
assert t._hover_pwm == 1420, "дрожание у края дало лишний шаг"
t._menu_tick(2003.0, stiki(tang=2000), False)
assert t._hover_pwm == 1430, t._hover_pwm

print("\n=== 4. Арм закрывает меню ===")
t._menu_tick(2003.0, stiki(), True)
assert not t._menu_otkryto

print("\n=== 5. Левый влево закрывает ===")
sbros()
t._nastroiki_put = put
t._menu_tick(3000.0, stiki(gaz=2000, tang=2000), False)
assert t._menu_otkryto
t._menu_tick(3000.4, stiki(), False)
assert t._menu_tick(3000.6, stiki(rych=1000), False) is False

print("\n=== 6. Tools: обновление и очистка логов ===")
sbros()
t._nastroiki_put = put
t._menu_tick(4000.0, stiki(gaz=2000, tang=2000), False)
t._menu_tick(4000.2, stiki(), False)
t._menu_tick(4000.4, stiki(tang=1000), False)
t._menu_tick(4000.5, stiki(), False)
t._menu_tick(4000.55, stiki(tang=1000), False)
vybr = [s[0] for s in t._menu_stroki() if s[1]]
assert vybr == ["Tools"], (vybr, t._menu_stroki())
t._menu_tick(4000.6, stiki(), False)
t._menu_tick(4000.8, stiki(kren=2000), False)
imena = [s[0] for s in t._menu_stroki()]
assert imena == ["Update", "Restart", "Record  off", "Clear logs", "Wi-Fi"], imena
vyzovy = []
t._menu_zapustit(lambda: vyzovy.append(1) or "ok")
import time as _time
_time.sleep(0.3)
assert vyzovy == [1], vyzovy
assert t._menu_status == "ok"
with t.state_lock:
    t.app_state["armed"] = True
assert t._menu_obnovit() == "armed"
assert t._menu_ochistit_logi() == "armed"

print("\n=== 7. Обновление показывает коммит и перезапуск ===")
with t.state_lock:
    t.app_state["armed"] = False
t._menu_ekran = []
t._menu_skript = lambda *a, **k: (0, "")
t._menu_tekushchiy_commit = lambda: "abc1234"
ubity = []
t.os.kill = lambda pid, sig: ubity.append((pid, sig))
t.time.sleep = lambda s: None
assert t._menu_obnovit() == "restarting"
assert t._menu_ekran == ["updated", "abc1234", "restarting"], t._menu_ekran
assert ubity, "процесс не перезапускается"

print("\n=== 8. Restart и Record ===")
t._menu_ekran = []
ubity.clear()
assert t._menu_perezapusk() == "restarting"
assert t._menu_ekran == ["restarting"]
assert ubity
t.RECORD_FRAMES = False
with t.state_lock:
    t.app_state["armed"] = False
assert t._menu_zapis() == "record on"
assert t.RECORD_FRAMES is True
assert "RECORD_FRAMES = True" in open(put, encoding="utf-8").read()
assert t._menu_zapis() == "record off"
assert "RECORD_FRAMES = False" in open(put, encoding="utf-8").read()
with t.state_lock:
    t.app_state["armed"] = True
assert t._menu_zapis() == "armed"
assert t._menu_perezapusk() == "armed"

print("\n=== 9. Wi-Fi: имя сети и переподключение ===")
with t.state_lock:
    t.app_state["armed"] = False
t._menu_wifi_kesh = ("", 0.0)
vyzovy_nm = []

def falsh_nmcli(argv, timeout=20):
    vyzovy_nm.append(list(argv))
    if argv[:2] == ["sudo", "-n"]:
        return 0, ""
    if "disconnect" in argv:
        return 1, "not authorized"
    return 0, ""

t._menu_nmcli = falsh_nmcli
t.time.sleep = lambda s: None
t._menu_wifi_ssid = lambda: "PodilOfficer"
t._menu_ekran = []
assert t._menu_wifi_zanovo() == "PodilOfficer"
assert t._menu_ekran[0] == "reconnecting"
assert t._menu_ekran[-1] == "PodilOfficer"
assert any(a[:2] == ["sudo", "-n"] and a[2].endswith("nmcli") for a in vyzovy_nm)
t._menu_otkryto = True
t._menu_put = []
t._menu_kursor = 0
t._menu_pravka = False
tools = [p for p in t._menu_derevo() if p["imya"] == "Tools"][0]
wifi = [p for p in tools["deti"] if p["imya"] == "Wi-Fi"][0]
deti = wifi["deti"]()
assert deti[0]["get"]() == "PodilOfficer"
print("    ", [p["imya"] for p in deti])

print("\n=== 11. Долгий упор листает меню, короткий — нет ===")
sbros()
t._menu_tick(6000.0, stiki(gaz=2000, tang=2000), False)
t._menu_tick(6000.2, stiki(), False)
t._menu_tick(6000.4, stiki(tang=1000), False)
t._menu_tick(6000.6, stiki(), False)
t._menu_tick(6000.8, stiki(tang=1000), False)
t._menu_tick(6001.0, stiki(), False)
t._menu_tick(6001.2, stiki(kren=2000), False)
assert t._menu_kursor == 0
t._menu_tick(6001.4, stiki(), False)
t._menu_tick(6001.6, stiki(tang=1000), False)
assert t._menu_kursor == 1, t._menu_kursor
t._menu_tick(6001.9, stiki(tang=1000), False)
assert t._menu_kursor == 1, "короткий упор сдвинул ещё раз"
t._menu_tick(6002.3, stiki(tang=1000), False)
assert t._menu_kursor == 2, t._menu_kursor
t._menu_tick(6002.9, stiki(tang=1000), False)
bystro = t._menu_kursor
t._menu_tick(6003.05, stiki(tang=1000), False)
assert t._menu_kursor == bystro + 1, (bystro, t._menu_kursor)

print("\n=== 10. Яркость в Tracker, выдержка не трогается ===")
sbros()
t._nastroiki_put = put
t._yarkost_shag = 0
t.BRIGHTNESS = 0.0
prinyato = []

class Cam:
    def set_controls(self, ctrl):
        prinyato.append(dict(ctrl))

t._picam2 = Cam()
t._menu_tick(5000.0, stiki(gaz=2000, tang=2000), False)
t._menu_tick(5000.2, stiki(), False)
t._menu_tick(5000.4, stiki(tang=1000), False)
assert [s[0] for s in t._menu_stroki() if s[1]] == ["Tracker"]
t._menu_tick(5000.6, stiki(), False)
t._menu_tick(5000.8, stiki(kren=2000), False)
t._menu_tick(5001.0, stiki(), False)
t._menu_tick(5001.2, stiki(kren=2000), False)
assert t._menu_pravka
t._menu_tick(5001.4, stiki(), False)
t._menu_tick(5001.6, stiki(tang=2000), False)
assert t._yarkost_shag == 1, t._yarkost_shag
assert t.BRIGHTNESS == 0.1
assert prinyato == [{"Brightness": 0.1}], prinyato
assert "AeEnable" not in open(put, encoding="utf-8").read()
assert "BRIGHTNESS = 0.1" in open(put, encoding="utf-8").read()

print("\n=== 13. Lock light замораживает выдержку и гасит автоматику ===")
class Svet:
    camera_controls = {"AeEnable": (0, 1, 1), "AwbEnable": (0, 1, 1)}
    def __init__(self):
        self.ctrl = []
    def set_controls(self, ctrl):
        self.ctrl.append(dict(ctrl))
    def capture_metadata(self):
        return {"ExposureTime": 4200, "AnalogueGain": 1.2,
                "ColourGains": (1.1, 1.4)}

cam = Svet()
t._picam2 = cam
t.BRIGHTNESS = 0.0
t.time.sleep = lambda s: None
with t.state_lock:
    t.app_state["armed"] = False
t._menu_ekran = []
assert t._menu_svet() == "locked 4ms"
assert cam.ctrl[0]["AeEnable"] is True
zamok = cam.ctrl[-1]
assert zamok["AeEnable"] is False
assert zamok["AwbEnable"] is False
assert zamok["ExposureTime"] == 4200
assert "Brightness" not in zamok
with t.state_lock:
    t.app_state["armed"] = True
assert t._menu_svet() == "armed"

print("\n=== 12. Статус гаснет сам, пока операция идёт — нет ===")
t._menu_ekran = ["cleared"]
t._menu_status = "cleared"
t._menu_status_t = 7000.0
t._menu_zanyato = True
t._menu_tick(7003.0, stiki(), False)
assert t._menu_ekran == ["cleared"]
t._menu_zanyato = False
t._menu_tick(7002.0, stiki(), False)
assert t._menu_status == "cleared"
t._menu_tick(7002.6, stiki(), False)
assert t._menu_status == "" and t._menu_ekran == []

print("\n=== 14. Выбор сети и значок в шапке ===")
import numpy as np
t._menu_wifi_seti = (["PodilOfficer", "iPhone"], t.time.monotonic())
t._menu_wifi_ssid = lambda: "iPhone"
punkty = t._menu_wifi_deti()
assert [p["imya"] for p in punkty] == [
    "Network", "PodilOfficer", "iPhone", "Reconnect"], [p["imya"] for p in punkty]
assert punkty[2]["get"]() == "on"
vyzovy = []

def nm_vybor(argv, timeout=20):
    vyzovy.append(list(argv))
    return 0, ""

t._menu_nmcli = nm_vybor
t.time.sleep = lambda s: None
with t.state_lock:
    t.app_state["armed"] = False
assert t._menu_wifi_podkluchit("PodilOfficer") == "iPhone"
assert any("connection" in a and "up" in a and "PodilOfficer" in a for a in vyzovy)

t._menu_otkryto = True
t._menu_put = []
t._menu_kursor = 0
t._menu_pravka = False
t._menu_ekran = []
t._menu_status = ""
t._menu_wifi_ssid = lambda: "PodilOfficer"
kadr = np.zeros((480, 720, 3), np.uint8)
t._narisovat_menu(kadr)
polosa = kadr[70:140, 500:700]
krasnyj = (polosa[:, :, 2] > 180) & (polosa[:, :, 1] < 80)
assert not krasnyj.any()
t._menu_wifi_ssid = lambda: "none"
pustoj = np.zeros((480, 720, 3), np.uint8)
t._narisovat_menu(pustoj)
polosa = pustoj[70:140, 500:700]
krasnyj = (polosa[:, :, 2] > 180) & (polosa[:, :, 1] < 80)
assert krasnyj.any()

print("\nOK: меню на земле, висение пишется")
