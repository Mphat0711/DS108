import requests
import json
import csv
import os
from datetime import datetime, timezone

AIRCRAFT_LIST_FILE = "DS108/data/0_master/aircraft_list.csv"
OUTPUT_DIR = os.path.join("data","1_raw", "flight_list")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

CSV_COLUMNS = ["DATE", "FLIGHT", "FROM", "TO", "FLIGHT_TIME", "STD", "ATD", "STA", "STATUS", "STATUS_COLOR"]

def format_time(ts):
    if not ts: return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")

def format_date(ts):
    if not ts: return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m")

def format_duration(seconds):
    if not seconds: return ""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h{m}m" if h > 0 else f"{m}m"

def get_flight_data(reg_number):
    url = "https://api.flightradar24.com/common/v1/flight/list.json"
    all_data = []
    page = 1

    while True:
        params = {
            "query": reg_number.lower(),
            "fetchBy": "reg",
            "limit": 100,
            "page": page
        }

        response = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = response.json().get("result", {}).get("response", {}).get("data", [])

        if not data:
            break

        all_data.extend(data)
        page += 1

    return all_data

def update_csv_for_aircraft(reg_number, flights_list):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        print(f"Completed: {OUTPUT_DIR}")

    file_name = f"flight_list_{reg_number.upper()}.csv"
    file_path = os.path.join(OUTPUT_DIR, file_name)
    
    data_map = {}

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["DATE"], row["FLIGHT"])
                data_map[key] = row

    for flight in flights_list:
        ident = flight.get("identification", {})
        times = flight.get("time", {})
        airports = flight.get("airport", {})
        status = flight.get("status", {})
        
        dep_ts = times.get("scheduled", {}).get("departure") or times.get("real", {}).get("departure") or 0
        f_date = format_date(dep_ts)
        f_flight = ident.get("number", {}).get("default")
        
        if not f_flight or not f_date: continue

        new_row = {
            "DATE": f_date,
            "FLIGHT": f_flight,
            "FROM": airports.get("origin", {}).get("code", {}).get("iata") if airports.get("origin") else "N/A",
            "TO": airports.get("destination", {}).get("code", {}).get("iata") if airports.get("destination") else "N/A",
            "FLIGHT_TIME": format_duration(times.get("other", {}).get("duration")),
            "STD": format_time(times.get("scheduled", {}).get("departure")),
            "ATD": format_time(times.get("real", {}).get("departure")),
            "STA": format_time(times.get("scheduled", {}).get("arrival")),
            "STATUS": status.get("text"),
            "STATUS_COLOR": status.get("icon"),
            "_ts": dep_ts
        }
        
        key = (f_date, f_flight)
        data_map[key] = new_row

    sorted_data = sorted(data_map.values(), key=lambda x: int(x.get("_ts", 0)), reverse=True)

    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted_data)

    print(f"Completed: {file_path} ({len(sorted_data)} rows)")

def main():
    if not os.path.exists(AIRCRAFT_LIST_FILE):
        print(f"Error: File not found {AIRCRAFT_LIST_FILE}")
        return

    with open(AIRCRAFT_LIST_FILE, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            reg = row.get('reg_number') or list(row.values())[0]
            if not reg: continue
            
            print(f"Completed: {reg}")
            new_flights = get_flight_data(reg)
            if new_flights:
                update_csv_for_aircraft(reg, new_flights)

if __name__ == "__main__":
    main()