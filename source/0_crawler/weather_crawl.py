import requests
import pandas as pd
import os
import re
import time
from datetime import datetime, timezone, timedelta

INPUT_MASTER_FILE = 'data/0_master/airports_list.csv'
OUTPUT_DIR = 'data/1_raw/weather_stats'
API_URL = "https://aviationweather.gov/api/data/metar"

def parse_metar_time(metar_str):
   
    try:
        match = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', metar_str)
        if not match:
            return None
        
        day, hour, minute = map(int, match.groups())
        now = datetime.now(timezone.utc)
        
        year = now.year
        month = now.month
        
        if day > now.day + 1:
            first_day_this_month = now.replace(day=1)
            last_day_last_month = first_day_this_month - timedelta(days=1)
            year = last_day_last_month.year
            month = last_day_last_month.month
            
        dt = datetime(year, month, day, hour, minute)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return None

def process_single_airport(icao, iata):
    params = {
        'ids': icao,
        'format': 'json',
        'hours': 48 
    }
    
    try:
        response = requests.get(API_URL, params=params, timeout=15)
        if response.status_code != 200:
            print(f"   [!] HTTP error {response.status_code} cho {icao}")
            return

        data = response.json()
        if not data:
            print(f"   [?] No data for {icao}")
            return

        station_results = []
        for entry in data:
            raw_metar = entry.get('rawOb', '')
            exact_time = parse_metar_time(raw_metar)
            if exact_time:
                station_results.append({
                    'Time_recorded': exact_time,
                    'METAR': raw_metar
                })

        if not station_results:
            return

        new_df = pd.DataFrame(station_results)
        
        file_path = os.path.join(OUTPUT_DIR, f"weather_stats_{str(iata).upper()}.csv")

        if os.path.exists(file_path):
            old_df = pd.read_csv(file_path)
            combined_df = pd.concat([new_df, old_df], ignore_index=True)
            combined_df.drop_duplicates(subset=['METAR'], keep='first', inplace=True)
        else:
            combined_df = new_df

        combined_df['Time_recorded'] = pd.to_datetime(combined_df['Time_recorded'])
        combined_df.sort_values(by='Time_recorded', ascending=False, inplace=True)
        combined_df['Time_recorded'] = combined_df['Time_recorded'].dt.strftime('%Y-%m-%d %H:%M:%S')


        combined_df.to_csv(file_path, index=False, encoding='utf-8-sig')
        print(f"   [OK] Completed {iata.upper()} ({len(station_results)} bản tin mới)")

    except Exception as e:
        print(f"   [X] Error {icao}: {e}")

def main():
    if not os.path.exists(INPUT_MASTER_FILE):
        print(f"Error: File not found {INPUT_MASTER_FILE}")
        return

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    master_df = pd.read_csv(INPUT_MASTER_FILE)
   
    valid_airports = master_df.dropna(subset=['ICAO', 'IATA'])
    
    print(f"Starting weather crawling process for {len(valid_airports)} airports...")
    print("-" * 50)

    for index, row in valid_airports.iterrows():
        icao = str(row['ICAO']).strip()
        iata = str(row['IATA']).strip()
        
        print(f"[{index + 1}/{len(valid_airports)}] Đang cào {icao} ({iata.upper()})...")
        
        process_single_airport(icao, iata)
 
        time.sleep(0.5)

    print("-" * 50)
    print("Completed weather crawling process.")

if __name__ == "__main__":
    main()