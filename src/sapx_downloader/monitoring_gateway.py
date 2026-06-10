import argparse
import calendar
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import requests

from .html_utils import extract_select_options


BASE_URL = "https://online.coresyssap.com"
CHECK_ROLES_URL = f"{BASE_URL}/user/check_roles/"
CHECK_AUTH_URL = f"{BASE_URL}/user/check_auth_code/"
DO_LOGIN_URL = f"{BASE_URL}/user/do_login"
REPORT_FORM_URL = f"{BASE_URL}/report_monitoring/process_monitoring_gateway"
MONITORING_GATEWAY_PAGE_URL = f"{BASE_URL}/report_monitoring/monitoring_gateway"
DEFAULT_CHECKPOINT = "7"
DEFAULT_BRANCH = os.getenv("CORESYS_BRANCH", "NASIONAL")


@dataclass
class DailyExport:
    date_iso: str
    url: str


@dataclass
class BatchDownloadResult:
    chunk_index: int
    chunk_total: int
    from_date: str
    to_date: str
    output_dir: str
    manifest_path: str
    daily_file_count: int


def fetch_monitoring_gateway_html(session: requests.Session, timeout: int = 60) -> str:
    response = session.get(MONITORING_GATEWAY_PAGE_URL, timeout=(30, timeout))
    response.raise_for_status()
    return response.text


def get_monitoring_gateway_filters(session: requests.Session, timeout: int = 60) -> dict:
    html = fetch_monitoring_gateway_html(session, timeout=timeout)
    return {
        "checkpoints": [
            {
                "value": option.value,
                "label": option.label,
                "attrs": option.attrs,
            }
            for option in extract_select_options(html, "monitoring_squence")
            if option.value
        ],
        "branches": [
            {
                "value": option.value,
                "label": option.label,
                "attrs": option.attrs,
            }
            for option in extract_select_options(html, "branch_code")
            if option.value
        ],
    }


class LoginError(RuntimeError):
    pass


ProgressCallback = Callable[[dict], None]


def format_bytes(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def render_progress_bar(current: int, total: int | None, width: int = 28) -> str:
    if not total or total <= 0:
        return "[" + ("#" * width) + "]"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(width * ratio)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def print_progress(
    label: str,
    current: int,
    total: int | None,
    downloaded_bytes: int | None = None,
    speed_bytes_per_sec: float | None = None,
) -> None:
    bar = render_progress_bar(current, total)
    if total and total > 0:
        percent = f"{(current / total) * 100:6.2f}%"
        counter = f"{current}/{total}"
    else:
        percent = "  --.--%"
        counter = str(current)

    details: list[str] = []
    if downloaded_bytes is not None:
        details.append(format_bytes(downloaded_bytes))
    if speed_bytes_per_sec is not None and speed_bytes_per_sec > 0:
        details.append(f"{format_bytes(int(speed_bytes_per_sec))}/s")

    suffix = f" | {' | '.join(details)}" if details else ""
    message = f"\r{label:<18} {bar} {percent} ({counter}){suffix}"
    print(message, end="", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download daily AWB Scan Incoming XLSX files from SAPX Monitoring Proses."
    )
    parser.add_argument("--year", type=int, help="Target year, e.g. 2026")
    parser.add_argument("--month", type=int, help="Target month, 1-12")
    parser.add_argument("--from-date", default=None, help="Start date in dd-mm-yyyy.")
    parser.add_argument("--to-date", default=None, help="End date in dd-mm-yyyy.")
    parser.add_argument("--username", default=os.getenv("CORESYS_USERNAME"))
    parser.add_argument("--password", default=os.getenv("CORESYS_PASSWORD"))
    parser.add_argument("--pin", default=os.getenv("CORESYS_PIN"))
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--from-time", default="00:00:00")
    parser.add_argument("--to-time", default="23:59:59")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.from_date and args.to_date):
        if args.year is None or args.month is None:
            parser.error("Either provide --from-date/--to-date or provide both --year and --month.")
    return args


