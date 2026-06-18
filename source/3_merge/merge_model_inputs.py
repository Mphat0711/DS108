from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

LOG = logging.getLogger(__name__)


FLIGHT_KEEP_COLUMNS = [
	"DATE",
	"FLIGHT",
	"FROM",
	"TO",
	"scheduled_dep_hour",
	"dep_delay_minutes",
	"prev_dep_delay_minutes",
	"scheduled_turnaround",
	"actual_buffer_left",
	"flight_legs",
	"cumulative_delay",
	"actual_turnaround",
]

MASTER_KEEP_COLUMNS = [
	"reg_number",
	"aircraft_model",
	"age",
	"airline_code",
]

WEATHER_KEEP_COLUMNS = [
	"is_cavok",
	"wind_speed",
	"is_gust",
	"visibility_meters",
	"has_thunderstorm",
	"has_heavy_rain",
	"has_low_vis_fog",
]

AIRPORT_KEEP_COLUMNS = [
	"dep_recent_index",
	"dep_recent_delay_avg",
]


def load_master(master_path: Path) -> pd.DataFrame:
	df = pd.read_csv(master_path)
	missing = [c for c in MASTER_KEEP_COLUMNS if c not in df.columns]
	if missing:
		raise ValueError(f"Missing master columns {missing} in {master_path}")
	return df[MASTER_KEEP_COLUMNS].copy()


def load_flight_list(flight_dir: Path) -> pd.DataFrame:
	frames: list[pd.DataFrame] = []

	for csv_path in sorted(flight_dir.glob("*.csv")):
		df = pd.read_csv(csv_path)
		required = FLIGHT_KEEP_COLUMNS + ["std_utc"]
		missing = [c for c in required if c not in df.columns]
		if missing:
			LOG.warning("Skipping %s because of missing columns: %s", csv_path.name, missing)
			continue

		frame = df[required].copy()
		frame["std_utc"] = pd.to_datetime(frame["std_utc"], errors="coerce")
		frame = frame.dropna(subset=["std_utc"])
		frame["reg_number"] = csv_path.stem.removeprefix("flight_list_")
		frame["flight_source_file"] = csv_path.name
		frames.append(frame)

	if not frames:
		raise ValueError(f"No usable flight-list files found in {flight_dir}")

	out = pd.concat(frames, ignore_index=True)
	return out.sort_values(["FROM", "std_utc", "reg_number", "FLIGHT"], kind="mergesort").reset_index(drop=True)


def load_weather(weather_dir: Path) -> pd.DataFrame:
	frames: list[pd.DataFrame] = []

	for csv_path in sorted(weather_dir.glob("*.csv")):
		df = pd.read_csv(csv_path)
		required = ["Time_recorded"] + WEATHER_KEEP_COLUMNS
		missing = [c for c in required if c not in df.columns]
		if missing:
			LOG.warning("Skipping %s because of missing columns: %s", csv_path.name, missing)
			continue

		airport_code = csv_path.stem.removeprefix("weather_stats_")
		frame = df[required].copy()
		frame["weather_airport_code"] = airport_code
		frame["Time_recorded"] = pd.to_datetime(frame["Time_recorded"], errors="coerce")
		frame = frame.dropna(subset=["Time_recorded"])
		frames.append(frame)

	if not frames:
		raise ValueError(f"No usable weather files found in {weather_dir}")

	out = pd.concat(frames, ignore_index=True)
	out = out.sort_values(["weather_airport_code", "Time_recorded"], kind="mergesort").reset_index(drop=True)
	return out


def load_airport_stats(airport_dir: Path) -> pd.DataFrame:
	frames: list[pd.DataFrame] = []

	for csv_path in sorted(airport_dir.glob("*.csv")):
		df = pd.read_csv(csv_path)
		required = ["time_crawled_UTC", "airport_code"] + AIRPORT_KEEP_COLUMNS
		missing = [c for c in required if c not in df.columns]
		if missing:
			LOG.warning("Skipping %s because of missing columns: %s", csv_path.name, missing)
			continue

		frame = df[required].copy()
		frame["time_crawled_UTC"] = pd.to_datetime(frame["time_crawled_UTC"], errors="coerce", utc=True).dt.tz_localize(None)
		frame = frame.dropna(subset=["time_crawled_UTC"])
		frames.append(frame)

	if not frames:
		raise ValueError(f"No usable airport-stats files found in {airport_dir}")

	out = pd.concat(frames, ignore_index=True)
	out = out.sort_values(["airport_code", "time_crawled_UTC"], kind="mergesort").reset_index(drop=True)
	return out


