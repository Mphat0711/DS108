from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.9+ has zoneinfo.
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


def find_project_root(start: Path) -> Path:
    for path in (start, *start.parents):
        if (path / "requirements.txt").exists() and (path / "config" / "crawler_schedule.json").exists():
            return path
    return start.parents[1]


ROOT = find_project_root(Path(__file__).resolve())
DEFAULT_CONFIG = ROOT / "config" / "crawler_schedule.json"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    description: str
    enabled: bool
    script: Path
    cwd: Path
    timeout_seconds: int | None
    schedule: dict[str, Any]
    args: list[str]
    python: str
    env: dict[str, str]


@dataclass(frozen=True)
class DueJob:
    job: Job
    mark: str
    mark_time: datetime


def resolve_path(value: str | Path, base: Path = ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_timezone(name: str):
    if name.upper() in {"UTC", "Z"}:
        return timezone.utc

    offset_match = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", name, re.IGNORECASE)
    if offset_match:
        sign, hours, minutes = offset_match.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
        if sign == "-":
            delta = -delta
        return timezone(delta, name)

    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass

    fixed_offsets = {
        "Asia/Ho_Chi_Minh": timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh"),
        "Asia/Bangkok": timezone(timedelta(hours=7), "Asia/Bangkok"),
    }
    if name in fixed_offsets:
        return fixed_offsets[name]

    raise ConfigError(
        f"Unknown timezone '{name}'. Install tzdata or use an offset like UTC+07:00."
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Config root must be a JSON object.")
    return data


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "jobs": {}}

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid state JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"State file must contain a JSON object: {path}")
    data.setdefault("version", 1)
    data.setdefault("jobs", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, path)


def parse_jobs(config: dict[str, Any]) -> list[Job]:
    raw_jobs = config.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ConfigError("Config must contain a non-empty 'jobs' list.")

    seen_ids: set[str] = set()
    jobs: list[Job] = []

    for index, raw in enumerate(raw_jobs, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"Job #{index} must be an object.")

        job_id = str(raw.get("id", "")).strip()
        if not job_id:
            raise ConfigError(f"Job #{index} is missing 'id'.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            raise ConfigError(f"Job id '{job_id}' may only contain letters, numbers, dot, dash, or underscore.")
        if job_id in seen_ids:
            raise ConfigError(f"Duplicate job id: {job_id}")
        seen_ids.add(job_id)

        script_value = raw.get("script")
        if not script_value:
            raise ConfigError(f"Job '{job_id}' is missing 'script'.")
        script = resolve_path(script_value)

        cwd = resolve_path(raw.get("cwd", "."))
        timeout_minutes = raw.get("timeout_minutes")
        timeout_seconds = None
        if timeout_minutes is not None:
            timeout_seconds = int(float(timeout_minutes) * 60)
            if timeout_seconds <= 0:
                raise ConfigError(f"Job '{job_id}' has invalid timeout_minutes.")

        schedule = raw.get("schedule")
        if not isinstance(schedule, dict):
            raise ConfigError(f"Job '{job_id}' is missing schedule object.")
        validate_schedule(job_id, schedule)

        args = raw.get("args", [])
        if not isinstance(args, list):
            raise ConfigError(f"Job '{job_id}' args must be a list.")

        env = raw.get("env", {})
        if not isinstance(env, dict):
            raise ConfigError(f"Job '{job_id}' env must be an object.")

        jobs.append(
            Job(
                id=job_id,
                description=str(raw.get("description", "")),
                enabled=bool(raw.get("enabled", True)),
                script=script,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                schedule=schedule,
                args=[str(item) for item in args],
                python=str(raw.get("python") or sys.executable),
                env={str(key): str(value) for key, value in env.items()},
            )
        )

    return jobs


def validate_schedule(job_id: str, schedule: dict[str, Any]) -> None:
    schedule_type = schedule.get("type")
    if schedule_type in {"daily_times", "fixed_times", "times"}:
        times = schedule.get("times")
        if not isinstance(times, list) or not times:
            raise ConfigError(f"Job '{job_id}' daily_times schedule requires non-empty 'times'.")
        for value in times:
            parse_hhmm(str(value))
        allowed_days(schedule)
        return

    if schedule_type == "interval":
        parse_interval(schedule)
        if "anchor" in schedule:
            # Validation of timezone-aware parsing happens later with the selected config timezone.
            str(schedule["anchor"])
        return

    raise ConfigError(
        f"Job '{job_id}' has unsupported schedule type '{schedule_type}'. "
        "Use 'daily_times' or 'interval'."
    )


def parse_hhmm(value: str) -> clock_time:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise ConfigError(f"Invalid time '{value}'. Expected HH:MM.")

    hour = int(match.group(1))
    minute = int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"Invalid time '{value}'. Expected HH:MM in a 24-hour day.")

    return clock_time(hour=hour, minute=minute)