def month_date_range(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return f"01-{month:02d}-{year}", f"{last_day:02d}-{month:02d}-{year}"


def resolve_date_range(args: argparse.Namespace) -> tuple[str, str]:
    if args.from_date and args.to_date:
        return args.from_date, args.to_date
    return month_date_range(args.year, args.month)


def ensure_credentials(args: argparse.Namespace) -> None:
    missing = [name for name in ("username", "password", "pin") if not getattr(args, name)]
    if missing:
        raise SystemExit(
            f"Missing credentials: {', '.join(missing)}. "
            "Set them in environment variables or pass them on the command line."
        )


def login(session: requests.Session, username: str, password: str, pin: str, timeout: int) -> None:
    if not re.fullmatch(r"\d{6}", pin):
        raise LoginError("PIN must contain exactly 6 digits.")

    role_response = session.post(
        CHECK_ROLES_URL,
        data={"username": username, "password": password},
        timeout=timeout,
    )
    role_response.raise_for_status()
    role_payload = role_response.json()
    if "data" not in role_payload or "username" not in role_payload:
        raise LoginError(f"Unexpected check_roles response: {role_payload}")

    auth_payload = {"username": username, "secrets": ""}
    for index, digit in enumerate(pin, start=1):
        auth_payload[f"pin{index}"] = digit

    auth_response = session.post(CHECK_AUTH_URL, data=auth_payload, timeout=timeout)
    auth_response.raise_for_status()
    auth_payload_result = auth_response.json()
    if auth_payload_result.get("status") != "yes":
        raise LoginError(f"PIN verification failed: {auth_payload_result}")

    final_payload = {
        "username_auth": role_payload["username"],
        "password_auth": role_payload["data"],
    }
    for index, digit in enumerate(pin, start=1):
        final_payload[f"pin{index}"] = digit

    login_response = session.post(DO_LOGIN_URL, data=final_payload, timeout=timeout)
    login_response.raise_for_status()
    if "Log Out" not in login_response.text and "Profile" not in login_response.text:
        raise LoginError("Authenticated page marker not found after login.")


def fetch_report_page(
    session: requests.Session,
    checkpoint: str,
    date_from: str,
    date_to: str,
    from_time: str,
    to_time: str,
    branch: str,
    timeout: int,
) -> str:
    response = session.post(
        REPORT_FORM_URL,
        data={
            "monitoring_squence": checkpoint,
            "from": date_from,
            "to": date_to,
            "from_time": from_time,
            "to_time": to_time,
            "branch_code": branch,
            "submit": "Cari",
        },
        timeout=(30, timeout),
    )
    response.raise_for_status()
    return response.text


def extract_daily_exports(html: str) -> list[DailyExport]:
    pattern = re.compile(
        r'href="(https://online\.coresyssap\.com/report_monitoring/export_report_gateway/'
        r'(\d{4}-\d{2}-\d{2})/[^"]+)"'
    )
    results: dict[str, DailyExport] = {}
    for match in pattern.finditer(html):
        url, date_iso = match.groups()
        results.setdefault(date_iso, DailyExport(date_iso=date_iso, url=url))
    return sorted(results.values(), key=lambda item: item.date_iso)


def iter_content(response: requests.Response, chunk_size: int = 1024 * 1024) -> Iterable[bytes]:
    for chunk in response.iter_content(chunk_size=chunk_size):
        if chunk:
            yield chunk


def parse_date_string(value: str) -> date:
    return datetime.strptime(value, "%d-%m-%Y").date()


def format_date_string(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def split_date_range(start_date: date, end_date: date, batch_days: int) -> list[tuple[date, date]]:
    if batch_days < 1:
        raise ValueError("batch_days must be at least 1")
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    chunks: list[tuple[date, date]] = []
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=batch_days - 1), end_date)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _emit(progress_callback: ProgressCallback | None, payload: dict) -> None:
    if progress_callback:
        progress_callback(payload)


def sanitize_period_fragment(date_from: str, date_to: str) -> str:
    return f"{date_from.replace('-', '')}_{date_to.replace('-', '')}"


def resolve_output_dir(args: argparse.Namespace, date_from: str, date_to: str) -> Path:
    if args.out_dir:
        return Path(args.out_dir)
    period_fragment = sanitize_period_fragment(date_from, date_to)
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "downloads" / f"monitoring_gateway_{period_fragment}"


