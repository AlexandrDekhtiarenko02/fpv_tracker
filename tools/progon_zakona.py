#!/usr/bin/env python3
"""Прогон закона по эталонам: каждая строка, все оси.

На каждой строке захвата считает команду текущего закона (PN + энергия)
и сравнивает со стиком пилота.

    python3 tools/progon_zakona.py <папка с захватами>
"""
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

HOVER = 1260.0


def progon(path):
    df = pd.read_csv(path)
    o = df[df.override == 1] if "override" in df.columns else df
    if len(o) < 30:
        return None
    dpp = t.CAMERA_VFOV_DEG / float(t.MAIN_H)
    # сброс состояния закона
    t._pn_bank_tek = 0.0
    t._pn_nedolet = 0.0
    t._filtr_los = {"los": None, "az": None, "t_los": None, "t_az": None}
    rows = []
    now = 1000.0
    for _, r in o.iterrows():
        now += 1.0 / t.NOMINAL_FPS
        alt = None if pd.isna(r.alt_cm) else r.alt_cm / 100.0
        tau = None if pd.isna(r.tau_s) else float(r.tau_s)
        sig = None if pd.isna(r.tau_sigma_s) else r.tau_sigma_s
        var = None if pd.isna(r.vario_cms) else r.vario_cms
        rng = None if pd.isna(r.range_m) else float(r.range_m)
        az = None if pd.isna(r.az_rate) else float(r.az_rate)
        los = None if pd.isna(r.los_rate) else float(r.los_rate)
        bank = 0.0 if pd.isna(r.fc_roll) else float(r.fc_roll)
        dx = 0.0 if pd.isna(r.dx_raw) else float(r.dx_raw)
        cy = None if pd.isna(r.box_cy) else r.box_cy
        bh = None if pd.isna(r.box_h) else r.box_h
        vc = (rng / tau) if (rng is not None and tau is not None and tau > 0.2) else None
        # крен
        kren = t._kren_na_kurs(
            az, now, bank, dx, now, 1.0,
            vc_mps=vc, range_m=rng, tau_s=tau)
        # тангаж
        los_glad = t._skorost_gladkaya(los if los is not None else 0.0, "los", now)
        ugol = t._tangazh_pn(
            los_glad, vc, rng,
            t._skolzyashiy(los_glad), None, tau)
        tang = t.PITCH_PWM_TO_FC_ANGLE_SIGN * ugol * t.PN_PITCH_PWM_PER_DEG
        # газ
        gaz = t._gaz_po_energii(HOVER, alt, tau, 20, sig, var, cy, bh, None)
        # рыскание
        rysk = t._ryaskan_na_kurs(bank, dx)
        rows.append({
            "range_m": rng,
            "tau_s": tau,
            "az_rate": az,
            "los_rate": los,
            "pilot_gaz": float(r.rc_t) if not pd.isna(r.rc_t) else HOVER,
            "gaz": gaz,
            "pilot_kren": (float(r.rc_r) - 1500) if not pd.isna(r.rc_r) else 0.0,
            "kren": kren,
            "pilot_tang": (float(r.rc_p) - 1500) if not pd.isna(r.rc_p) else 0.0,
            "tang": tang,
            "pilot_rysk": (float(r.rc_y) - 1500) if not pd.isna(r.rc_y) else 0.0,
            "rysk": rysk,
        })
    return pd.DataFrame(rows)


def osi(name, df):
    def mae(a, b):
        return (a - b).abs().mean()
    rul = df[df.pilot_kren.abs() > 5]
    print(f"{name:40s}  rows={len(df):4d}  "
          f"gaz_mae={mae(df.gaz, df.pilot_gaz):5.1f}  "
          f"kren_mae={mae(df.kren, df.pilot_kren):5.1f}  "
          f"tang_mae={mae(df.tang, df.pilot_tang):5.1f}  "
          f"rul_rows={len(rul):3d}")


def main():
    if len(sys.argv) < 2:
        print("usage: progon_zakona.py <dir>")
        return 1
    paths = []
    for root, _, files in os.walk(sys.argv[1]):
        for f in files:
            if f.endswith(".csv") and "строки" not in f and "flight_" in f:
                paths.append(os.path.join(root, f))
    paths.sort()
    if not paths:
        # fallback: any csv
        for root, _, files in os.walk(sys.argv[1]):
            for f in files:
                if f.endswith(".csv"):
                    paths.append(os.path.join(root, f))
        paths.sort()
    for p in paths:
        df = progon(p)
        if df is not None:
            osi(os.path.basename(p), df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
