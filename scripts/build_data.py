"""Build the site's JSON data from the audited poster materials and TLC taxi-zone shapes.

Inputs (from a local UrbanPulse checkout, see README):
  <datamining>/materials_v2/tables/*.csv and evidence.json  (run materials_v2/source/export_figures.py first)
  <datamining>/data/raw/reference/taxi_zones/taxi_zones/taxi_zones.{shp,dbf}

Usage:
  python scripts/build_data.py [--datamining PATH]
Defaults to the parent folder of this repository, or $URBANPULSE_ROOT.
Only aggregates are written; no trip-level records leave the analysis checkout.
"""
from pathlib import Path
import argparse
import json
import os
import struct

import numpy as np
import pandas as pd

SITE = Path(__file__).resolve().parents[1]
OUT = SITE / "data"

FIELDS = {"日期": "Day", "时段": "Time", "上车行政区": "Pickup", "下车行政区": "Drop-off",
          "距离": "Distance", "费用": "Total", "支付": "Payment", "乘客": "Passengers"}
VALUES = {
    "Day": {"工作日": "weekday", "周末": "weekend"},
    "Time": {"凌晨(00-05)": "00:00–05:59", "早高峰(06-09)": "06:00–09:59", "白天(10-15)": "10:00–15:59",
             "晚高峰(16-19)": "16:00–19:59", "夜间(20-23)": "20:00–23:59"},
    "Distance": {"零": "0 mi", "短途(<2mi)": "< 2 mi", "中途(2-5mi)": "2–5 mi", "长途(5-15mi)": "5–15 mi",
                 "超长途(>=15mi)": "≥ 15 mi"},
    "Total": {"负值": "negative", "低(<$15)": "< $15", "中($15-30)": "$15–30", "高($30-75)": "$30–75",
              "极高(>=$75)": "≥ $75"},
    "Payment": {"信用卡": "credit card", "现金": "cash", "免付": "no charge", "争议": "dispute",
                "未知": "unknown", "作废": "voided", "其他/缺失": "other / missing", "字段缺失": "field missing"},
    "Passengers": {"缺失": "missing", "零": "0", "1": "1", "2": "2", "3+": "3+"},
}
QUALITY = {
    "missing_time": "Missing or unparseable time", "invalid_duration": "Non-positive duration",
    "outside_month": "Pickup outside January 2025", "invalid_distance": "Missing or invalid distance",
    "missing_amount": "Missing or invalid amount", "unknown_pickup": "Unknown pickup zone",
    "unknown_dropoff": "Unknown drop-off zone", "zero_distance": "Zero distance",
    "negative_amount": "Negative total amount", "high_amount": "Total above $300",
    "long_duration": "Duration above 240 min", "high_speed": "Average speed above 100 mph",
    "missing_passengers": "Missing passenger count",
}
MODELS = {"lag_24": "Previous day (same hour)", "lag_168": "Previous week (same hour)",
          "hist_gradient_boosting_poisson": "Poisson gradient boosting"}


def item_label(item):
    field, value = item.split("=", 1)
    name = FIELDS[field]
    if name in ("Pickup", "Drop-off"):
        return name, value
    return name, VALUES[name][value]   # KeyError means a new category needs a label


def read_shapefile(base):
    """Minimal ESRI polygon shapefile + dBASE reader (same as the poster's export script)."""
    dbf = base.with_suffix(".dbf").read_bytes()
    count, header_len, record_len = struct.unpack("<IHH", dbf[4:12])
    fields, i = [], 32
    while dbf[i] != 0x0D:
        fields.append((dbf[i:i + 11].split(b"\0")[0].decode(), dbf[i + 16]))
        i += 32
    records = []
    for r in range(count):
        offset, rec = header_len + r * record_len + 1, {}
        for name, length in fields:
            rec[name] = dbf[offset:offset + length].decode("latin-1").strip()
            offset += length
        records.append(rec)
    shp, shapes, pos = base.with_suffix(".shp").read_bytes(), [], 100
    while pos < len(shp):
        _, words = struct.unpack(">ii", shp[pos:pos + 8])
        content = shp[pos + 8:pos + 8 + 2 * words]
        pos += 8 + 2 * words
        if struct.unpack("<i", content[:4])[0] != 5:
            shapes.append([])
            continue
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", content[44:44 + 4 * n_parts])) + [n_points]
        start = 44 + 4 * n_parts
        pts = np.frombuffer(content[start:start + 16 * n_points], dtype="<f8").reshape(-1, 2)
        shapes.append([pts[parts[k]:parts[k + 1]] for k in range(n_parts)])
    return records, shapes


