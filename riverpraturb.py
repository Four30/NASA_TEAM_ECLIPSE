import re
import earthaccess
import numpy as np
import pandas as pd
import rioxarray as rxr
import matplotlib.pyplot as plt
import pymannkendall as mk

# Box from your coordinates (lat, lon) pairs; longitudes are WEST so they are negative.
# Format: (west, south, east, north)
BBOX = (-1.568, 5.585, -1.545, 5.613)
START, END = "2017-01-01", "2026-11-01"
PRODUCTS = ["HLSL30", "HLSS30"]


# ---------- 1. PULL DATA ----------

def search(short_name):
    return earthaccess.search_data(
        short_name=short_name,
        bounding_box=BBOX,
        temporal=(START, END),
        cloud_cover=(0, 60),
    )


def pick(links, band):
    return next(l for l in links if l.endswith(f".{band}.tif"))


def read_band(file_obj):
    da = rxr.open_rasterio(file_obj, masked=True).squeeze()
    return da.rio.clip_box(*BBOX, crs="EPSG:4326")   # read only the small window


def process_granule(granule):
    name = granule["umm"]["GranuleUR"]
    links = granule.data_links()
    swir_band= "B06" if ".L30" in name else "B11"

    green_f, red_f, swir_f, fmask_f = earthaccess.open(
        [pick(links, "B03"),pick(links, "B04"), pick(links, swir_band), pick(links, "Fmask")]
    )
    green, red, swir, fmask = (read_band(f) for f in (green_f, red_f, swir_f, fmask_f))
    
    fm = fmask.fillna(255).astype("uint8")
    bad = (fm & 0b00100000)>0
    mndwi = (green - swir) / (green + swir)
    valid = (mndwi > 0.0) & ~bad

    print(name, "fmask water:", int(((fm & 0b00100000)>0).sum()),
        "mndwi water:", int((mndwi > 0).sum()), "bad:", int(bad.sum()))
    
    if int(valid.sum())< 10:
        return None

    g = green.where(valid) * 0.0001
    r = red.where(valid) * 0.0001
    ndti = (r-g)/(r+g)

    m = re.search(r"\.(\d{4})(\d{3})T", name)          # e.g. ...2023045T102021...
    date = pd.to_datetime(f"{m.group(1)}-{m.group(2)}", format="%Y-%j")
    return {"date": date, "sensor": name.split(".")[1],
            "red_refl": float(r.median()), "ndti": float(ndti.median()),
            "n_pixels": int(valid.sum())}


def pull_hls():
    earthaccess.login()
    rows = []
    for product in PRODUCTS:
        granules = search(product)
        print(f"{product}: {len(granules)} granules")
        for gr in granules:
            try:
                row = process_granule(gr)
                if row:
                    rows.append(row)
            except Exception as e:
                print("skip:", e)
                import traceback
                traceback.print_exc()
                break
    df = pd.DataFrame(rows).sort_values("date").set_index("date")
    df.to_csv("pra_hls_turbidity.csv")
    df = df[df["n_pixels"]>= 100]
    med = df["red_refl"].median()
    mad = (df["red_refl"] - med).abs().median()
    out = df[df["red_refl"]> med + 3 * 1.4826 * mad]
    print("Dropped as outliers: \n", out[["sensor", "red_refl", "n_pixels"]])
    df = df.drop(out.index)
    return df


# ---------- 2. TIME SERIES ----------

def plot_series(df, column="red_refl", ylabel="Red reflectance (turbidity proxy)"):
    fig, ax = plt.subplots(figsize=(10, 4))
    for sensor, sub in df.groupby("sensor"):
        label = "Landsat (HLSL30)" if sensor == "L30" else "Sentinel-2 (HLSS30)"
        ax.scatter(sub.index, sub[column], s=18, label=label)
    m = df[column].resample("MS").median()
    ax.plot(m.index, m, color = "k", lw = 1.5, marker = "o", ms=3, label= "Monthly median")
    ax.set_xlabel("Date")
    ax.set_ylabel(ylabel)
    ax.set_title("Pra River turbidity proxy (HLS)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig("pra_hls_timeseries.png", dpi=150)
    plt.show()

def run_smk(df, column="red_refl", sensor= None):
    d = df if sensor is None else df[df["sensor"]==sensor]
    s = d[column].resample("QS-DEC").median()
    print(F"\n Seasonal Mann-Kendall ({sensor or 'all sensors'})")
    print(s.notna().sum(), "of", len(s), "seasons have data")
    if s.notna().sum()<8:
        print("Too few seasons for a reliable test")
        return
    res = mk.seasonal_test(s, period=4, alpha=0.05)
    print(res)
    print("Sen's slope per year:", res.slope * 4)


if __name__ == "__main__":
    data = pull_hls()
    run_smk(data)
    run_smk(data, sensor = "S30")

    plot_series(data, "red_refl")