def download_export(
    session: requests.Session,
    export: DailyExport,
    out_dir: Path,
    timeout: int,
    skip_existing: bool,
    file_index: int,
    total_files: int,
) -> Path:
    out_path = out_dir / f"awb_scan_incoming_{export.date_iso}.xlsx"
    if skip_existing and out_path.exists():
        print_progress("overall", file_index, total_files)
        print(f"\nskip   {out_path.name}")
        return out_path

    with session.get(export.url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total_bytes = int(response.headers.get("Content-Length", "0")) or None
        downloaded_bytes = 0
        start_time = time.monotonic()

        with out_path.open("wb") as file_handle:
            for chunk in iter_content(response):
                file_handle.write(chunk)
                downloaded_bytes += len(chunk)
                elapsed = max(time.monotonic() - start_time, 0.001)
                speed = downloaded_bytes / elapsed
                print_progress(
                    f"file {file_index}/{total_files}",
                    downloaded_bytes,
                    total_bytes,
                    downloaded_bytes=downloaded_bytes,
                    speed_bytes_per_sec=speed,
                )

    print_progress("overall", file_index, total_files)
    print(f"\nsaved  {out_path.name}")
    return out_path


def _download_export_with_callback(
    session: requests.Session,
    export: DailyExport,
    out_dir: Path,
    timeout: int,
    skip_existing: bool,
    max_retries: int,
    retry_delay: int,
    progress_callback: ProgressCallback | None,
    chunk_index: int,
    chunk_total: int,
    file_index: int,
    file_total: int,
) -> Path:
    out_path = out_dir / f"awb_scan_incoming_{export.date_iso}.xlsx"
    if skip_existing and out_path.exists():
        _emit(
            progress_callback,
            {
                "stage": "file",
                "chunk_index": chunk_index,
                "chunk_total": chunk_total,
                "file_index": file_index,
                "file_total": file_total,
                "file_name": out_path.name,
                "downloaded_bytes": out_path.stat().st_size,
                "total_bytes": out_path.stat().st_size,
                "speed_text": "skip",
            },
        )
        return out_path

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with session.get(export.url, stream=True, timeout=(30, timeout)) as response:
                response.raise_for_status()
                total_bytes = int(response.headers.get("Content-Length", "0")) or None
                downloaded_bytes = 0
                started_at = time.monotonic()

                with out_path.open("wb") as file_handle:
                    for chunk in iter_content(response):
                        file_handle.write(chunk)
                        downloaded_bytes += len(chunk)
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        speed = downloaded_bytes / elapsed
                        _emit(
                            progress_callback,
                            {
                                "stage": "file",
                                "chunk_index": chunk_index,
                                "chunk_total": chunk_total,
                                "file_index": file_index,
                                "file_total": file_total,
                                "file_name": out_path.name,
                                "downloaded_bytes": downloaded_bytes,
                                "total_bytes": total_bytes,
                                "speed_text": f"{format_bytes(int(speed))}/s",
                                "attempt": attempt,
                                "max_retries": max_retries,
                            },
                        )
            return out_path
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if out_path.exists():
                out_path.unlink(missing_ok=True)
            _emit(
                progress_callback,
                {
                    "stage": "retry",
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "file_index": file_index,
                    "file_total": file_total,
                    "file_name": out_path.name,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "message": str(exc),
                },
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
            continue
    if last_error:
        raise last_error
    raise RuntimeError("Download failed without a captured exception.")


def run_monitoring_gateway_batches(
    session: requests.Session,
    checkpoint: str,
    branch: str,
    from_time: str,
    to_time: str,
    start_date: date,
    end_date: date,
    batch_days: int,
    output_root: Path,
    timeout: int = 180,
    skip_existing: bool = False,
    max_retries: int = 3,
    retry_delay: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> list[BatchDownloadResult]:
    chunks = split_date_range(start_date, end_date, batch_days)
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[BatchDownloadResult] = []

    for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        from_text = format_date_string(chunk_start)
        to_text = format_date_string(chunk_end)
        out_dir = output_root / f"monitoring_gateway_{chunk_start.isoformat()}_{chunk_end.isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        _emit(
            progress_callback,
            {
                "stage": "overall",
                "chunk_index": chunk_index,
                "chunk_total": len(chunks),
                "from_date": from_text,
                "to_date": to_text,
            },
        )
        html = fetch_report_page(
            session=session,
            checkpoint=checkpoint,
            date_from=from_text,
            date_to=to_text,
            from_time=from_time,
            to_time=to_time,
            branch=branch,
            timeout=timeout,
        )
        exports = extract_daily_exports(html)
        manifest_path = out_dir / "manifest.txt"
        manifest_path.write_text(
            "\n".join(f"{item.date_iso}\t{item.url}" for item in exports) + ("\n" if exports else ""),
            encoding="utf-8",
        )

        for file_index, export in enumerate(exports, start=1):
            _download_export_with_callback(
                session=session,
                export=export,
                out_dir=out_dir,
                timeout=timeout,
                skip_existing=skip_existing,
                max_retries=max_retries,
                retry_delay=retry_delay,
                progress_callback=progress_callback,
                chunk_index=chunk_index,
                chunk_total=len(chunks),
                file_index=file_index,
                file_total=len(exports),
            )

        results.append(
            BatchDownloadResult(
                chunk_index=chunk_index,
                chunk_total=len(chunks),
                from_date=from_text,
                to_date=to_text,
                output_dir=str(out_dir),
                manifest_path=str(manifest_path),
                daily_file_count=len(exports),
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_credentials(args)
    date_from, date_to = resolve_date_range(args)
    out_dir = resolve_output_dir(args, date_from, date_to)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
            )
        }
    )

    try:
        login(session, args.username, args.password, args.pin, args.timeout)
        html = fetch_report_page(
            session=session,
            checkpoint=args.checkpoint,
            date_from=date_from,
            date_to=date_to,
            from_time=args.from_time,
            to_time=args.to_time,
            branch=args.branch,
            timeout=args.timeout,
        )
        exports = extract_daily_exports(html)
        if not exports:
            print("No daily export links found in report page.", file=sys.stderr)
            return 2

        manifest_path = out_dir / "manifest.txt"
        manifest_path.write_text(
            "\n".join(f"{item.date_iso}\t{item.url}" for item in exports) + "\n",
            encoding="utf-8",
        )

        print(f"Found {len(exports)} daily export link(s) for {date_from} to {date_to}.")
        print_progress("overall", 0, len(exports))
        for index, export in enumerate(exports, start=1):
            download_export(
                session,
                export,
                out_dir,
                args.timeout,
                args.skip_existing,
                index,
                len(exports),
            )
        print(f"Done. Files saved in: {out_dir}")
        return 0
    except (requests.RequestException, LoginError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
