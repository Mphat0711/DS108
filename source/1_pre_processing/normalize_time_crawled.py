from pathlib import Path
import pandas as pd
import datetime as dt
import re

ROOT = Path(__file__).resolve().parents[2]


def parse_gmt_offset(text):
    if not isinstance(text, str) or not text.startswith("GMT"):
        return dt.timedelta(0)
    m = re.match(r"GMT([+-])(\d{2}):(\d{2})", text)
    if not m:
        return dt.timedelta(0)
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3))
    return dt.timedelta(hours=sign * hours, minutes=sign * minutes)


def parse_time(s):
    if pd.isna(s):
        return pd.NaT
    s = str(s).strip()
    try:
        dtv = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", errors="coerce")
        if not pd.isna(dtv):
            return dtv
    except Exception:
        pass
    try:
        dtv = pd.to_datetime(s, format="%y-%m-%d %H:%M:%S", errors="coerce")
        if not pd.isna(dtv):
            return dtv
    except Exception:
        pass
    try:
        dtv = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        return dtv
    except Exception:
        return pd.NaT


def main():
    raw_dir = ROOT / "data" / "1_raw" / "airport_stats"
    airports_csv = ROOT / "data" / "0_master" / "airports_list.csv"
    out_dir = ROOT / "data" / "2_preprocessed" / "airport_stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not airports_csv.exists():
        print("airports_list.csv not found:", airports_csv)
        return

    airports = pd.read_csv(airports_csv)
    airports_map = {}
    for _, r in airports.iterrows():
        code = str(r.get("IATA", "")).strip()
        tz = r.get("GMT_Timezone", "GMT+00:00")
        airports_map[code] = parse_gmt_offset(tz)

    files = sorted(raw_dir.glob("*.csv"))
    if not files:
        print("No files in", raw_dir)
        return

    for p in files:
        try:
            df = pd.read_csv(p)
        except Exception as e:
            print("Failed to read", p.name, e)
            continue
        df.columns = [c.strip() for c in df.columns]
        if "time_crawled" not in df.columns:
            print(p.name, "missing time_crawled column; skipping")
            continue

        def norm_row(r):
            raw = r.get("time_crawled")
            parsed = parse_time(raw)
            if pd.isna(parsed):
                return None
            code = r.get("airport_code") if "airport_code" in r else None
            if pd.isna(code) or not str(code).strip():
                code = None
            offset = airports_map.get(code, dt.timedelta(0))
            utc_dt = parsed - offset
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        df["time_crawled_UTC"] = df.apply(norm_row, axis=1)
        out_path = out_dir / p.name
        df.to_csv(out_path, index=False)
        print("Wrote:", out_path)


if __name__ == "__main__":
    main()
