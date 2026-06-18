
import re
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def parse_flight_time_to_minutes(s):
    if pd.isna(s):
        return None
    s = str(s).strip()
    if not s:
        return None
    m = re.match(r"(?:(\d+)h)?(?:(\d+)m)?", s)
    if not m:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    mm = int(m.group(2)) if m.group(2) else 0
    return h * 60 + mm


def main():
    raw_dir = ROOT / "data" / "1_raw" / "flight_list"
    files = sorted(raw_dir.glob("*.csv"))
    
    if not files:
        print("No flight_list CSV files found in", raw_dir)
        return
    
    print(f"Found {len(files)} CSV files. Merging...")
    
    # Read and merge all CSVs
    dfs = []
    for p in files:
        try:
            df = pd.read_csv(p)
            df.columns = [c.strip() for c in df.columns]
            dfs.append(df)
            print(f"  Loaded: {p.name} ({len(df)} rows)")
        except Exception as e:
            print(f"  ERROR reading {p.name}: {e}")
            continue
    
    if not dfs:
        print("No data loaded!")
        return
    
    merged = pd.concat(dfs, ignore_index=True)
    print(f"\nTotal rows after merge: {len(merged)}")
    
    # Group by FROM, TO and compute median FLIGHT_TIME
    results = []
    
    for (frm, to), group in merged.groupby(["FROM", "TO"]):
        if pd.isna(frm) or pd.isna(to):
            continue
        frm = str(frm).strip()
        to = str(to).strip()
        if not frm or not to:
            continue
        
        # Parse flight times to minutes
        flight_times_min = group["FLIGHT_TIME"].apply(parse_flight_time_to_minutes).dropna().values
        
        if len(flight_times_min) == 0:
            continue
        
        median_min = float(np.median(flight_times_min))
        count = len(flight_times_min)
        min_val = float(np.min(flight_times_min))
        max_val = float(np.max(flight_times_min))
        
        results.append({
            "FROM": frm,
            "TO": to,
            "MEDIAN_FLIGHT_TIME_MIN": int(median_min),
            "COUNT": count,
            "MIN": int(min_val),
            "MAX": int(max_val),
        })
    
    if not results:
        print("No FROM-TO pairs with FLIGHT_TIME found!")
        return
    
    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values(["FROM", "TO"]).reset_index(drop=True)
    
    # Save reference file
    out_file = ROOT / "data" / "1_raw" / "flight_time_reference.csv"
    result_df.to_csv(out_file, index=False)
    
    print(f"\n✅ Saved {len(result_df)} FROM-TO pairs to: {out_file}")
    print(f"\nTop 20 routes by COUNT:")
    print(result_df.nlargest(20, "COUNT")[["FROM", "TO", "MEDIAN_FLIGHT_TIME_MIN", "COUNT"]])


if __name__ == "__main__":
    main()