def allowed_days(schedule: dict[str, Any]) -> set[int]:
    raw_days = schedule.get("days")
    if raw_days is None:
        return set(range(7))
    if not isinstance(raw_days, list) or not raw_days:
        raise ConfigError("Schedule days must be a non-empty list when provided.")

    out: set[int] = set()
    for raw_day in raw_days:
        day = str(raw_day).strip().lower()
        if day.isdigit():
            index = int(day)
            if 0 <= index <= 6:
                out.add(index)
                continue
        if day in DAY_NAMES:
            out.add(DAY_NAMES.index(day))
            continue
        raise ConfigError(f"Invalid day '{raw_day}'. Use mon..sun or 0..6.")
    return out


def parse_interval(schedule: dict[str, Any]) -> timedelta:
    units = {
        "every_seconds": "seconds",
        "every_minutes": "minutes",
        "every_hours": "hours",
        "every_days": "days",
        "every_weeks": "weeks",
    }
    values: dict[str, float] = {}
    for config_key, delta_key in units.items():
        if config_key in schedule:
            values[delta_key] = float(schedule[config_key])

    if not values:
        raise ConfigError("Interval schedule requires one of every_minutes, every_hours, or every_days.")

    interval = timedelta(**values)
    if interval.total_seconds() <= 0:
        raise ConfigError("Interval schedule must be greater than zero.")
    return interval


def parse_local_datetime(value: str, tz) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ConfigError(f"Invalid datetime '{value}'. Use ISO format, for example 2026-01-01T02:00:00+07:00.") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def local_midnight_with_time(day: date, value: clock_time, tz) -> datetime:
    return datetime.combine(day, value, tzinfo=tz)


