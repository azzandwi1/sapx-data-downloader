import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

import requests

from .auth import BASE_URL
from .html_utils import SelectOption, extract_select_options


PickupExportKind = Literal["report_pickup", "monitoring"]
PickupManualExportKind = Literal["outstanding", "productivity"]
ProgressCallback = Callable[[dict], None]


PICKUP_MONITORING_URL = f"{BASE_URL}/pickup/monitoring_list"
PICKUP_MANUAL_MONITORING_URL = f"{BASE_URL}/pickup_manual/monitoring_list"
DEFAULT_TIMEOUT = 45 * 60


@dataclass
class CustomerOption:
    value: str
    label: str


@dataclass
class ChunkResult:
    chunk_index: int
    chunk_total: int
    from_date: str
    to_date: str
    export_url: str
    saved_path: str
    file_size: int


def _format_date(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def _format_bytes(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    current = float(num_bytes)
    for unit in units:
        if current < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(current)} {unit}"
            return f"{current:.1f} {unit}"
        current /= 1024
    return f"{num_bytes} B"


def _emit(progress_callback: ProgressCallback | None, payload: dict) -> None:
    if progress_callback:
        progress_callback(payload)


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


def sanitize_name(value: str) -> str:
    safe = value.lower().replace(" ", "_").replace("/", "_").replace("\\", "_")
    safe = "".join(ch for ch in safe if ch.isalnum() or ch in ("_", "-", "."))
    return safe or "export"


def fetch_pickup_monitoring_html(session: requests.Session, timeout: int = 60) -> str:
    response = session.get(PICKUP_MONITORING_URL, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_pickup_manual_monitoring_html(session: requests.Session, timeout: int = 60) -> str:
    response = session.get(PICKUP_MANUAL_MONITORING_URL, timeout=timeout)
    response.raise_for_status()
    return response.text


def get_pickup_monitoring_filters(session: requests.Session, timeout: int = 60) -> dict:
    html = fetch_pickup_monitoring_html(session, timeout=timeout)
    return {
        "origin_regions": _options_to_dicts(extract_select_options(html, "branch_area_asal")),
        "origin_branches": _options_to_dicts(extract_select_options(html, "branch_ori")),
        "destination_regions": _options_to_dicts(extract_select_options(html, "branch_area_tujuan")),
        "destination_branches": _options_to_dicts(extract_select_options(html, "branch_dest")),
        "status_pickup": _options_to_dicts(extract_select_options(html, "pilih_status")),
        "status_cod": _options_to_dicts(extract_select_options(html, "is_cod")),
        "pickup_method": _options_to_dicts(extract_select_options(html, "pilih_pickup_place")),
        "grouping_status": _options_to_dicts(extract_select_options(html, "pilih_rowstate")),
        "date_basis": _options_to_dicts(extract_select_options(html, "date_pickup")),
    }


def get_pickup_manual_filters(session: requests.Session, timeout: int = 60) -> dict:
    html = fetch_pickup_manual_monitoring_html(session, timeout=timeout)
    return {
        "origin_regions": _options_to_dicts(extract_select_options(html, "origin_area_branch_code")),
        "origin_branches": _options_to_dicts(extract_select_options(html, "branch_ori")),
        "status_pickup": _options_to_dicts(extract_select_options(html, "pilih_status")),
        "counter_type": _options_to_dicts(extract_select_options(html, "counter_type")),
        "date_basis": _options_to_dicts(extract_select_options(html, "date_pickup")),
    }


def _options_to_dicts(options: list[SelectOption]) -> list[dict]:
    return [asdict(option) for option in options]


def search_pickup_customers(
    session: requests.Session,
    query: str,
    timeout: int = 60,
) -> list[CustomerOption]:
    response = session.get(
        f"{BASE_URL}/pos/show_customer/",
        params={"query": query},
        timeout=timeout,
    )
    response.raise_for_status()
    raw_items = json.loads(response.text)
    results: list[CustomerOption] = []
    for item in raw_items:
        parts = item.split(" - ", 1)
        code = parts[0].strip()
        label = item.strip()
        if code:
            results.append(CustomerOption(value=f"{code}-X", label=label))
    return results


def search_pickup_manual_customers(
    session: requests.Session,
    query: str,
    timeout: int = 60,
) -> list[CustomerOption]:
    response = session.get(
        f"{BASE_URL}/pickup_manual/show_customer_detail",
        params={"query": query},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PICKUP_MANUAL_MONITORING_URL,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    raw_items = response.json()
    return [
        CustomerOption(
            value=item["others"]["customer_code"],
            label=item["label"],
        )
        for item in raw_items
        if item.get("others", {}).get("customer_code")
    ]


def build_pickup_export_url(export_kind: PickupExportKind, filters: dict, from_date: str, to_date: str) -> str:
    endpoint = {
        "report_pickup": "report_monitoring_zilingo_xlsx",
        "monitoring": "report_monitoring_xlsx",
    }[export_kind]
    token = {"report_pickup": "token_01", "monitoring": "token_02"}[export_kind]

    parts = [
        filters.get("customer", "-"),
        from_date,
        to_date,
        filters.get("pickup_status", "-"),
        filters.get("koli", "-"),
        filters.get("kilo", "-"),
        filters.get("cod_status", "-"),
        filters.get("origin_branch", "-"),
        filters.get("destination_branch", "-"),
        filters.get("pickup_method", "-"),
        filters.get("grouping_status", "-"),
        filters.get("date_basis", "-"),
        filters.get("origin_region", "-"),
        filters.get("destination_region", "-"),
    ]
    return f"{BASE_URL}/pickup/{endpoint}/{'/'.join(parts)}?token={token}"


def build_pickup_manual_export_url(
    export_kind: PickupManualExportKind,
    filters: dict,
    from_date: str,
    to_date: str,
) -> str:
    endpoint = {
        "outstanding": "report_monitoring",
        "productivity": "report_monitoring_v2",
    }[export_kind]

    parts = [
        filters.get("customer", "-"),
        from_date,
        to_date,
        filters.get("pickup_status", "-"),
        filters.get("koli", "-"),
        filters.get("kilo", "-"),
        filters.get("counter_type", "-"),
        filters.get("origin_branch", "-"),
        filters.get("destination_branch", "-"),
        filters.get("date_basis", "-"),
        filters.get("origin_region", "-"),
        filters.get("destination_region", "-"),
    ]
    return f"{BASE_URL}/pickup_manual/{endpoint}/{'/'.join(parts)}"


def _download_file(
    session: requests.Session,
    url: str,
    output_path: Path,
    timeout: int,
    max_retries: int,
    retry_delay: int,
    progress_callback: ProgressCallback | None,
    chunk_index: int,
    chunk_total: int,
) -> int:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with session.get(url, stream=True, timeout=(30, timeout)) as response:
                response.raise_for_status()
                total_bytes = int(response.headers.get("Content-Length", "0")) or None
                downloaded_bytes = 0
                started_at = time.monotonic()

                with output_path.open("wb") as file_handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
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
                                "downloaded_bytes": downloaded_bytes,
                                "total_bytes": total_bytes,
                                "speed_text": f"{_format_bytes(int(speed))}/s",
                                "attempt": attempt,
                                "max_retries": max_retries,
                                "file_name": output_path.name,
                            },
                        )
                return downloaded_bytes
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            _emit(
                progress_callback,
                {
                    "stage": "retry",
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
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


def run_pickup_export_batches(
    session: requests.Session,
    export_kind: PickupExportKind,
    filters: dict,
    start_date: date,
    end_date: date,
    batch_days: int,
    output_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
    retry_delay: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> list[ChunkResult]:
    chunks = split_date_range(start_date, end_date, batch_days)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ChunkResult] = []

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        from_text = _format_date(chunk_start)
        to_text = _format_date(chunk_end)
        url = build_pickup_export_url(export_kind, filters, from_text, to_text)
        export_label = sanitize_name(export_kind)
        output_path = output_dir / f"pickup_{export_label}_{chunk_start.isoformat()}_{chunk_end.isoformat()}.xlsx"
        _emit(progress_callback, {"stage": "overall", "chunk_index": index, "chunk_total": len(chunks), "from_date": from_text, "to_date": to_text})
        file_size = _download_file(
            session,
            url,
            output_path,
            timeout,
            max_retries,
            retry_delay,
            progress_callback,
            index,
            len(chunks),
        )
        results.append(
            ChunkResult(
                chunk_index=index,
                chunk_total=len(chunks),
                from_date=from_text,
                to_date=to_text,
                export_url=url,
                saved_path=str(output_path),
                file_size=file_size,
            )
        )
    return results


def run_pickup_manual_export_batches(
    session: requests.Session,
    export_kind: PickupManualExportKind,
    filters: dict,
    start_date: date,
    end_date: date,
    batch_days: int,
    output_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
    retry_delay: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> list[ChunkResult]:
    chunks = split_date_range(start_date, end_date, batch_days)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ChunkResult] = []

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        from_text = _format_date(chunk_start)
        to_text = _format_date(chunk_end)
        url = build_pickup_manual_export_url(export_kind, filters, from_text, to_text)
        export_label = sanitize_name(export_kind)
        output_path = output_dir / f"pickup_manual_{export_label}_{chunk_start.isoformat()}_{chunk_end.isoformat()}.xlsx"
        _emit(progress_callback, {"stage": "overall", "chunk_index": index, "chunk_total": len(chunks), "from_date": from_text, "to_date": to_text})
        file_size = _download_file(
            session,
            url,
            output_path,
            timeout,
            max_retries,
            retry_delay,
            progress_callback,
            index,
            len(chunks),
        )
        results.append(
            ChunkResult(
                chunk_index=index,
                chunk_total=len(chunks),
                from_date=from_text,
                to_date=to_text,
                export_url=url,
                saved_path=str(output_path),
                file_size=file_size,
            )
        )
    return results


def parse_date_string(value: str) -> date:
    return datetime.strptime(value, "%d-%m-%Y").date()
