from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

LOG = logging.getLogger(__name__)

DEFAULT_YEAR = 2026
CHAIN_BREAK_MINUTES = 240


def _parse_date_time(date_series: pd.Series, time_series: pd.Series) -> pd.Series:
	date_part = date_series.fillna("").astype(str).str.strip()
	time_part = time_series.fillna("").astype(str).str.strip()
	combined = f"{DEFAULT_YEAR}-" + date_part + " " + time_part
	return pd.to_datetime(combined, format="%Y-%d/%m %H:%M", errors="coerce")


def _roll_arrival_if_overnight(sta_utc: pd.Series, std_utc: pd.Series) -> pd.Series:
	out = sta_utc.copy()
	mask = out.notna() & std_utc.notna() & (out < std_utc)
	out.loc[mask] = out.loc[mask] + pd.Timedelta(days=1)
	return out


def _build_time_columns(df: pd.DataFrame) -> pd.DataFrame:
	out = df.copy()
	out["std_utc"] = _parse_date_time(out["DATE"], out["STD"])
	out["atd_utc"] = _parse_date_time(out["DATE"], out["ATD"])
	out["sta_utc"] = _parse_date_time(out["DATE"], out["STA"])
	out["sta_utc"] = _roll_arrival_if_overnight(out["sta_utc"], out["std_utc"])
	out["status_utc"] = pd.to_datetime(out["STATUS_UTC"], errors="coerce")
	out["scheduled_dep_hour"] = out["std_utc"].dt.hour

	# Outcome columns: useful for training labels or diagnostics,
	# but they must not be used to build same-row predictors.
	out["dep_delay_minutes"] = (
		(out["atd_utc"] - out["std_utc"]).dt.total_seconds().div(60).clip(lower=0)
	)
	out["dep_delay_minutes"] = out["dep_delay_minutes"].where(out["atd_utc"].notna())
	return out


def _engineer_chain_features(df: pd.DataFrame) -> pd.DataFrame:
	out = _build_time_columns(df)
	out = out.sort_values(
		by=["std_utc", "FLIGHT", "FROM", "TO"],
		kind="mergesort",
	).reset_index(names="source_row")

	prev_sta = out["sta_utc"].shift(1)
	prev_status = out["status_utc"].shift(1)
	prev_dep_delay = out["dep_delay_minutes"].shift(1)

	out["prev_sta_utc"] = prev_sta
	out["prev_status_utc"] = prev_status
	out["prev_dep_delay_minutes"] = prev_dep_delay
	out["scheduled_turnaround"] = (
		(out["std_utc"] - prev_sta).dt.total_seconds().div(60)
	)
	out["actual_buffer_left"] = (
		(out["std_utc"] - prev_status).dt.total_seconds().div(60)
	)
	out["actual_turnaround"] = (
		(out["atd_utc"] - prev_status).dt.total_seconds().div(60)
	)

	out["has_prev_leg"] = prev_status.notna().astype(int)
	out["is_new_chain"] = (
		prev_status.isna()
		| out["scheduled_turnaround"].isna()
		| (out["scheduled_turnaround"] > CHAIN_BREAK_MINUTES)
		| (out["scheduled_turnaround"] < 0)
	).astype(int)

	out["chain_id"] = out["is_new_chain"].cumsum()
	out["flight_legs"] = out.groupby("chain_id").cumcount() + 1
	out["cumulative_delay"] = (
		out.groupby("chain_id")["dep_delay_minutes"]
		.transform(lambda s: s.fillna(0).cumsum().shift(fill_value=0))
	)

	out["scheduled_turnaround"] = out["scheduled_turnaround"].where(out["has_prev_leg"] == 1)
	out["actual_buffer_left"] = out["actual_buffer_left"].where(out["has_prev_leg"] == 1)
	out["actual_turnaround"] = out["actual_turnaround"].where(out["has_prev_leg"] == 1)
	out["prev_dep_delay_minutes"] = out["prev_dep_delay_minutes"].where(out["has_prev_leg"] == 1)
	out["cumulative_delay"] = out["cumulative_delay"].astype("float64")

	out = out.sort_values("source_row", kind="mergesort").drop(columns=["source_row"])
	return out


def process_file(in_path: Path, out_path: Path) -> None:
	df = pd.read_csv(in_path)
	required = {"DATE", "STD", "ATD", "STA", "STATUS_UTC"}
	missing = sorted(required - set(df.columns))
	if missing:
		raise ValueError(f"Missing required columns in {in_path.name}: {missing}")

	out_df = _engineer_chain_features(df)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_df.to_csv(out_path, index=False)
	LOG.info("Wrote feature-engineered flight list to %s", out_path)


def process_all(
	input_dir: Path | None = None,
	output_dir: Path | None = None,
	pattern: str = "*.csv",
) -> None:
	repo_root = Path(__file__).resolve().parents[2]
	if input_dir is None:
		input_dir = repo_root / "data" / "2_preprocessed" / "flight_list"
	if output_dir is None:
		output_dir = repo_root / "data" / "3_feature_engineered" / "flight_list"

	input_dir = Path(input_dir)
	output_dir = Path(output_dir)
	if not input_dir.exists():
		LOG.error("Input directory %s does not exist", input_dir)
		return

	for csv_path in sorted(input_dir.glob(pattern)):
		out_path = output_dir / csv_path.name
		try:
			process_file(csv_path, out_path)
		except Exception as exc:
			LOG.exception("Failed to process %s: %s", csv_path, exc)


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	parser = argparse.ArgumentParser(description="Engineer flight-list chain features from preprocessed CSVs")
	parser.add_argument("--input", help="Input folder (defaults to data/2_preprocessed/flight_list)")
	parser.add_argument("--output", help="Output folder (defaults to data/3_feature_engineered/flight_list)")
	args = parser.parse_args()

	input_dir = Path(args.input) if args.input else None
	output_dir = Path(args.output) if args.output else None
	process_all(input_dir, output_dir)


if __name__ == "__main__":
	main()