def mark(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def last_attempted_mark(state: dict[str, Any], job_id: str) -> str | None:
    jobs = state.get("jobs", {})
    if not isinstance(jobs, dict):
        return None
    job_state = jobs.get(job_id, {})
    if not isinstance(job_state, dict):
        return None
    value = job_state.get("last_attempted_mark")
    return str(value) if value else None


def due_daily_times(job: Job, now: datetime, state: dict[str, Any], tz) -> DueJob | None:
    times = sorted(parse_hhmm(str(item)) for item in job.schedule["times"])
    day_filter = allowed_days(job.schedule)
    candidates: list[datetime] = []

    for offset in (-1, 0):
        candidate_date = (now + timedelta(days=offset)).date()
        if candidate_date.weekday() not in day_filter:
            continue
        for item in times:
            candidate = local_midnight_with_time(candidate_date, item, tz)
            if candidate <= now:
                candidates.append(candidate)

    if not candidates:
        return None

    due_at = max(candidates)
    due_mark = mark(due_at)
    if due_mark == last_attempted_mark(state, job.id):
        return None
    return DueJob(job=job, mark=due_mark, mark_time=due_at)


def next_daily_time(job: Job, now: datetime, tz) -> datetime:
    times = sorted(parse_hhmm(str(item)) for item in job.schedule["times"])
    day_filter = allowed_days(job.schedule)
    candidates: list[datetime] = []

    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        if candidate_date.weekday() not in day_filter:
            continue
        for item in times:
            candidate = local_midnight_with_time(candidate_date, item, tz)
            if candidate > now:
                candidates.append(candidate)

    if not candidates:
        raise ConfigError(f"Cannot compute next run for job '{job.id}'.")
    return min(candidates)


def interval_anchor(job: Job, now: datetime, tz) -> datetime:
    raw_anchor = job.schedule.get("anchor")
    if raw_anchor:
        return parse_local_datetime(str(raw_anchor), tz)
    return now


def due_interval(job: Job, now: datetime, state: dict[str, Any], tz) -> DueJob | None:
    interval = parse_interval(job.schedule)
    anchor = interval_anchor(job, now, tz)
    if now < anchor:
        return None

    elapsed_seconds = (now - anchor).total_seconds()
    slots = int(elapsed_seconds // interval.total_seconds())
    due_at = anchor + (interval * slots)
    due_mark = mark(due_at)

    if due_mark == last_attempted_mark(state, job.id):
        return None
    return DueJob(job=job, mark=due_mark, mark_time=due_at)


def next_interval_time(job: Job, now: datetime, state: dict[str, Any], tz) -> datetime:
    interval = parse_interval(job.schedule)
    anchor = interval_anchor(job, now, tz)
    last_mark = last_attempted_mark(state, job.id)

    if now < anchor:
        return anchor

    elapsed_seconds = (now - anchor).total_seconds()
    slots = int(elapsed_seconds // interval.total_seconds())
    current_slot = anchor + (interval * slots)
    if mark(current_slot) != last_mark:
        return current_slot
    return current_slot + interval


def due_job(job: Job, now: datetime, state: dict[str, Any], tz) -> DueJob | None:
    schedule_type = job.schedule["type"]
    if schedule_type in {"daily_times", "fixed_times", "times"}:
        return due_daily_times(job, now, state, tz)
    if schedule_type == "interval":
        return due_interval(job, now, state, tz)
    raise ConfigError(f"Unsupported schedule type for job '{job.id}': {schedule_type}")


def next_run_time(job: Job, now: datetime, state: dict[str, Any], tz) -> datetime:
    schedule_type = job.schedule["type"]
    if schedule_type in {"daily_times", "fixed_times", "times"}:
        due = due_daily_times(job, now, state, tz)
        return due.mark_time if due else next_daily_time(job, now, tz)
    if schedule_type == "interval":
        return next_interval_time(job, now, state, tz)
    raise ConfigError(f"Unsupported schedule type for job '{job.id}': {schedule_type}")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "job"


def command_for(job: Job) -> list[str]:
    return [job.python, str(job.script), *job.args]


def validate_runtime_paths(jobs: list[Job]) -> None:
    for job in jobs:
        if not job.script.exists():
            raise ConfigError(f"Script for job '{job.id}' not found: {job.script}")
        if not job.cwd.exists():
            raise ConfigError(f"Working directory for job '{job.id}' not found: {job.cwd}")


def run_job(
    due: DueJob,
    state: dict[str, Any],
    state_path: Path,
    log_dir: Path,
    dry_run: bool = False,
    manual: bool = False,
) -> int:
    job = due.job
    cmd = command_for(job)
    display_cmd = shlex.join(cmd)

    if dry_run:
        logging.info("[dry-run] %s would run at mark %s: %s", job.id, due.mark, display_cmd)
        return 0

    log_dir.mkdir(parents=True, exist_ok=True)
    started_utc = datetime.now(timezone.utc)
    log_file = log_dir / f"{started_utc.strftime('%Y%m%d_%H%M%S')}_{safe_name(job.id)}.log"

    job_state = state.setdefault("jobs", {}).setdefault(job.id, {})
    if manual:
        job_state["last_manual_started_at"] = started_utc.isoformat(timespec="seconds")
    else:
        job_state["last_attempted_mark"] = due.mark
        job_state["last_scheduled_started_at"] = started_utc.isoformat(timespec="seconds")
    job_state["status"] = "running"
    job_state["last_log_file"] = str(log_file.relative_to(ROOT))
    save_state(state_path, state)

    logging.info("Starting job '%s' for mark %s", job.id, due.mark)
    logging.info("Log file: %s", log_file)

    env = os.environ.copy()
    env.update(job.env)
    env.setdefault("PYTHONUNBUFFERED", "1")

    exit_code = 0
    with log_file.open("w", encoding="utf-8") as fh:
        fh.write(f"job_id: {job.id}\n")
        fh.write(f"mode: {'manual' if manual else 'scheduled'}\n")
        fh.write(f"scheduled_mark: {due.mark}\n")
        fh.write(f"started_at_utc: {started_utc.isoformat(timespec='seconds')}\n")
        fh.write(f"cwd: {job.cwd}\n")
        fh.write(f"command: {display_cmd}\n")
        fh.write("-" * 80 + "\n")
        fh.flush()

        try:
            completed = subprocess.run(
                cmd,
                cwd=str(job.cwd),
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=job.timeout_seconds,
                check=False,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            fh.write("\n")
            fh.write(f"TIMEOUT after {job.timeout_seconds} seconds\n")
        except OSError as exc:
            exit_code = 127
            fh.write("\n")
            fh.write(f"FAILED TO START: {exc}\n")

    finished_utc = datetime.now(timezone.utc)
    job_state["status"] = "success" if exit_code == 0 else "failed"
    job_state["last_finished_at"] = finished_utc.isoformat(timespec="seconds")
    job_state["last_exit_code"] = exit_code
    job_state["runs"] = int(job_state.get("runs", 0)) + 1
    save_state(state_path, state)

    if exit_code == 0:
        logging.info("Finished job '%s' successfully.", job.id)
    else:
        logging.error("Job '%s' failed with exit code %s.", job.id, exit_code)
    return exit_code


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "crawler_orchestrator.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def load_runtime(args: argparse.Namespace) -> tuple[dict[str, Any], list[Job], Any, Path, Path, int]:
    config_path = resolve_path(args.config)
    config = read_json(config_path)
    tz = load_timezone(str(config.get("timezone", "UTC")))
    jobs = parse_jobs(config)
    validate_runtime_paths(jobs)

    state_path = resolve_path(args.state or config.get("state_file", "logs/crawler_state.json"))
    log_dir = resolve_path(args.log_dir or config.get("log_dir", "logs/crawler_runs"))
    poll_seconds = int(args.poll_seconds or config.get("poll_seconds", 60))
    if poll_seconds <= 0:
        raise ConfigError("poll_seconds must be greater than zero.")

    return config, jobs, tz, state_path, log_dir, poll_seconds


def print_schedule(jobs: list[Job], state: dict[str, Any], tz) -> None:
    now = datetime.now(tz)
    print(f"Current time: {now.isoformat(timespec='minutes')}")
    print()
    print(f"{'ID':<18} {'ENABLED':<8} {'TYPE':<12} {'STATUS':<10} {'TIME'}")
    print("-" * 84)
    for job in jobs:
        if job.enabled:
            due = due_job(job, now, state, tz)
            status = "due" if due else "scheduled"
            next_time = (due.mark_time if due else next_run_time(job, now, state, tz)).isoformat(timespec="minutes")
        else:
            status = "disabled"
            next_time = "-"
        print(f"{job.id:<18} {str(job.enabled):<8} {job.schedule['type']:<12} {status:<10} {next_time}")


def selected_jobs(jobs: list[Job], ids: list[str]) -> list[Job]:
    if len(ids) == 1 and ids[0].lower() == "all":
        return jobs

    lookup = {job.id: job for job in jobs}
    missing = [job_id for job_id in ids if job_id not in lookup]
    if missing:
        raise ConfigError(f"Unknown job id(s): {', '.join(missing)}")
    return [lookup[job_id] for job_id in ids]


def run_manual(
    jobs: list[Job],
    ids: list[str],
    state: dict[str, Any],
    state_path: Path,
    log_dir: Path,
    dry_run: bool,
    tz,
) -> int:
    now = datetime.now(tz)
    exit_codes: list[int] = []
    for job in selected_jobs(jobs, ids):
        due = DueJob(job=job, mark=f"manual:{now.isoformat(timespec='minutes')}", mark_time=now)
        exit_codes.append(run_job(due, state, state_path, log_dir, dry_run=dry_run, manual=True))
    return 0 if all(code == 0 for code in exit_codes) else 1


def run_due_once(
    jobs: list[Job],
    state: dict[str, Any],
    state_path: Path,
    log_dir: Path,
    dry_run: bool,
    tz,
) -> int:
    now = datetime.now(tz)
    due_items = [item for job in jobs if job.enabled for item in [due_job(job, now, state, tz)] if item]

    if not due_items:
        logging.info("No crawler is due at %s.", now.isoformat(timespec="minutes"))
        return 0

    exit_codes: list[int] = []
    for due in sorted(due_items, key=lambda item: item.mark_time):
        exit_codes.append(run_job(due, state, state_path, log_dir, dry_run=dry_run))
    return 0 if all(code == 0 for code in exit_codes) else 1


def daemon_loop(
    jobs: list[Job],
    state_path: Path,
    log_dir: Path,
    dry_run: bool,
    tz,
    poll_seconds: int,
) -> int:
    logging.info("Crawler orchestrator started. Poll interval: %s seconds.", poll_seconds)
    logging.info("Press Ctrl+C to stop.")

    try:
        while True:
            state = load_state(state_path)
            run_due_once(jobs, state, state_path, log_dir, dry_run, tz)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logging.info("Crawler orchestrator stopped by user.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coordinate DS108 crawler scripts without modifying source/0_crawler."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to crawler schedule JSON.")
    parser.add_argument("--state", help="Override state file path.")
    parser.add_argument("--log-dir", help="Override run log directory.")
    parser.add_argument("--poll-seconds", type=int, help="Override scheduler polling interval.")
    parser.add_argument("--list", action="store_true", help="Print configured jobs and next run times.")
    parser.add_argument("--check-config", action="store_true", help="Validate config and exit.")
    parser.add_argument("--once", action="store_true", help="Check due jobs once, then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without launching scripts.")
    parser.add_argument("--run", nargs="+", metavar="JOB_ID", help="Run one or more jobs immediately. Use 'all' for every job.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        _, jobs, tz, state_path, log_dir, poll_seconds = load_runtime(args)

        if args.check_config:
            print(f"Config is valid. Jobs loaded: {len(jobs)}")
            return 0

        if args.list:
            state = load_state(state_path)
            print_schedule(jobs, state, tz)
            return 0

        setup_logging(log_dir)
        state = load_state(state_path)

        if args.run:
            return run_manual(jobs, args.run, state, state_path, log_dir, args.dry_run, tz)

        if args.once:
            return run_due_once(jobs, state, state_path, log_dir, args.dry_run, tz)

        return daemon_loop(jobs, state_path, log_dir, args.dry_run, tz, poll_seconds)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
