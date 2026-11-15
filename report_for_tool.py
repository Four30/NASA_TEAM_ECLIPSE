"""
Reads the CSV from the logger's SD card (columns: time, ntu, mv, device),
runs a seasonal Mann-Kendall test, and writes report.html with the graph
and the result.

Install:  pip install pandas numpy matplotlib pymannkendall
Run:      python make_report.py turbidity.csv
"""
import base64
import io
import math
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymannkendall as mk

HTML_FILE = "report.html"
MIN_MONTHS = 24          # need at least this many monthly values to run the test


def seasonal_mk(series, alpha=0.05):
    """Seasonal Mann-Kendall using pymannkendall. series: monthly values, one per month."""
    res = mk.seasonal_test(series, period=12, alpha=alpha)
    return {"trend": res.trend, "p": res.p, "z": res.z,
            "slope_per_year": float(res.slope) * 12}      # slope is per month, x12 = per year


def seasonal_mk_manual(series, alpha=0.05):
    """Hand-written seasonal Mann-Kendall (Hirsch et al. 1982), used only as a cross-check.
    Each calendar month is a season; missing months (NaN) keep their season."""
    s = series.dropna()
    S, var_S, slopes = 0.0, 0.0, []
    for month in range(1, 13):
        v = s[s.index.month == month]
        x, yrs = v.values, v.index.year.values
        n = len(x)
        if n < 2:
            continue
        for i in range(n - 1):
            for j in range(i + 1, n):
                S += np.sign(x[j] - x[i])
                slopes.append((x[j] - x[i]) / (yrs[j] - yrs[i]))
        _, counts = np.unique(x, return_counts=True)
        var_S += (n * (n - 1) * (2 * n + 5)
                  - sum(c * (c - 1) * (2 * c + 5) for c in counts)) / 18
    if var_S == 0:
        return None
    z = 0.0 if S == 0 else (S - np.sign(S)) / math.sqrt(var_S)
    p = math.erfc(abs(z) / math.sqrt(2))
    trend = "no significant trend"
    if p < alpha:
        trend = "increasing" if z > 0 else "decreasing"
    return {"trend": trend, "p": p, "z": z, "S": S,
            "slope_per_year": float(np.median(slopes)) if slopes else float("nan")}


def build_report(csv_file, html_file=HTML_FILE):
    df = pd.read_csv(csv_file)
    df["time"] = pd.to_datetime(df["time"], format="mixed")
    df = df.set_index("time").sort_index()
    monthly = df["ntu"].resample("MS").median()
    n_months = int(monthly.notna().sum())

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.scatter(df.index, df["ntu"], s=14, alpha=0.4, label="Readings")
    ax.plot(monthly.index, monthly, "k-o", ms=3, lw=1.5, label="Monthly median")
    ax.set_xlabel("Date")
    ax.set_ylabel("Turbidity (NTU)")
    ax.set_title("River turbidity (ESP32-S3 logger)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    res = seasonal_mk(monthly) if n_months >= MIN_MONTHS else None
    if res:
        check = seasonal_mk_manual(monthly)
        if check and abs(check["p"] - res["p"]) > 0.01:
            print(f"WARNING: pymannkendall p={res['p']:.4f} but manual check p={check['p']:.4f}. "
                  "Missing months may be misaligning seasons in pymannkendall.")
        else:
            print("Cross-check with manual implementation: agrees.")
        smk = (f"<tr><th>Trend</th><td>{res['trend']}</td></tr>"
               f"<tr><th>p-value</th><td>{res['p']:.4f}</td></tr>"
               f"<tr><th>Z</th><td>{res['z']:.3f}</td></tr>"
               f"<tr><th>Sen's slope</th><td>{res['slope_per_year']:+.3f} NTU/year</td></tr>")
    else:
        smk = (f"<tr><td colspan=2>Collecting data: {n_months} of {MIN_MONTHS} "
               f"monthly values needed for the seasonal Mann-Kendall test.</td></tr>")

    rows = "".join(f"<tr><td>{t:%Y-%m-%d %H:%M}</td><td>{r.ntu:.1f}</td></tr>"
                   for t, r in df.tail(5).iloc[::-1].iterrows())
    made = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>River turbidity report</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:900px;margin:1.5rem auto;padding:0 1rem}}
 img{{max-width:100%;height:auto}} table{{border-collapse:collapse;margin:1rem 0}}
 th,td{{border:1px solid #8884;padding:.4rem .8rem;text-align:left}}
</style></head><body>
<h1>River turbidity report</h1><p>Report generated: {made}</p>
<img src="data:image/png;base64,{img}" alt="Turbidity time series">
<h2>Seasonal Mann-Kendall (12 monthly seasons)</h2><table>{smk}</table>
<h2>Latest readings</h2><table><tr><th>Time</th><th>NTU</th></tr>{rows}</table>
</body></html>"""
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print("Wrote", html_file)


if __name__ == "__main__":
    build_report(sys.argv[1] if len(sys.argv) > 1 else "turbidity.csv")
