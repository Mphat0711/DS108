import json
import re
import csv
import os
from datetime import datetime
import pytz
from bs4 import BeautifulSoup
from timezonefinder import TimezoneFinder

def get_gmt_offset(lat, lon, tf):
    try:
        tz_name = tf.timezone_at(lng=lon, lat=lat)
        if not tz_name:
            return "N/A"
        
        timezone = pytz.timezone(tz_name)
        now = datetime.now(timezone)
        offset = now.strftime('%z') 
 
        return f"GMT{offset[:3]}:{offset[3:]}"
    except:
        return "N/A"

def process_vjc_html(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"Error: File not found '{input_file}' in the directory.")
        return

    tf = TimezoneFinder()
    processed_icao = set()
    final_airports = []

    print(f"Starting analysis of file: {input_file}...")

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')
        scripts = soup.find_all('script')
        
        json_data = None
        for script in scripts:
            if script.string and 'var arrRoutes =' in script.string:
                match = re.search(r'var arrRoutes\s*=\s*(\[[\s\S]*?\])\s*[,;]', script.string)
                if match:
                    try:
                        json_data = json.loads(match.group(1))
                        break
                    except json.JSONDecodeError as e:
                        print(f"Error decoding JSON: {e}")
                        continue

        if not json_data:
            print("Failed: No valid 'arrRoutes' data found.")
            return

        print(f"Found {len(json_data)} routes. Extracting airport list...")

        for route in json_data:
            for key in ['airport1', 'airport2']:
                ap = route[key]
                icao = ap.get('icao')

                if not icao or icao in processed_icao:
                    continue
                
                # Lấy múi giờ
                gmt_tz = get_gmt_offset(ap['lat'], ap['lon'], tf)

                final_airports.append({
                    'Name': ap.get('name'),
                    'IATA': ap.get('iata'),
                    'ICAO': icao,
                    'Country': ap.get('country'),
                    'GMT_Timezone': gmt_tz
                })
                
                processed_icao.add(icao)

        # Ghi ra file CSV
        headers = ['Name', 'IATA', 'ICAO', 'Country', 'GMT_Timezone']
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(final_airports)

        print("-" * 30)
        print(f"Completed!")
        print(f"Number of unique airports: {len(final_airports)}")
        print(f"Results saved to: {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    INPUT_FILE = 'bav.html' 
    OUTPUT_FILE = 'bav_ap.csv'
    
    process_vjc_html(INPUT_FILE, OUTPUT_FILE)