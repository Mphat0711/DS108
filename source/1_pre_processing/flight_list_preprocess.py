import pandas as pd
import numpy as np
import re
from pathlib import Path
from datetime import datetime, timedelta

# --- CẤU HÌNH ĐƯỜNG DẪN ---
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "1_raw" / "flight_list"
MASTER_DIR = ROOT / "data" / "0_master"
OUT_DIR = ROOT / "data" / "2_preprocessed" / "flight_list"

YEAR = 2026

def to_min(s):
    if pd.isna(s) or str(s).lower() in ['nan', 'none', 'unknown', '']: return None
    s = str(s).strip()
    hm = re.findall(r'(\d+):(\d+)', s)
    if hm: return int(hm[0][0]) * 60 + int(hm[0][1])
    h = re.findall(r'(\d+)h', s)
    m = re.findall(r'(\d+)m', s)
    if h or m: return int(h[0] if h else 0) * 60 + int(m[0] if m else 0)
    return None

def parse_gmt_offset(tz_str):
    if pd.isna(tz_str) or not isinstance(tz_str, str): return 0
    m = re.match(r"GMT([+-])(\d{2}):(\d{2})", tz_str.strip())
    if not m: return 0
    sign = 1 if m.group(1) == "+" else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))

def build_tz_map():
    csv_path = MASTER_DIR / "airports_list.csv"
    if not csv_path.exists():
        print(f"Missing {csv_path}")
        return {}
    df_ap = pd.read_csv(csv_path)
    return df_ap.set_index('IATA')['GMT_Timezone'].apply(parse_gmt_offset).to_dict()

def process_file(file_path, tz_map):
    df = pd.read_csv(file_path)
    df.columns = [c.strip() for c in df.columns]
    for col in ['DATE', 'FROM', 'TO', 'STATUS', 'ATD', 'FLIGHT_TIME']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace(['nan', 'None', 'Unknown', 'unknown', ''], np.nan)

    df['_fn_num'] = df['FLIGHT'].str.extract(r'(\d+)').astype(float)
    df['_ref_dur'] = df['FLIGHT_TIME'].apply(to_min)

    for i in range(len(df)):
        if pd.isna(df.loc[i, 'TO']) or pd.isna(df.loc[i, '_ref_dur']):
            for neighbor in [i - 1, i + 1]:
                if neighbor in df.index:
                    if abs(df.loc[i, '_fn_num'] - df.loc[neighbor, '_fn_num']) == 1:
                        if pd.isna(df.loc[i, 'TO']):
                            df.at[i, 'TO'] = df.loc[neighbor, 'FROM']
                        if pd.isna(df.loc[i, '_ref_dur']):
                            df.at[i, '_ref_dur'] = to_min(df.loc[neighbor, 'FLIGHT_TIME'])
                        break

    def calculate_logic(row):
        atd_utc_dt = None
        if pd.notna(row['DATE']) and pd.notna(row['ATD']):
            atd_utc_dt = datetime.strptime(f"{row['DATE']}/{YEAR} {row['ATD']}", "%d/%m/%Y %H:%M")

        local_time_str = re.findall(r'(\d{1,2}:\d{2})', str(row['STATUS']))
        offset = tz_map.get(str(row['TO']).strip(), 0)
        final_status_label = row['STATUS']

        status_utc_dt = None

        if local_time_str:
            local_dt = datetime.strptime(f"{row['DATE']}/{YEAR} {local_time_str[0]}", "%d/%m/%Y %H:%M")
            status_utc_dt = local_dt - timedelta(minutes=offset)
        elif atd_utc_dt and pd.notna(row['_ref_dur']):
            status_utc_dt = atd_utc_dt + timedelta(minutes=row['_ref_dur'])
            local_dt = status_utc_dt + timedelta(minutes=offset)
            final_status_label = f"Estimated {local_dt.strftime('%H:%M')}"


        if atd_utc_dt and status_utc_dt and status_utc_dt <= atd_utc_dt:
            status_utc_dt += timedelta(days=1)

            if "Estimated" in str(final_status_label):
                local_dt = status_utc_dt + timedelta(minutes=offset)
                final_status_label = f"Estimated {local_dt.strftime('%H:%M')}"

        ft_mins = np.nan
        if status_utc_dt and atd_utc_dt:
            ft_mins = int((status_utc_dt - atd_utc_dt).total_seconds() / 60)

        status_utc_str = status_utc_dt.strftime("%Y-%m-%d %H:%M") if status_utc_dt else ""
        return status_utc_str, ft_mins, final_status_label

    results = df.apply(calculate_logic, axis=1)
    df['STATUS_UTC'] = [r[0] for r in results]
    df['FLIGHT_TIME_MINS'] = [r[1] for r in results]
    df['STATUS'] = [r[2] for r in results]

    df['FLIGHT_TIME'] = df['FLIGHT_TIME_MINS'].apply(
        lambda x: f"{int(x//60)}h{int(x%60)}m" if (pd.notna(x) and x > 0) else ""
    )

    df = df.drop(columns=['_fn_num', '_ref_dur'])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / file_path.name, index=False)
    print(f"Processing completed: {file_path.name}")

def main():
    tz_map = build_tz_map()
    files = list(RAW_DIR.glob("*.csv"))
    if not files:
        print("Empty input folder.")
        return
    for f in files:
        process_file(f, tz_map)

if __name__ == "__main__":
    main()