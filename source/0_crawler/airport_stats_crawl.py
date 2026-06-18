import requests
import json
import time
import pandas as pd
import os
from datetime import datetime

INPUT_MASTER_FILE = 'data/0_master/airports_list.csv'
OUTPUT_DIR = os.path.join("data", "1_raw", "airport_stats")
TOKEN = ""

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://www.flightradar24.com/'
}

def extract_full_stats(data):
    try:
        details = data['result']['response']['airport']['pluginData']['details']
        stats = details.get('stats', {})
        
        def get_nested(d, path, default=0):
            for key in path:
                if isinstance(d, dict):
                    d = d.get(key, default)
                else:
                    return default
            return d

        record = {
            'time_crawled': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'airport_code': details['code']['iata'],
    
            'dep_recent_index': get_nested(stats, ['departures', 'recent', 'delayIndex']),
            'dep_recent_delay_avg': get_nested(stats, ['departures', 'recent', 'delayAvg']),
            'dep_recent_delayed_qty': get_nested(stats, ['departures', 'recent', 'quantity', 'delayed']),
            'dep_today_delayed_pct': get_nested(stats, ['departures', 'today', 'percentage', 'delayed']),
            'dep_today_delayed_qty': get_nested(stats, ['departures', 'today', 'quantity', 'delayed']),
            'dep_today_total_qty': get_nested(stats, ['departures', 'today', 'quantity', 'total']),
            'dep_yesterday_delayed_pct': get_nested(stats, ['departures', 'yesterday', 'percentage', 'delayed']),
            'dep_trend': get_nested(stats, ['departures', 'percentage', 'trend']),

            'arr_recent_index': get_nested(stats, ['arrivals', 'recent', 'delayIndex']),
            'arr_recent_delay_avg': get_nested(stats, ['arrivals', 'recent', 'delayAvg']),
            'arr_recent_delayed_qty': get_nested(stats, ['arrivals', 'recent', 'quantity', 'delayed']),
            'arr_today_delayed_pct': get_nested(stats, ['arrivals', 'today', 'percentage', 'delayed']),
            'arr_today_delayed_qty': get_nested(stats, ['arrivals', 'today', 'quantity', 'delayed']),
            'arr_today_total_qty': get_nested(stats, ['arrivals', 'today', 'quantity', 'total']),
            'arr_yesterday_delayed_pct': get_nested(stats, ['arrivals', 'yesterday', 'percentage', 'delayed']),
            'arr_trend': get_nested(stats, ['arrivals', 'percentage', 'trend']),
            
            'total_flights_today': get_nested(stats, ['total', 'quantity', 'total']),
            'ground_speed_avg': get_nested(data, ['result', 'response', 'airport', 'pluginData', 'weather', 'wind', 'speed', 'kmh'])
        }
        return record
    except Exception as e:
        print(f"Lỗi phân tích: {e}")
        return None

def save_and_prepend(record, airport_code):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    file_path = os.path.join(OUTPUT_DIR, f"stats_{airport_code.upper()}.csv")
    new_df = pd.DataFrame([record])

    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            final_df = pd.concat([new_df, old_df], ignore_index=True)
        except:
            final_df = new_df
    else:
        final_df = new_df

    final_df.to_csv(file_path, index=False, encoding='utf-8-sig')

def run_bot():
    if not os.path.exists(INPUT_MASTER_FILE):
        print(f"Error: File not found {INPUT_MASTER_FILE}")
        return

    try:
        master_df = pd.read_csv(INPUT_MASTER_FILE)
        iata_codes = master_df['IATA'].dropna().str.lower().tolist()
    except Exception as e:
        print(f"Error reading IATA list: {e}")
        return

    print(f"Starting data retrieval for {len(iata_codes)} airports...")

    for code in iata_codes:
        current_ts = int(time.time())
        api_url = f"https://api.flightradar24.com/common/v1/airport.json?code={code}&plugin[]=&plugin-setting[schedule][mode]=&plugin-setting[schedule][timestamp]={current_ts}&page=1&limit=100&fleet=&token={TOKEN}"

        try:
            res = requests.get(api_url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                record = extract_full_stats(res.json())
                if record:
                    save_and_prepend(record, code)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] OK: {code.upper()}")
            else:
                print(f"Error {code.upper()}: HTTP {res.status_code}")
        except Exception as e:
            print(f"Error connecting {code.upper()}: {e}")
        
        time.sleep(1)

if __name__ == "__main__":
    run_bot()