def simplify(ring, tol):
    """Douglas-Peucker simplification; tol in map units (feet)."""
    if len(ring) < 5:
        return ring
    keep = np.zeros(len(ring), bool)
    keep[[0, -1]] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = ring[i], ring[j]
        seg = b - a
        norm = np.hypot(*seg)
        pts = ring[i + 1:j]
        dist = (np.hypot(*(pts - a).T) if norm == 0 else
                np.abs(seg[0] * (pts[:, 1] - a[1]) - seg[1] * (pts[:, 0] - a[0])) / norm)
        k = int(dist.argmax())
        if dist[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return ring[keep]


def write(name, obj):
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.relative_to(SITE)}  {path.stat().st_size / 1024:.0f} KB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--datamining", type=Path,
                        default=Path(os.environ.get("URBANPULSE_ROOT", SITE.parent)))
    root = parser.parse_args().datamining.resolve()
    mat = root / "materials_v2"
    tables = mat / "tables"
    E = json.loads((mat / "evidence.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    N = int(E["clean"]["records"])

    # Summary: headline numbers and provenance (no local paths).
    case = E["case"]
    fc = E["forecast"]
    summary = {
        "month": E["month"], "run_id": Path(E["run"]).name, "git_commit": E["git_commit"],
        "generated_at_utc": E["generated_at_utc"], "report_sha256": E["report_sha256"],
        "raw_records": E["raw"]["records"], "clean_records": N, "retention": E["retention"],
        "raw_stats": E["raw"], "clean_stats": E["clean"],
        "unique_baskets": E["patterns"]["unique_baskets"], "rules": E["patterns"]["rules"],
        "frequent_itemsets": E["patterns"]["frequent_itemsets"],
        "default_display_rules": E["patterns"]["default_display_rules"],
        "pattern_config": E["patterns"]["config"],
        "algorithms_identical": E["patterns"]["algorithms"]["identical"],
        "manhattan_pickup_share": E["manhattan_pickup_share"],
        "manhattan_internal_share": E["top_borough_od_share"],
        "airport_pickup_share": E["airport_pickup_share"],
        "airport_pickup_share_total": E["airport_pickup_share_total"],
        "top_pickup_zone": E["top_pickup_zone"],
        "midnight_ratio": E["midnight_weekend_to_weekday_ratio"],
        "midnight_daily_average": E["midnight_daily_average"],
        "peak_hours": {k: {"hour": int(v["hour"]), "daily_average": v["daily_average"]}
                       for k, v in E["peak_hours"].items()},
        "case": {"antecedent": case["antecedent_en"], "consequent": case["consequent_en"],
                 "confidence": case["confidence"], "baseline": case["consequent_support"],
                 "lift": case["lift"], "support": case["support"], "support_count": int(case["support_count"])},
        "case_recount_matches": E["case_independent_counts"]["support_count"] == int(case["support_count"]),
        "selection": E["selection"],
        "forecast": {"improvement_vs_previous_week": fc["test_wape_improvement_vs_lag_168"],
                     "splits": fc["splits"], "split_rows": fc["split_rows"],
                     "model": {"name": fc["model"]["name"], "loss": fc["model"]["loss"],
                               "params": fc["model"]["selected_params"]},
                     "features": len(fc["feature_columns"]), "lags": fc["config"]["lags"]},
        "sources": [{k: f[k] for k in ("file", "url", "bytes", "sha256")} for f in E["source_manifest"]["files"]]
                   + [{"file": "taxi_zones.zip", "url": E["taxi_zone_shapes"]["source"], "bytes": None,
                       "sha256": E["taxi_zone_shapes"]["sha256"]}],
        "source_page": E["source_manifest"]["source_page"],
        "dictionary_url": E["source_manifest"]["dictionary_url"],
        "planned_not_completed": E["planned_not_completed"],
    }
    write("summary", summary)

    # Zones: simplified SVG geometry plus pickups, drop-offs, and top links.
    od = pd.read_csv(tables / "zone_od_ranked.csv")
    pickups = od.groupby("pickup_zone_id").trips.sum()
    dropoffs = od.groupby("dropoff_zone_id").trips.sum()
    assert int(pickups.sum()) == int(dropoffs.sum()) == N
    names = dict(zip(od.pickup_zone_id, od.pickup_zone)) | dict(zip(od.dropoff_zone_id, od.dropoff_zone))
    records, shapes = read_shapefile(root / "data/raw/reference/taxi_zones/taxi_zones/taxi_zones")
    geo = [(rec, rings) for rec, rings in zip(records, shapes) if rings and rec["borough"] != "EWR"]
    pts = np.vstack([r for _, rings in geo for r in rings])
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    scale = (x1 - x0) / 1000.0
    view = [0, 0, 1000, round((y1 - y0) / scale, 1)]
    paths, boroughs = {}, {}
    for rec, rings in geo:
        zid = int(rec["LocationID"])
        boroughs[zid] = rec["borough"]
        parts = []
        for ring in rings:
            ring = simplify(ring, scale * 0.35)
            if len(ring) < 4:
                continue
            xy = np.column_stack([(ring[:, 0] - x0) / scale, (y1 - ring[:, 1]) / scale]).round(1)
            parts.append("M" + " ".join(f"{x:g},{y:g}" for x, y in xy) + "Z")
        paths[zid] = paths.get(zid, "") + "".join(parts)
    lookup = pd.read_csv(root / "data/raw" / E["month"] / "taxi_zone_lookup.csv")
    borough_of = dict(zip(lookup.LocationID, lookup.Borough)) | boroughs

    def top_links(frame, key, other, k=6):
        out = {}
        for zid, grp in frame.groupby(key):
            g = grp.nlargest(k, "trips")
            out[int(zid)] = [[int(z), int(t)] for z, t in zip(g[other], g.trips)]
        return out
    to = top_links(od, "pickup_zone_id", "dropoff_zone_id")
    frm = top_links(od, "dropoff_zone_id", "pickup_zone_id")
    zones = []
    for zid in sorted(set(pickups.index) | set(dropoffs.index) | set(paths)):
        zones.append({"id": int(zid), "name": names.get(zid, lookup.set_index("LocationID").Zone.get(zid, "")),
                      "borough": borough_of.get(zid, ""), "pickups": int(pickups.get(zid, 0)),
                      "dropoffs": int(dropoffs.get(zid, 0)), "to": to.get(int(zid), []),
                      "from": frm.get(int(zid), []), "d": paths.get(zid)})
    write("zones", {"viewBox": view, "total": N, "omitted_from_map": ["EWR"], "zones": zones})

    # Borough origin-destination matrix.
    bod = pd.read_csv(tables / "borough_od.csv")
    order = [b for b in ["Manhattan", "Queens", "Brooklyn", "Bronx", "Staten Island", "EWR"]
             if b in set(bod.pickup_borough) | set(bod.dropoff_borough)]
    m = bod.pivot(index="pickup_borough", columns="dropoff_borough", values="trips").reindex(
        index=order, columns=order).fillna(0).astype(int)
    write("boroughs", {"order": order, "matrix": m.values.tolist(), "total": N})

    # Time: hourly averages by day type and daily counts.
    hourly = pd.read_csv(tables / "hourly_day_type.csv")
    daily = pd.read_csv(tables / "daily_counts.csv")
    kinds = {"工作日": "weekday", "周末": "weekend"}
    time = {"hourly": {kinds[k]: {"calendar_days": int(g.calendar_days.iloc[0]),
                                  "daily_average": g.sort_values("hour").daily_average.round(1).tolist(),
                                  "trips": g.sort_values("hour").trips.astype(int).tolist()}
                       for k, g in hourly.groupby("day_type")},
            "daily": [[d, int(t)] for d, t in zip(daily.date, daily.trips)]}
    write("time", time)

    # Rules: compact arrays over an item vocabulary.
    rules = pd.read_csv(tables / "association_rules.csv")
    vocab, index = [], {}

    def ids(text):
        out = []
        for item in text.split("；"):
            if item not in index:
                index[item] = len(vocab)
                vocab.append(list(item_label(item)))
            out.append(index[item])
        return out
    rows = [[ids(r.antecedent), ids(r.consequent), int(r.n11), int(r.antecedent_count), int(r.consequent_count),
             round(float(r.confidence), 6), round(float(r.lift), 4), round(float(r.kulc), 4),
             round(float(r.imbalance_ratio), 4)] for r in rules.itertuples()]
    selected = pd.read_csv(tables / "selected_rules.csv")
    highlight = [[ids(r.antecedent), ids(r.consequent)] for r in selected.itertuples()]
    write("rules", {"N": N, "fields": ["antecedent", "consequent", "n11", "nA", "nB", "confidence", "lift",
                                       "kulc", "ir"],
                    "items": vocab, "rules": rows, "highlight": highlight,
                    "config": E["patterns"]["config"], "metric_policy": E["patterns"]["metric_policy"]})

    # Forecast and quality.
    fm = pd.read_csv(tables / "forecast_test_metrics.csv")
    write("forecast", {"metrics": [{"model": MODELS[r.model], "key": r.model, "rows": int(r.rows),
                                    "mae": r.mae, "wape": r.wape, "rmse": r.rmse} for r in fm.itertuples()]})
    q = pd.read_csv(tables / "quality_flags.csv")
    write("quality", {"raw_records": E["raw"]["records"],
                      "flags": [{"key": key, "label": QUALITY[key], "records": int(n), "share": float(s),
                                 "excluded": bool(x)}
                                for key, n, s, x in zip(q.key, q["记录数"], q["占原始窗口比例"], q["默认排除"])]})


if __name__ == "__main__":
    main()
