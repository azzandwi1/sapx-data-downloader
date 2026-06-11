import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from email.message import Message
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree
from zipfile import ZipFile

import requests

from .auth import BASE_URL
from .html_utils import SelectOption, extract_select_options


ProgressCallback = Callable[[dict], None]

POD_REPORT_V2_URL = f"{BASE_URL}/pod_report_v2/"
POD_REPORT_V2_CUSTOMER_URL = f"{BASE_URL}/pod_report_v2/get_customer_list"
POD_REPORT_V2_EXIST_URL = f"{BASE_URL}/pod_report_v2/is_report_exist"
POD_REPORT_V2_DETAIL_URL = f"{BASE_URL}/pod_report_v2/get_process_detail"
POD_REPORT_V2_GENERATE_URL = "https://report-js.coresyssap.com/report/pod"
POD_REPORT_V2_DOWNLOAD_ROOT = "https://report-js.coresyssap.com/download_report"

POD_BY_AWB_URL = f"{BASE_URL}/report/pod_by_awb"
POD_BY_AWB_EXPORT_URL = "https://report02-aws-jkt.coresyssap.com/report_pod/export_report_pod_by_awb/"
DEFAULT_TIMEOUT = 45 * 60
DEFAULT_MAX_WORKERS = 4


@dataclass
class CustomerOption:
    value: str
    label: str


@dataclass
class PodV2Result:
    chunk_index: int
    chunk_total: int
    from_date: str
    to_date: str
    process_id: str
    document_name: str
    saved_path: str
    file_size: int


@dataclass
class PodByAwbResult:
    chunk_index: int
    chunk_total: int
    awb_count: int
    first_awb: str
    last_awb: str
    saved_path: str
    file_size: int
    data_row_count: int


def _emit(progress_callback: ProgressCallback | None, payload: dict) -> None:
    if progress_callback:
        progress_callback(payload)


def _clone_session(session: requests.Session) -> requests.Session:
    cloned = requests.Session()
    cloned.headers.update(session.headers)
    cloned.cookies.update(session.cookies)
    return cloned


def _format_date_for_pod(value: date) -> str:
    return value.strftime("%Y-%m-%d")


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


def _options_to_dicts(options: list[SelectOption]) -> list[dict]:
    return [asdict(option) for option in options]


