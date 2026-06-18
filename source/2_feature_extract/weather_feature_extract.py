
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# Try to import python-metar; if not available, will use fallback regex
try:
	from metar.Metar import Metar as MetarParser
	HAS_METAR = True
except ImportError:
	MetarParser = None  # type: ignore
	HAS_METAR = False

LOG = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
	try:
		return float(value)
	except Exception:
		pass

	if callable(value):
		try:
			return float(value())
		except Exception:
			pass

	if hasattr(value, "value"):
		try:
			return float(value.value())
		except Exception:
			pass

	return None


def _parse_visibility_from_sm(token: str) -> int | None:
	value = token.removesuffix("SM")
	total = 0.0

	try:
		if " " in value:
			for part in value.split():
				if "/" in part:
					num, den = part.split("/", 1)
					total += float(num) / float(den)
				else:
					total += float(part)
		elif "/" in value:
			num, den = value.split("/", 1)
			total = float(num) / float(den)
		else:
			total = float(value)
	except Exception:
		return None

	return int(round(total * 1609.0))


def _extract_visibility_meters(tokens: List[str]) -> int | None:
	for idx, token in enumerate(tokens):
		if token == "CAVOK":
			return 9999

		if token.endswith("SM"):
			if idx > 0 and re.fullmatch(r"\d+", tokens[idx - 1]) and "/" in token:
				combined = f"{tokens[idx - 1]} {token}"
				meters = _parse_visibility_from_sm(combined)
				if meters is not None:
					return meters
			meters = _parse_visibility_from_sm(token)
			if meters is not None:
				return meters

		if re.fullmatch(r"\d{4}", token):
			return int(token)

	return None


def _extract_weather_tokens(tokens: List[str], vocab: List[str]) -> List[str]:
	found: List[str] = []
	for idx, token in enumerate(tokens):
		clean = token.strip()
		if not clean:
			continue
		if clean in {"METAR", "SPECI"}:
			continue
		if idx == 1 and re.fullmatch(r"[A-Z]{4}", clean):
			continue
		if re.fullmatch(r"\d{6}Z", clean):
			continue
		if re.fullmatch(r"[A-Z]{2,3}\d{2,3}", clean):
			continue
		if re.fullmatch(r"[A-Z]{3}\d{3}(?:CB|TCU)?", clean):
			continue
		if re.fullmatch(r"[M]?\d{2}/[M]?\d{2}", clean):
			continue
		if re.fullmatch(r"A\d{4}", clean):
			continue

		exact_matches = [vocab_token for vocab_token in vocab if clean == vocab_token]
		if exact_matches:
			found.extend(exact_matches)
			continue

		for vocab_token in vocab:
			if len(clean) > len(vocab_token) and (clean.endswith(vocab_token) or clean.startswith(vocab_token)):
				found.append(vocab_token)
	return sorted(set(found))


def parse_metar(metar: str) -> Dict[str, Any]:
	s = (metar or "").strip()
	S = s.upper()
	tokens = S.split()
	out: Dict[str, Any] = {
		"is_cavok": 0,
		"wind_speed": None,
		"is_gust": 0,
		"visibility_meters": None,
		"has_thunderstorm": 0,
		"has_heavy_rain": 0,
		"has_low_vis_fog": 0,
		"other_phenomenon": "",
	}

	if not S:
		return out

	if "CAVOK" in S:
		out.update({
			"is_cavok": 1,
			"wind_speed": 0,
			"is_gust": 0,
			"visibility_meters": 9999,
			"has_thunderstorm": 0,
			"has_heavy_rain": 0,
			"has_low_vis_fog": 0,
			"other_phenomenon": "",
		})
		return out

	# vocabulary used for fallback / other_phenomenon
	vocab = [
		"TS",
		"+RA",
		"-RA",
		"RA",
		"DZ",
		"SN",
		"SG",
		"PL",
		"GR",
		"GS",
		"UP",
		"SQ",
		"FC",
		"SS",
		"DS",
		"BC",
		"VC",
		"SH",
		"FG",
		"BR",
	]

	# Prefer using python-metar if available
	if HAS_METAR:
		try:
			m = MetarParser(s)
			# wind speed (knots)
			ws = getattr(m, "wind_speed", None)
			if ws:
				val = _safe_float(ws)
				if val is not None:
					out["wind_speed"] = int(round(val))

			gust = getattr(m, "wind_gust", None)
			if gust:
				out["is_gust"] = 1

			# python-metar exposes some values as methods, so only use it if conversion succeeds
			vis = getattr(m, "vis", None)
			v = _safe_float(vis)
			if v is not None:
				if v < 1000:
					v = v * 1609.0
				out["visibility_meters"] = int(round(v))

			# weather phenomena via string search of parsed weather attributes
			try:
				weather_text = " ".join(
					filter(
						None,
						[
							str(getattr(m, "weather", "") or ""),
							str(getattr(m, "present_weather", "")() if callable(getattr(m, "present_weather", None)) else getattr(m, "present_weather", "") or ""),
						],
					)
				).upper()
				if "TS" in weather_text or "TS" in S:
					out["has_thunderstorm"] = 1
				if "+RA" in S or "+RA" in weather_text:
					out["has_heavy_rain"] = 1
				if any(t in weather_text for t in ("FG", "BR")) or any(t in S for t in ("FG", "BR")):
					out["has_low_vis_fog"] = 1
				found = _extract_weather_tokens(tokens, vocab)
				out["other_phenomenon"] = ",".join(sorted(set(found)))
			except Exception:
				pass
		except Exception:
			# fall back to regex-based parser below
			pass

	# --- fallback: rule-based regex extraction ---
	# Wind: look for patterns like 23012KT or 09015G25KT or VRB05KT
	wind_re = re.search(r"\b(\d{3}|VRB)?(?P<speed>\d{2,3})(G(?P<gust>\d{2,3}))?KT\b", S)
	if wind_re:
		try:
			out["wind_speed"] = int(wind_re.group("speed"))
		except Exception:
			out["wind_speed"] = None
		out["is_gust"] = 1 if wind_re.group("gust") else 0

	if out["visibility_meters"] is None:
		out["visibility_meters"] = _extract_visibility_meters(tokens)

	# Phenomena flags
	if any("TS" in token for token in tokens):
		out["has_thunderstorm"] = 1

	if "+RA" in S:
		out["has_heavy_rain"] = 1

	if any(token.endswith("FG") or token.endswith("BR") or token == "FG" or token == "BR" for token in tokens):
		out["has_low_vis_fog"] = 1

	found = _extract_weather_tokens(tokens, vocab)
	out["other_phenomenon"] = ",".join(sorted(set(found)))

	return out