def merge_weather(flights: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
	parts: list[pd.DataFrame] = []
	for airport_code, left_group in flights.groupby("FROM", sort=False):
		right_group = weather.loc[weather["weather_airport_code"] == airport_code].copy()
		left_group = left_group.sort_values("std_utc", kind="mergesort").copy()

		if right_group.empty:
			for col in ["Time_recorded", "weather_airport_code", *WEATHER_KEEP_COLUMNS]:
				if col not in left_group.columns:
					left_group[col] = pd.NA
			parts.append(left_group)
			continue

		right_group = right_group.sort_values("Time_recorded", kind="mergesort").copy()
		merged_group = pd.merge_asof(
			left_group,
			right_group,
			left_on="std_utc",
			right_on="Time_recorded",
			direction="backward",
			allow_exact_matches=False,
		)
		parts.append(merged_group)

	return pd.concat(parts, ignore_index=True)


def merge_airport_stats(flights: pd.DataFrame, airport_stats: pd.DataFrame) -> pd.DataFrame:
	parts: list[pd.DataFrame] = []
	for airport_code, left_group in flights.groupby("FROM", sort=False):
		right_group = airport_stats.loc[airport_stats["airport_code"] == airport_code].copy()
		left_group = left_group.sort_values("std_utc", kind="mergesort").copy()

		if right_group.empty:
			for col in ["time_crawled_UTC", "airport_code", *AIRPORT_KEEP_COLUMNS]:
				if col not in left_group.columns:
					left_group[col] = pd.NA
			parts.append(left_group)
			continue

		right_group = right_group.sort_values("time_crawled_UTC", kind="mergesort").copy()
		merged_group = pd.merge_asof(
			left_group,
			right_group,
			left_on="std_utc",
			right_on="time_crawled_UTC",
			direction="backward",
			allow_exact_matches=False,
		)
		parts.append(merged_group)

	return pd.concat(parts, ignore_index=True)


def build_final_dataset(
	flight_dir: Path,
	master_path: Path,
	weather_dir: Path,
	airport_dir: Path,
) -> pd.DataFrame:
	flights = load_flight_list(flight_dir)
	master = load_master(master_path)
	weather = load_weather(weather_dir)
	airport_stats = load_airport_stats(airport_dir)

	merged = flights.merge(master, on="reg_number", how="left", validate="many_to_one")
	merged = merge_weather(merged, weather)
	merged = merge_airport_stats(merged, airport_stats)

	final_columns = [
		"reg_number",
		"aircraft_model",
		"age",
		"airline_code",
		"DATE",
		"FLIGHT",
		"FROM",
		"TO",
		"std_utc",
		"scheduled_dep_hour",
		"dep_delay_minutes",
		"prev_dep_delay_minutes",
		"scheduled_turnaround",
		"actual_buffer_left",
		"flight_legs",
		"cumulative_delay",
		"Time_recorded",
		*WEATHER_KEEP_COLUMNS,
		"time_crawled_UTC",
		*AIRPORT_KEEP_COLUMNS,
		"actual_turnaround",
		"flight_source_file",
	]

	for col in final_columns:
		if col not in merged.columns:
			merged[col] = pd.NA

	return merged[final_columns].sort_values(["std_utc", "reg_number", "FLIGHT"], kind="mergesort").reset_index(drop=True)


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	parser = argparse.ArgumentParser(description="Merge flight-list, master, weather, and airport-stat features into one final CSV.")
	parser.add_argument("--flight-dir", help="Defaults to data/3_feature_engineered/flight_list")
	parser.add_argument("--master-path", help="Defaults to data/0_master/aircraft_list.csv")
	parser.add_argument("--weather-dir", help="Defaults to data/3_feature_engineered/weather_stats")
	parser.add_argument("--airport-dir", help="Defaults to data/3_feature_engineered/airport_stats")
	parser.add_argument("--output", help="Defaults to data/4_final/model_input_merged.csv")
	args = parser.parse_args()

	repo_root = Path(__file__).resolve().parents[2]
	flight_dir = Path(args.flight_dir) if args.flight_dir else repo_root / "data" / "3_feature_engineered" / "flight_list"
	master_path = Path(args.master_path) if args.master_path else repo_root / "data" / "0_master" / "aircraft_list.csv"
	weather_dir = Path(args.weather_dir) if args.weather_dir else repo_root / "data" / "3_feature_engineered" / "weather_stats"
	airport_dir = Path(args.airport_dir) if args.airport_dir else repo_root / "data" / "3_feature_engineered" / "airport_stats"
	output_path = Path(args.output) if args.output else repo_root / "data" / "4_final" / "model_input_merged.csv"

	final_df = build_final_dataset(flight_dir, master_path, weather_dir, airport_dir)
	output_path.parent.mkdir(parents=True, exist_ok=True)
	final_df.to_csv(output_path, index=False)

	LOG.info("Wrote merged dataset to %s", output_path)
	LOG.info("Rows: %s", len(final_df))
	LOG.info("Rows with weather match: %s", final_df["Time_recorded"].notna().sum())
	LOG.info("Rows with airport-stats match: %s", final_df["time_crawled_UTC"].notna().sum())


if __name__ == "__main__":
	main()