def _extract_js_string(page_html: str, pattern: str) -> str:
    match = re.search(pattern, page_html, re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError(f"Gagal menemukan token JS untuk pola: {pattern}")
    return match.group(1)


def _extract_userdata(page_html: str) -> str:
    match = re.search(r"userdata:\s*'((?:\\'|[^'])*)'", page_html, re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError("Gagal menemukan payload userdata di halaman POD V2.")
    return match.group(1)


def fetch_pod_report_v2_html(session: requests.Session, timeout: int = 60) -> str:
    response = session.get(POD_REPORT_V2_URL, timeout=(30, timeout))
    response.raise_for_status()
    return response.text


def fetch_pod_by_awb_html(session: requests.Session, timeout: int = 60) -> str:
    response = session.get(POD_BY_AWB_URL, timeout=(30, timeout))
    response.raise_for_status()
    return response.text


def get_pod_report_v2_filters(session: requests.Session, timeout: int = 60) -> dict:
    html = fetch_pod_report_v2_html(session, timeout=timeout)
    return {
        "export_date_type": _options_to_dicts(extract_select_options(html, "export_date_type")),
        "origin_area_branch_code": _options_to_dicts(extract_select_options(html, "origin_area_branch_code")),
        "origin_branch_code": _options_to_dicts(extract_select_options(html, "origin_branch_code")),
        "destination_area_branch_code": _options_to_dicts(extract_select_options(html, "destination_area_branch_code")),
        "destination_branch_code": _options_to_dicts(extract_select_options(html, "destination_branch_code")),
        "report_type_code": _options_to_dicts(extract_select_options(html, "report_type_code")),
        "pod_status_code": _options_to_dicts(extract_select_options(html, "pod_status_code")),
        "transaction_type_code": _options_to_dicts(extract_select_options(html, "transaction_type_code")),
        "service_type_code": _options_to_dicts(extract_select_options(html, "service_type_code")),
        "courier_code": _options_to_dicts(extract_select_options(html, "courier_code")),
        "transportation_code": _options_to_dicts(extract_select_options(html, "transportation_code")),
        "opt_insurance": _options_to_dicts(extract_select_options(html, "opt_insurance")),
        "customer_div": _options_to_dicts(extract_select_options(html, "customer_div")),
        "flag_return": _options_to_dicts(extract_select_options(html, "flag_return")),
        "flag_rowstate": _options_to_dicts(extract_select_options(html, "flag_rowstate")),
        "shipment_type_code": _options_to_dicts(extract_select_options(html, "shipment_type_code")),
        "awb_type": _options_to_dicts(extract_select_options(html, "awb_type")),
        "auth_token": _extract_js_string(html, r"let\s+auth_token\s*=\s*'([^']+)'"),
        "is_encrypted": _extract_js_string(html, r"let\s+is_encrypted\s*=\s*'([^']+)'"),
        "userdata": _extract_userdata(html),
    }


def get_pod_by_awb_filters(session: requests.Session, timeout: int = 60) -> dict:
    html = fetch_pod_by_awb_html(session, timeout=timeout)
    return {
        "key": _options_to_dicts(extract_select_options(html, "key")),
    }


def search_pod_v2_customers(
    session: requests.Session,
    query: str,
    timeout: int = 60,
) -> list[CustomerOption]:
    response = session.post(
        POD_REPORT_V2_CUSTOMER_URL,
        data={"customer": query},
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": POD_REPORT_V2_URL},
        timeout=(30, timeout),
    )
    response.raise_for_status()
    items = response.json()
    results: list[CustomerOption] = []
    for item in items:
        item_id = str(item.get("id", "")).strip()
        item_name = str(item.get("name", "")).strip()
        if item_id and item_name:
            results.append(CustomerOption(value=item_name, label=item_name))
    return results


def split_date_range(start_date: date, end_date: date, batch_days: int) -> list[tuple[date, date]]:
    if batch_days < 1:
        raise ValueError("batch_days harus minimal 1.")
    if end_date < start_date:
        raise ValueError("Tanggal akhir tidak boleh lebih kecil dari tanggal awal.")

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


def _chunk_awbs(awbs: list[str], chunk_size: int) -> list[list[str]]:
    return [awbs[index:index + chunk_size] for index in range(0, len(awbs), chunk_size)]


def normalize_awb_lines(raw_awbs: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for raw_line in raw_awbs.replace("\r", "\n").split("\n"):
        value = raw_line.strip()
        if not value:
            continue
        if value not in seen:
            seen.add(value)
            results.append(value)
    return results


def _build_pod_v2_payload(filters: dict) -> dict:
    customer_code = (filters.get("customer_code") or "").strip()
    customer_code = customer_code.split(" - ", 1)[0].strip() if customer_code else "1"
    tgl_terima = (filters.get("tgl_terima") or "").strip() or "1"
    awb_master_id = (filters.get("awb_master_id") or "").strip() or "1"
    return {
        "export_date_type": filters["export_date_type"],
        "from": filters["from"],
        "to": filters["to"],
        "tgl_terima": tgl_terima,
        "origin_branch_code": filters["origin_branch_code"],
        "destination_branch_code": filters["destination_branch_code"],
        "customer_code": customer_code or "1",
        "pod_status_code": filters["pod_status_code"],
        "report_type_code": filters["report_type_code"],
        "transaction_type_code": filters["transaction_type_code"],
        "transportation_code": filters["transportation_code"],
        "opt_insurance": filters["opt_insurance"],
        "awb_master_id": awb_master_id,
        "service_type_code": filters["service_type_code"],
        "customer_div": filters["customer_div"],
        "flag_return": filters["flag_return"],
        "flag_rowstate": filters["flag_rowstate"],
        "origin_area_branch_code": filters["origin_area_branch_code"],
        "destination_area_branch_code": filters["destination_area_branch_code"],
        "shipment_type_code": filters["shipment_type_code"],
        "awb_type": filters["awb_type"],
        "is_encrypted": filters["is_encrypted"],
    }


def _extract_filename_from_headers(headers: requests.structures.CaseInsensitiveDict) -> str | None:
    content_disposition = headers.get("Content-Disposition")
    if not content_disposition:
        return None
    message = Message()
    message["content-disposition"] = content_disposition
    file_name = message.get_param("filename", header="content-disposition")
    if not file_name:
        match = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
        if match:
            file_name = match.group(1)
    return file_name


def _count_xlsx_data_rows(path: Path, header_rows: int = 3) -> int:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
    root = ElementTree.fromstring(sheet_xml)
    rows = root.findall(".//main:sheetData/main:row", namespace)
    data_rows = 0
    for row in rows:
        row_index = int(row.attrib.get("r", "0") or "0")
        if row_index <= header_rows:
            continue
        has_value = False
        for cell in row.findall("main:c", namespace):
            if cell.find("main:v", namespace) is not None or cell.find("main:is", namespace) is not None:
                has_value = True
                break
        if has_value:
            data_rows += 1
    return data_rows


def _download_stream_to_path(
    session: requests.Session,
    url: str,
    output_path: Path,
    timeout: int,
    max_retries: int,
    retry_delay: int,
    progress_callback: ProgressCallback | None,
    chunk_index: int,
    chunk_total: int,
    file_label: str,
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
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        if not block:
                            continue
                        file_handle.write(block)
                        downloaded_bytes += len(block)
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        speed = downloaded_bytes / elapsed
                        _emit(
                            progress_callback,
                            {
                                "stage": "file",
                                "chunk_index": chunk_index,
                                "chunk_total": chunk_total,
                                "file_name": file_label,
                                "downloaded_bytes": downloaded_bytes,
                                "total_bytes": total_bytes,
                                "speed_text": f"{_format_bytes(int(speed))}/s",
                                "attempt": attempt,
                                "max_retries": max_retries,
                            },
                        )
                return downloaded_bytes
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
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
    if last_error:
        raise last_error
    raise RuntimeError("Download gagal tanpa exception yang tertangkap.")


def _poll_pod_v2_process(
    session: requests.Session,
    process_id: str,
    timeout: int,
    max_retries: int,
    retry_delay: int,
    poll_interval: int,
    poll_timeout: int,
    progress_callback: ProgressCallback | None,
    chunk_index: int,
    chunk_total: int,
) -> dict:
    deadline = time.monotonic() + poll_timeout
    started_at = time.monotonic()
    attempt = 0
    last_error: Exception | None = None

    while time.monotonic() <= deadline:
        attempt += 1
        try:
            response = session.post(
                POD_REPORT_V2_DETAIL_URL,
                data={"id": process_id},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": POD_REPORT_V2_URL},
                timeout=(30, timeout),
            )
            response.raise_for_status()
            payload = response.json()
            rowstate = str(payload.get("rowstate", ""))
            status_message = payload.get("status_message") or "-"
            _emit(
                progress_callback,
                {
                    "stage": "status",
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "message": f"Process {process_id} | rowstate={rowstate} | status={status_message}",
                    "process_id": process_id,
                    "rowstate": rowstate,
                    "status_message": status_message,
                    "elapsed_seconds": min(time.monotonic() - started_at, poll_timeout),
                    "timeout_seconds": poll_timeout,
                },
            )
            if rowstate == "2":
                return payload
            if rowstate not in {"1", "2"} and status_message != "-":
                return payload
            time.sleep(poll_interval)
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
            last_error = exc
            retry_no = min(attempt, max_retries)
            _emit(
                progress_callback,
                {
                    "stage": "retry",
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "attempt": retry_no,
                    "max_retries": max_retries,
                    "message": f"Polling process {process_id}: {exc}",
                },
            )
            time.sleep(retry_delay)

    if last_error:
        raise last_error
    raise TimeoutError(f"Process {process_id} belum selesai dalam {poll_timeout} detik.")


def _start_or_reuse_pod_v2_process(
    session: requests.Session,
    filters: dict,
    timeout: int,
    max_retries: int,
    retry_delay: int,
    poll_interval: int,
    poll_timeout: int,
    progress_callback: ProgressCallback | None,
    chunk_index: int,
    chunk_total: int,
) -> dict:
    exist_payload = _build_pod_v2_payload(filters)
    response = session.post(
        POD_REPORT_V2_EXIST_URL,
        data=exist_payload,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": POD_REPORT_V2_URL},
        timeout=(30, timeout),
    )
    response.raise_for_status()
    data = response.json()
    status = data.get("status")
    process_id = str(data.get("data", {}).get("id", ""))
    file_name = str(data.get("data", {}).get("file_name", ""))
    document_name = str(data.get("data", {}).get("document_name", "")).strip() or f"laporan_pod_v2_{process_id}.xlsx"

    if not process_id or not file_name:
        raise RuntimeError(f"Response is_report_exist tidak lengkap: {data}")

    detail = None
    if status == "FILE_EXIST":
        detail = _poll_pod_v2_process(
            session=session,
            process_id=process_id,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            progress_callback=progress_callback,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
        if str(detail.get("rowstate", "")) == "2":
            detail["file_name"] = file_name
            detail["document_name"] = document_name
            return detail

    generate_payload = {
        **exist_payload,
        "userdata": filters["userdata"],
        "process_id": process_id,
        "file_name": file_name,
        "auth_token": filters["auth_token"],
    }
    generate_response = session.post(
        POD_REPORT_V2_GENERATE_URL,
        data=generate_payload,
        headers={"Referer": POD_REPORT_V2_URL},
        timeout=(30, timeout),
    )
    generate_response.raise_for_status()
    _emit(
        progress_callback,
        {
            "stage": "status",
            "chunk_index": chunk_index,
            "chunk_total": chunk_total,
            "message": f"Process {process_id} dimulai untuk {document_name}",
        },
    )

    detail = _poll_pod_v2_process(
        session=session,
        process_id=process_id,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
        progress_callback=progress_callback,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
    )
    detail["file_name"] = file_name
    detail["document_name"] = document_name
    return detail


def _build_pod_v2_download_url(file_name: str, document_name: str) -> str:
    return f"{POD_REPORT_V2_DOWNLOAD_ROOT}/{file_name}/{document_name.replace(' ', '_X_')}"


def run_pod_report_v2_batches(
    session: requests.Session,
    filters: dict,
    start_date: date,
    end_date: date,
    batch_days: int,
    output_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
    retry_delay: int = 5,
    poll_interval: int = 5,
    poll_timeout: int = DEFAULT_TIMEOUT,
    progress_callback: ProgressCallback | None = None,
) -> list[PodV2Result]:
    chunks = split_date_range(start_date, end_date, batch_days)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[PodV2Result] = []

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        from_text = _format_date_for_pod(chunk_start)
        to_text = _format_date_for_pod(chunk_end)
        _emit(
            progress_callback,
            {
                "stage": "overall",
                "chunk_index": index,
                "chunk_total": len(chunks),
                "from_date": from_text,
                "to_date": to_text,
                "label": f"Batch {index}/{len(chunks)} | {from_text} s/d {to_text}",
            },
        )

        chunk_filters = {
            **filters,
            "from": from_text,
            "to": to_text,
        }
        detail = _start_or_reuse_pod_v2_process(
            session=session,
            filters=chunk_filters,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            progress_callback=progress_callback,
            chunk_index=index,
            chunk_total=len(chunks),
        )
        rowstate = str(detail.get("rowstate", ""))
        if rowstate != "2":
            raise RuntimeError(
                f"Process {detail.get('id')} gagal atau belum siap. rowstate={rowstate}, "
                f"status_message={detail.get('status_message')}"
            )

        document_name = str(detail.get("document_name") or f"laporan_pod_v2_{index}.xlsx")
        file_name = str(detail.get("file_name") or "")
        if not file_name:
            raise RuntimeError(f"Process {detail.get('id')} tidak memiliki file_name.")
        download_url = _build_pod_v2_download_url(file_name, document_name)
        output_path = output_dir / f"pod_v2_{chunk_start.isoformat()}_{chunk_end.isoformat()}_{sanitize_name(document_name)}"
        file_size = _download_stream_to_path(
            session=session,
            url=download_url,
            output_path=output_path,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            progress_callback=progress_callback,
            chunk_index=index,
            chunk_total=len(chunks),
            file_label=document_name,
        )
        results.append(
            PodV2Result(
                chunk_index=index,
                chunk_total=len(chunks),
                from_date=from_text,
                to_date=to_text,
                process_id=str(detail.get("id")),
                document_name=document_name,
                saved_path=str(output_path),
                file_size=file_size,
            )
        )
    return results


def run_pod_by_awb_batches(
    session: requests.Session,
    key: str,
    raw_awbs: str,
    output_dir: Path,
    awb_per_file: int = 500,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = 3,
    retry_delay: int = 5,
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress_callback: ProgressCallback | None = None,
) -> list[PodByAwbResult]:
    awbs = normalize_awb_lines(raw_awbs)
    if not awbs:
        raise ValueError("Daftar AWB kosong.")
    if awb_per_file < 1:
        raise ValueError("awb_per_file harus minimal 1.")

    chunks = _chunk_awbs(awbs, awb_per_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_by_index: dict[int, PodByAwbResult] = {}
    max_workers = max(1, min(max_workers, len(chunks) or 1))

    def worker(index: int, awb_chunk: list[str]) -> PodByAwbResult:
        # The report02 endpoint silently returns an empty workbook if textarea lines
        # are sent with LF only. Browser form submission normalizes textarea content
        # to CRLF, so mirror that behavior exactly.
        payload = {
            "key": key or "a.awb_no",
            "val": "\r\n".join(awb_chunk),
        }

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                worker_session = _clone_session(session)
                with worker_session.post(
                    POD_BY_AWB_EXPORT_URL,
                    files={
                        "key": (None, payload["key"]),
                        "val": (None, payload["val"]),
                    },
                    headers={
                        "Origin": BASE_URL,
                        "Referer": f"{BASE_URL}/",
                        "Upgrade-Insecure-Requests": "1",
                    },
                    stream=True,
                    timeout=(30, timeout),
                ) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    if "sheet" not in content_type and "excel" not in content_type:
                        raise RuntimeError(f"Response POD BY AWB bukan file excel: {content_type}")

                    header_file_name = _extract_filename_from_headers(response.headers) or f"laporan_pod_by_awb_{index:04d}.xlsx"
                    output_path = output_dir / f"pod_by_awb_{index:04d}_{sanitize_name(header_file_name)}"
                    total_bytes = int(response.headers.get("Content-Length", "0")) or None
                    downloaded_bytes = 0
                    started_at = time.monotonic()
                    with output_path.open("wb") as file_handle:
                        for block in response.iter_content(chunk_size=1024 * 1024):
                            if not block:
                                continue
                            file_handle.write(block)
                            downloaded_bytes += len(block)
                    return PodByAwbResult(
                        chunk_index=index,
                        chunk_total=len(chunks),
                        awb_count=len(awb_chunk),
                        first_awb=awb_chunk[0],
                        last_awb=awb_chunk[-1],
                        saved_path=str(output_path),
                        file_size=output_path.stat().st_size,
                        data_row_count=_count_xlsx_data_rows(output_path),
                    )
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(retry_delay)
        else:
            if last_error:
                raise last_error
            raise RuntimeError("Export POD BY AWB gagal tanpa exception yang tertangkap.")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(worker, index, awb_chunk): index
            for index, awb_chunk in enumerate(chunks, start=1)
        }
        completed = 0
        for future in as_completed(future_map):
            result = future.result()
            completed += 1
            results_by_index[result.chunk_index] = result
            _emit(
                progress_callback,
                {
                    "stage": "file",
                    "chunk_index": result.chunk_index,
                    "chunk_total": len(chunks),
                    "file_name": Path(result.saved_path).name,
                    "downloaded_bytes": result.file_size,
                    "total_bytes": result.file_size,
                    "speed_text": "parallel",
                },
            )
            _emit(
                progress_callback,
                {
                    "stage": "overall",
                    "chunk_index": completed,
                    "chunk_total": len(chunks),
                    "label": f"Selesai {completed}/{len(chunks)} chunk AWB",
                },
            )
    return [results_by_index[index] for index in sorted(results_by_index)]
