#!/usr/bin/env python3
"""Прогон закона наведения по эталонным логам.

Берёт CSV захвата, на каждой строке подаёт в закон то, что видел борт,
и сравнивает команду со стиком пилота. Без полёта и без симулятора.

    python3 tools/progon_zakona.py <папка с захватами>
"""
import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# На ПК нет камеры и порта. Подменяем, чтобы импортировать закон.
import types
for _name in ("picamera2", "libcamera", "serial"):
    _m = types.ModuleType(_name)
    _m.Picamera2 = _m.Preview = _m.MappedArray = _m.Transform = object
    _m.Serial = object
    sys.modules[_name] = _m
import tracker as t


def progon(path):
    df = pd.read_csv(path)
    o = df[df.override == 1] if "override" in df.columns else df
    if len(o) < 30:
        return None
    dpp = t.CAMERA_VFOV_DEG / float(t.MAIN_H)
    n = 0
    zapas_sum = 0.0
    gaz_sum = 0.0
    pilot_gaz_sum = 0.0
    nedolet = 0
    vcel = 0
    net = 0
    for _, r in o.iterrows():
        alt = None if pd.isna(r.alt_cm) else r.alt_cm / 100.0
        tau = None if pd.isna(r.tau_s) else r.tau_s
        sig = None if pd.isna(r.tau_sigma_s) else r.tau_sigma_s
        var = None if pd.isna(r.vario_cms) else r.vario_cms
        ubyv = None if pd.isna(r.tau_ubyv) else r.tau_ubyv
        cy = None if pd.isna(r.box_cy) else r.box_cy
        bh = None if pd.isna(r.box_h) else r.box_h
        z = t._nedolet_deg(alt, tau, 20, sig, var, cy, bh, ubyv)
        if z is None:
            net += 1
            z = 0.0
        elif z > 0.5:
            nedolet += 1
        else:
            vcel += 1
        t._pn_nedolet = z
        aim = 0.0 if pd.isna(r.dy_raw) else -r.dy_raw * dpp
        base = r.base_thr if not pd.isna(r.base_thr) else 1300.0
        gaz = t._gaz_ot_zapasa(aim, base)
        zapas_sum += z
        gaz_sum += gaz
        pilot_gaz_sum += r.rc_t if not pd.isna(r.rc_t) else base
        n += 1
    return {
        "n": n,
        "zapas": zapas_sum / n,
        "nedolet": 100 * nedolet / n,
        "vcel": 100 * vcel / n,
        "net": 100 * net / n,
        "gaz": gaz_sum / n,
        "pilot": pilot_gaz_sum / n,
    }


def main():
    if len(sys.argv) < 2:
        print("usage: progon_zakona.py <папка>")
        return 1
    files = sorted(glob.glob(os.path.join(sys.argv[1], "*/строки.csv")))
    if not files:
        files = sorted(glob.glob(os.path.join(sys.argv[1], "*.csv")))
    print(f"{'захват':<22s} {'запас':>6s} {'недолет':>8s} {'в цель':>7s} "
          f"{'нет tau':>8s} {'газ':>6s} {'пилот':>7s}")
    for f in files:
        r = progon(f)
        if r is None:
            continue
        name = os.path.basename(os.path.dirname(f))[-18:]
        print(f"{name:<22s} {r['zapas']:6.1f} {r['nedolet']:7.0f}% "
              f"{r['vcel']:6.0f}% {r['net']:7.0f}% {r['gaz']:6.0f} {r['pilot']:7.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
