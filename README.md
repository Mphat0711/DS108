# DS108 Project: Data Collection and Preprocessing for Contextual Analysis of Aircraft Turnaround Time in Vietnam’s Aviation Environment

This project collects, preprocesses, engineers, and merges aviation data for analyzing aircraft turnaround-time behavior in the Vietnamese aviation environment.

## Authors

- Tran Minh Phat - 24521317
- Vo Kieu Oanh - 24521286
Faculty of Information Science and Engineering
## Project Structure

```text
DS108/
|-- config/
|   `-- crawler_schedule.json          # Crawler orchestration schedule
|-- data/
|   |-- 0_master/                      # Static master data: airports, aircraft, airlines
|   |-- 1_raw/                         # Raw data collected from APIs and web sources
|   |   |-- airport_stats/             # Airport arrival/departure status data
|   |   |-- flight_list/               # Flight history data from Flightradar24
|   |   `-- weather_stats/             # Raw METAR weather reports
|   |-- 2_preprocessed/                # Normalized and UTC-synchronized data
|   |-- 3_feature_engineered/          # Extracted features for modeling and analysis
|   `-- 4_final/                       # Final merged dataset
|-- logs/
|   `-- crawler_runs/                  # Orchestrator and crawler execution logs
|-- notebook/                          # EDA notebooks and analysis experiments
|-- others/                            # Technical references, diagrams, and supporting files
|-- source/
|   |-- 0_crawler/                     # Raw data crawler scripts and crawler orchestrator
|   |-- 1_pre_processing/              # Cleaning, time normalization, and raw-data preparation
|   |-- 2_feature_extract/             # Feature extraction modules
|   `-- 3_merge/                       # Final dataset merge logic
|-- README.md                          # Project documentation
`-- requirements.txt                   # Python dependencies
```

## Environment Setup

Install the required Python packages:

```powershell
pip install -r requirements.txt
```

Run commands from the project root:

```powershell
cd D:\DS108
```

## Crawler System

The crawler layer collects three raw data streams:

| Job | Script | Default Schedule | Output |
| --- | --- | --- | --- |
| Airport statistics | `source/0_crawler/airport_stats_crawl.py` | Every day at 18:00, 20:00, and 22:00 UTC+7 | `data/1_raw/airport_stats/` |
| Weather METAR reports | `source/0_crawler/weather_crawl.py` | Every 48 hours | `data/1_raw/weather_stats/` |
| Flight history | `source/0_crawler/flight_list_crawl.py` | Every 6 days | `data/1_raw/flight_list/` |

The crawler scripts above are kept as standalone collectors. The orchestration layer only calls them; it does not require modifying the crawler implementations.

## Crawler Orchestrator

The automated crawler coordinator is:

```text
source/0_crawler/crawler_orchestrator.py
```

Its schedule configuration is:

```text
config/crawler_schedule.json
```

The orchestrator supports scheduled daemon execution, one-shot due-job checks, dry runs, manual job execution, per-run logs, and persistent state tracking.

### Check the Config

```powershell
python source/0_crawler/crawler_orchestrator.py --check-config
```

### View the Current Schedule

```powershell
python source/0_crawler/crawler_orchestrator.py --list
```

### Test One Scheduling Cycle Without Running Crawlers

```powershell
python source/0_crawler/crawler_orchestrator.py --once --dry-run
```

### Run the Orchestrator Continuously

```powershell
python source/0_crawler/crawler_orchestrator.py
```

The process keeps polling the schedule from `config/crawler_schedule.json` and launches due crawlers automatically.

### Run a Crawler Manually

Run one job:

```powershell
python source/0_crawler/crawler_orchestrator.py --run airport_stats
```

Run all configured jobs:

```powershell
python source/0_crawler/crawler_orchestrator.py --run all
```

Available job IDs:

- `airport_stats`
- `weather_metar`
- `flight_list`

## Logs and State

Crawler run logs are stored in:

```text
logs/crawler_runs/
```

The scheduler state file is stored in:

```text
logs/crawler_state.json
```

The state file records the latest attempted schedule mark, status, exit code, log file, and run count for each job. Delete this file only when you intentionally want the orchestrator to forget previous schedule marks.

## Data Pipeline Overview

After raw data is collected into `data/1_raw/`, downstream modules prepare the data for analysis:

1. Preprocess raw flight, weather, and airport-stat data in `source/1_pre_processing/`.
2. Extract modeling features in `source/2_feature_extract/`.
3. Merge processed data into the final dataset in `source/3_merge/`.
4. Use notebooks in `notebook/` for EDA and analysis.

## Notes

- Keep crawler credentials, tokens, and API-sensitive values out of public commits.
- Use `--dry-run` before starting long crawler sessions.
- The orchestrator logs stdout and stderr from each crawler into a dedicated run log, which makes failed API requests easier to inspect.
