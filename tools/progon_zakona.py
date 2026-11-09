#!/usr/bin/env python3
"""Прогон закона по эталонам: каждая строка, все оси.

На каждой строке захвата считает команду закона на тангаж, газ, крен
и рыскание и сравнивает со стиком пилота на той же строке.

    python3 tools/progon_zakona.py <папка с захватами>
"""
import glob
import os
import sys
import types

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _name in ("picamera2", "libcamera", "serial"):
    _m = types.ModuleType(_name)
    _m.Picamera2 = _m.Preview = _m.MappedArray = _m.Transform = object
    _m.Serial = object
    sys.modules[_name] = _m
import tracker as t

# Висен ие этого борта: медиана газа на ближних заходах, где нос на цели.
HOVER = 1260.0


def progon(path):
    df = pd.read_csv(path)
    o = df[df.override == 1] if "override" in df.columns else df
    if len(o) < 30:
        return None
    dpp = t.CAMERA_VFOV_DEG / float(t.MAIN_H)
    t._pn_baza = None
    t._pn_nedolet = 0.0
    t._pn_bank_i = 0.0
    t._pn_az_i = 0.0
    t._pn_bank_tek = 0.0
    rows = []
    now = 1000.0
    for _, r in o.iterrows():
        now += 1.0 / t.NOMINAL_FPS
        if t._pn_baza is None and not pd.isna(r.range_m):
            t._pn_baza = t.PN_BAZA_DEG if r.range_m > t.PN_BAZA_RANGE_M else 0.0
        alt = None if pd.isna(r.alt_cm) else r.alt_cm / 100.0
        tau = None if pd.isna(r.tau_s) else r.tau_s
        sig = None if pd.isna(r.tau_sigma_s) else r.tau_sigma_s
        var = None if pd.isna(r.vario_cms) else r.vario_cms
        ubyv = None if pd.isna(r.tau_ubyv) else r.tau_ubyv
        cy = None if pd.isna(r.box_cy) else r.box_cy
        bh = None if pd.isna(r.box_h) else r.box_h
        z = t._nedolet_deg(alt, tau, 20, sig, var, cy, bh, ubyv)
        t._pn_nedolet = 0.0 if z is None else z
        zapas = t._zapas_nosa()
        aim = 0.0 if pd.isna(r.dy_raw) else -float(r.dy_raw) * dpp
        gaz = t._gaz_ot_zapasa(aim, HOVER)
        dx = 0.0 if pd.isna(r.dx_raw) else float(r.dx_raw)
        az = 0.0 if pd.isna(r.az_rate) else float(r.az_rate)
        bank = 0.0 if pd.isna(r.fc_roll) else float(r.fc_roll)
        kren = t._kren_na_kurs(az, now, bank, dx, now, 1.0)
        rysk = t._ryaskan_na_kurs(bank, dx)
        rows.append({
            "aim": aim, "zapas": zapas,
            "pilot_gaz": float(r.rc_t) if not pd.isna(r.rc_t) else HOVER,
            "gaz": gaz,
            "pilot_kren": (float(r.rc_r) - 1500) if not pd.isna(r.rc_r) else 0.0,
            "kren": kren,
            "pilot_rysk": (float(r.rc_y) - 1500) if not pd.isna(r.rc_y) else 0.0,
            "rysk": rysk,
        })
    return pd.DataFrame(rows)


def osi(name, df):
    def mae(a, b):
        return (a - b).abs().mean()
    print(f"\n{name}  строк {len(df)}")
    print(f"  тангаж  пилот {df.aim.mean():6.1f}°  закон {df.zapas.mean():6.1f}°  "
          f"ошибка {mae(df.zapas, df.aim):5.1f}°")
    print(f"  газ     пилот {df.pilot_gaz.mean():6.0f}   закон {df.gaz.mean():6.0f}   "
          f"ошибка {mae(df.gaz, df.pilot_gaz):5.0f}")
    print(f"  крен    пилот {df.pilot_kren.mean():6.1f}   закон {df.kren.mean():6.1f}   "
          f"ошибка {mae(df.kren, df.pilot_kren):5.1f}")
    print(f"  рыскание пилот {df.pilot_rysk.mean():6.1f}   закон {df.rysk.mean():6.1f}   "
          f"ошибка {mae(df.rysk, df.pilot_rysk):5.1f}")


def main():
    if len(sys.argv) < 2:
        print("usage: progon_zakona.py <папка>")
        return 1
    files = sorted(glob.glob(os.path.join(sys.argv[1], "*/строки.csv")))
    vse = []
    for f in files:
        df = progon(f)
        if df is None:
            continue
        name = os.path.basename(os.path.dirname(f))[-18:]
        osi(name, df)
        vse.append(df)
    if vse:
        osi("ИТОГО", pd.concat(vse, ignore_index=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