def detect_metar_column(df: pd.DataFrame) -> str | None:
	"""Try to detect which object column contains METAR-like strings."""
	for preferred in ("METAR", "metar"):
		if preferred in df.columns:
			return preferred

	candidates = []
	cols = df.select_dtypes(include=[object]).columns.tolist()
	for c in cols:
		sample = df[c].dropna().astype(str).head(200).tolist()
		if not sample:
			continue
		matches = 0
		for v in sample:
			u = v.upper()
			if (
				u.startswith(("METAR ", "SPECI "))
				or ("KT" in u and re.search(r"\b\d{6}Z\b", u))
				or ("CAVOK" in u)
				or ("SM" in u and " A" in u)
			):
				matches += 1
		ratio = matches / len(sample)
		if ratio >= 0.05:
			candidates.append((ratio, c))
	if not candidates:
		return None
	candidates.sort(reverse=True)
	return candidates[0][1]


def process_file(in_path: Path, out_path: Path) -> None:
	df = pd.read_csv(in_path)
	metar_col = detect_metar_column(df)
	if metar_col is None:
		LOG.warning("No METAR-like column detected in %s, skipping", in_path)
		return

	LOG.info("Detected METAR column '%s' in %s", metar_col, in_path.name)
	feats = df[metar_col].fillna("").astype(str).apply(parse_metar)
	feats_df = pd.DataFrame(feats.tolist(), index=feats.index)
	out_df = pd.concat([df, feats_df], axis=1)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_df.to_csv(out_path, index=False)
	LOG.info("Wrote features to %s", out_path)


def process_all(
	input_dir: Path | None = None, output_dir: Path | None = None, pattern: str = "*.csv"
) -> None:
	repo_root = Path(__file__).resolve().parents[2]
	if input_dir is None:
		input_dir = repo_root / "data" / "2_preprocessed" / "weather"
	if output_dir is None:
		output_dir = repo_root / "data" / "3_feature_engineered" / "weather_stats"

	input_dir = Path(input_dir)
	output_dir = Path(output_dir)
	if not input_dir.exists():
		LOG.error("Input directory %s does not exist", input_dir)
		return

	for p in sorted(input_dir.glob(pattern)):
		out_file = output_dir / p.name
		try:
			process_file(p, out_file)
		except Exception as exc:  # keep processing other files
			LOG.exception("Failed to process %s: %s", p, exc)


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	parser = argparse.ArgumentParser(description="Extract METAR weather features from CSVs")
	parser.add_argument("--input", help="Input weather folder (defaults to data/2_preprocessed/weather)")
	parser.add_argument("--output", help="Output folder (defaults to data/3_feature_engineered/weather_stats)")
	args = parser.parse_args()

	in_dir = Path(args.input) if args.input else None
	out_dir = Path(args.output) if args.output else None
	process_all(in_dir, out_dir)


if __name__ == "__main__":
	main()

