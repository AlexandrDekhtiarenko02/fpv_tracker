#!/usr/bin/env python3
"""Разбор закона по записанному заходу: С/Ш, слепое время, насыщение, рывки.

Считает то, что иначе видно только в ручном разборе:

  - С/Ш оценки λ̇: настоящая скорость линии (по изменению depression)
    против шума оценщика;
  - слепое время: доля кадров, где закон молчит (pitch_off ≈ 0 при
    ненулевой ошибке);
  - насыщение: доля кадров в упоре;
  - рывки: СКЗ и число скачков команды >20 PWM за кадр;
  - залипание λ̇: серии одинаковых значений.

Запуск:
    python3 tools/razbor_zakona.py <папка с захватами или csv>
"""
import os
import sys
import glob

import numpy as np
import pandas as pd


def razbor(path):
    df = pd.read_csv(path, low_memory=False)
    o = df[df.override == 1].reset_index(drop=True) if "override" in df.columns else df
    if len(o) < 30:
        return None
    out = {"file": os.path.basename(path), "rows": len(o)}

    # С/Ш λ̇: тренд по окну 1 с против остатка
    if "los_rate" in o.columns and "depression_deg" in o.columns:
        dep = o.depression_deg.interpolate()
        t = o.t if "t" in o.columns else pd.Series(np.arange(len(o)) * 0.033)
        # настоящая скорость: наклон по 1 с
        true = []
        for i in range(len(o)):
            m = (t - t.iloc[i]).abs() < 0.5
            if m.sum() >= 5:
                xs = t[m].values
                ys = dep[m].values
                if xs.std() > 0.05:
                    k = np.polyfit(xs - xs.mean(), ys - ys.mean(), 1)[0]
                    true.append(k)
                else:
                    true.append(np.nan)
            else:
                true.append(np.nan)
        true = np.array(true)
        noise = o.los_rate.values - true
        sig = np.nanstd(true)
        noi = np.nanstd(noise)
        out["snr"] = sig / noi if noi > 0.01 else np.nan
        out["true_rms"] = sig
        out["noise_rms"] = noi

    # слепое время: pitch_off ≈ 0
    if "pitch_off" in o.columns:
        out["blind_pct"] = (o.pitch_off.abs() < 1).mean() * 100
        out["sat_pct"] = (o.pitch_off.abs() >= 29).mean() * 100
        d = o.pitch_off.diff().abs()
        out["step_rms"] = d.std()
        out["steps_gt20"] = (d > 20).sum()

    # залипание λ̇
    if "los_rate" in o.columns:
        same = (o.los_rate.diff().abs() < 0.01)
        # серии
        runs = same.astype(int).groupby((~same).cumsum()).sum()
        out["stuck_max_frames"] = runs.max() if len(runs) else 0
        out["stuck_pct"] = same.mean() * 100

    return out


def main():
    if len(sys.argv) < 2:
        print("usage: razbor_zakona.py <dir or csv>")
        return 1
    paths = []
    arg = sys.argv[1]
    if os.path.isdir(arg):
        for root, _, files in os.walk(arg):
            for f in files:
                if f.endswith(".csv"):
                    paths.append(os.path.join(root, f))
    else:
        paths = [arg]
    paths.sort()
    rows = []
    for p in paths:
        r = razbor(p)
        if r:
            rows.append(r)
    if not rows:
        print("нет данных")
        return 1
    df = pd.DataFrame(rows)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 120)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print()
    if "snr" in df.columns:
        print(f"С/Ш медиана: {df.snr.median():.2f}")
    if "blind_pct" in df.columns:
        print(f"слепое время медиана: {df.blind_pct.median():.0f}%")
    if "sat_pct" in df.columns:
        print(f"в упоре медиана: {df.sat_pct.median():.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
