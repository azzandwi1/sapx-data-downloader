import sys
from io import BytesIO
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import extra_streamlit_components as stx
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sapx_downloader.auth import build_authenticated_session
from sapx_downloader.monitoring_gateway import get_monitoring_gateway_filters, run_monitoring_gateway_batches
from sapx_downloader.pod_exports import (
    get_pod_by_awb_filters,
    get_pod_report_v2_filters,
    run_pod_by_awb_batches,
    run_pod_report_v2_batches,
    search_pod_v2_customers,
)
from sapx_downloader.pickup_exports import (
    get_pickup_manual_filters,
    get_pickup_monitoring_filters,
    run_pickup_export_batches,
    run_pickup_manual_export_batches,
    search_pickup_customers,
    search_pickup_manual_customers,
)

COOKIE_PREFIX = "sapx_downloader"
DEFAULT_REQUEST_TIMEOUT = 45 * 60
DEFAULT_MAX_WORKERS = 4
REMEMBER_ME_DAYS = 30


st.set_page_config(page_title="SAPX Data Downloader", layout="wide")


def get_cookie_manager() -> stx.CookieManager:
    return stx.CookieManager()


def init_state() -> None:
    st.session_state.setdefault("gateway_filters_meta", None)
    st.session_state.setdefault("pickup_filters_meta", None)
    st.session_state.setdefault("pickup_manual_filters_meta", None)
    st.session_state.setdefault("pod_v2_filters_meta", None)
    st.session_state.setdefault("pod_by_awb_filters_meta", None)
    st.session_state.setdefault("pickup_customer_results", [])
    st.session_state.setdefault("pickup_manual_customer_results", [])
    st.session_state.setdefault("pod_customer_results", [])
    st.session_state.setdefault("remember_me", False)
    st.session_state.setdefault("remember_me_loaded", False)
    st.session_state.setdefault("download_results", {})


def hydrate_remembered_credentials(cookie_manager: stx.CookieManager) -> None:
    if st.session_state.get("remember_me_loaded"):
        return

    cookies = cookie_manager.get_all(key=f"{COOKIE_PREFIX}_get_all") or {}
    remembered_cookie = f"{COOKIE_PREFIX}_remember_me"
    if remembered_cookie not in cookies:
        st.session_state.setdefault("username", "")
        st.session_state.setdefault("password", "")
        st.session_state.setdefault("pin", "")
        return

    remembered_flag = cookies.get(remembered_cookie)
    remembered = remembered_flag == "1"
    st.session_state["remember_me"] = remembered
    if remembered:
        st.session_state["username"] = cookies.get(f"{COOKIE_PREFIX}_username") or ""
        st.session_state["password"] = cookies.get(f"{COOKIE_PREFIX}_password") or ""
        st.session_state.setdefault("pin", "")
    else:
        st.session_state.setdefault("username", "")
        st.session_state.setdefault("password", "")
        st.session_state.setdefault("pin", "")
    st.session_state["remember_me_loaded"] = True


def persist_remembered_credentials(
    cookie_manager: stx.CookieManager,
    username: str,
    password: str,
    remember_me: bool,
) -> None:
    if remember_me:
        expires_at = datetime.now() + timedelta(days=REMEMBER_ME_DAYS)
        cookie_manager.batch_set(
            {
                f"{COOKIE_PREFIX}_remember_me": "1",
                f"{COOKIE_PREFIX}_username": username,
                f"{COOKIE_PREFIX}_password": password,
            },
            expires_at=expires_at,
            same_site="lax",
        )
    else:
        clear_remembered_credentials(cookie_manager)


def clear_remembered_credentials(cookie_manager: stx.CookieManager) -> None:
    cookie_manager.delete(f"{COOKIE_PREFIX}_remember_me", key=f"{COOKIE_PREFIX}_delete_remember")
    cookie_manager.delete(f"{COOKIE_PREFIX}_username", key=f"{COOKIE_PREFIX}_delete_username")
    cookie_manager.delete(f"{COOKIE_PREFIX}_password", key=f"{COOKIE_PREFIX}_delete_password")


def option_map(options: list[dict]) -> dict[str, str]:
    mapping = {item["label"] or item["value"]: item["value"] for item in options}
    if not mapping:
        mapping = {"-": "-"}
    return mapping


def select_value(label: str, options: list[dict], key: str) -> str:
    mapping = option_map(options)
    labels = list(mapping.keys())
    selected_label = st.selectbox(label, labels, key=key)
    return mapping.get(selected_label, "-")


def select_from_mapping(label: str, mapping: dict[str, str], key: str) -> str:
    labels = list(mapping.keys()) or ["-"]
    selected_label = st.selectbox(label, labels, key=key)
    return mapping.get(selected_label, "-")


def filter_branch_options(options: list[dict], area_value: str) -> list[dict]:
    if not area_value or area_value == "-":
        return options
    if not any(item.get("attrs", {}).get("data-area") for item in options):
        return options
    filtered = [item for item in options if item.get("attrs", {}).get("data-area") == area_value or item["value"] == "-"]
    return filtered or options


def connect_and_load_filters(username: str, password: str, pin: str) -> None:
    session = build_authenticated_session(username, password, pin)
    st.session_state["gateway_filters_meta"] = get_monitoring_gateway_filters(session)
    st.session_state["pickup_filters_meta"] = get_pickup_monitoring_filters(session)
    st.session_state["pickup_manual_filters_meta"] = get_pickup_manual_filters(session)
    st.session_state["pod_v2_filters_meta"] = get_pod_report_v2_filters(session)
    st.session_state["pod_by_awb_filters_meta"] = get_pod_by_awb_filters(session)


def search_customers(menu: str, username: str, password: str, pin: str, query: str) -> None:
    session = build_authenticated_session(username, password, pin)
    if menu == "pickup":
        results = search_pickup_customers(session, query)
        st.session_state["pickup_customer_results"] = [result.__dict__ for result in results]
    elif menu == "pod":
        results = search_pod_v2_customers(session, query)
        st.session_state["pod_customer_results"] = [result.__dict__ for result in results]
    else:
        results = search_pickup_manual_customers(session, query)
        st.session_state["pickup_manual_customer_results"] = [result.__dict__ for result in results]


def make_progress_callback(overall_bar, overall_text, file_bar, file_text):
    state = {
        "unknown_file_name": None,
        "unknown_tick": 0,
    }

    def callback(payload: dict) -> None:
        if payload["stage"] == "overall":
            current = payload["chunk_index"]
            total = payload["chunk_total"]
            overall_bar.progress(current / total if total else 0.0)
            label = payload.get("label")
            if not label:
                from_date = payload.get("from_date", "-")
                to_date = payload.get("to_date", "-")
                label = f"Batch {current}/{total}: {from_date} s/d {to_date}"
            overall_text.write(label)
            file_bar.progress(0.0)
            file_text.write("Menyiapkan proses...")
            state["unknown_file_name"] = None
            state["unknown_tick"] = 0
        elif payload["stage"] == "file":
            total_bytes = payload.get("total_bytes") or 0
            downloaded = payload.get("downloaded_bytes", 0)
            file_name = payload.get("file_name") or ""
            if total_bytes:
                progress_value = max(0.0, min(1.0, downloaded / total_bytes))
                state["unknown_file_name"] = None
                state["unknown_tick"] = 0
            else:
                if state["unknown_file_name"] != file_name:
                    state["unknown_file_name"] = file_name
                    state["unknown_tick"] = 0
                state["unknown_tick"] += 1
                progress_value = min(0.95, 0.08 + (state["unknown_tick"] % 18) * 0.045)
            file_bar.progress(progress_value)
            byte_text = f"{downloaded:,}"
            if total_bytes:
                byte_text += f" / {total_bytes:,}"
            else:
                byte_text += " / ukuran file belum diketahui"
            file_label = payload.get("file_name") or f"File {payload['chunk_index']}/{payload['chunk_total']}"
            if payload.get("file_index") and payload.get("file_total"):
                file_label = f"{payload['file_index']}/{payload['file_total']} - {file_label}"
            file_text.write(
                f"{file_label} | {byte_text} bytes | {payload['speed_text']}"
            )
        elif payload["stage"] == "status":
            elapsed = payload.get("elapsed_seconds")
            timeout_seconds = payload.get("timeout_seconds")
            if timeout_seconds:
                progress_value = max(0.0, min(0.98, float(elapsed or 0) / float(timeout_seconds)))
                file_bar.progress(progress_value)
                file_text.write(
                    f"{payload['message']} | menunggu server {int(elapsed or 0)} / {int(timeout_seconds)} detik"
                )
            else:
                file_text.write(payload["message"])
        elif payload["stage"] == "retry":
            file_text.write(
                f"Retry {payload['attempt']}/{payload['max_retries']} | {payload['message']}"
            )
    return callback


def build_zip_bytes(files: list[Path]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for file_path in files:
            zip_file.writestr(file_path.name, file_path.read_bytes())
    return buffer.getvalue()


def normalize_download_paths(items: list[object], key: str) -> list[Path]:
    paths: list[Path] = []
    for item in items:
        raw_value = getattr(item, key, "")
        if raw_value:
            path = Path(raw_value)
            if path.exists() and path.is_file():
                paths.append(path)
    return paths


def collect_monitoring_gateway_paths(items: list[object]) -> list[Path]:
    paths: list[Path] = []
    for item in items:
        output_dir = Path(getattr(item, "output_dir", ""))
        if output_dir.exists() and output_dir.is_dir():
            paths.extend(sorted(output_dir.glob("*.xlsx")))
    return paths


def render_download_browser(result_key: str, title: str) -> None:
    files: list[str] = st.session_state["download_results"].get(result_key, [])
    if not files:
        return

    paths = [Path(file_path) for file_path in files if Path(file_path).exists()]
    if not paths:
        return

    st.markdown(f"**{title}**")
    st.caption("File di bawah bisa langsung diunduh ke browser Anda. Jika ingin semua sekaligus, gunakan ZIP.")
    zip_bytes = build_zip_bytes(paths)
    st.download_button(
        "Download Semua sebagai ZIP",
        data=zip_bytes,
        file_name=f"{result_key}.zip",
        mime="application/zip",
        key=f"{result_key}_zip_download",
        use_container_width=True,
    )

    for index, path in enumerate(paths, start=1):
        col1, col2, col3 = st.columns([5, 2, 2])
        with col1:
            st.write(f"{index}. `{path.name}`")
        with col2:
            st.write(f"{path.stat().st_size:,} bytes")
        with col3:
            st.download_button(
                "Download",
                data=path.read_bytes(),
                file_name=path.name,
                mime="application/octet-stream",
                key=f"{result_key}_file_{index}",
                use_container_width=True,
            )


def internal_output_dir(name: str) -> Path:
    path = PROJECT_ROOT / "downloads" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_sidebar() -> tuple[str, str, str]:
    cookie_manager = get_cookie_manager()
    hydrate_remembered_credentials(cookie_manager)
    with st.sidebar:
        st.header("Login")
        username = st.text_input("Username", key="username")
        password = st.text_input("Password", type="password", key="password")
        pin = st.text_input("PIN", type="password", key="pin", max_chars=6)
        remember_me = st.checkbox("Ingat saya di browser ini", key="remember_me")
        if st.button("Load Filters", use_container_width=True):
            try:
                connect_and_load_filters(username, password, pin)
                persist_remembered_credentials(cookie_manager, username, password, remember_me)
                st.success("Filter berhasil dimuat.")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if st.button("Lupakan login tersimpan", use_container_width=True):
            clear_remembered_credentials(cookie_manager)
            st.session_state["username"] = ""
            st.session_state["password"] = ""
            st.session_state["pin"] = ""
            st.session_state["remember_me"] = False
            st.success("Login tersimpan di browser ini sudah dihapus.")
            st.rerun()
        st.caption("Kredensial default dikosongkan. Jika `Ingat saya` dicentang, hanya username dan password yang disimpan di browser user ini. PIN tetap perlu diisi manual.")
    return username, password, pin


def render_pickup_tab(username: str, password: str, pin: str) -> None:
    st.subheader("Pickup > Monitoring Pickup")
    meta = st.session_state.get("pickup_filters_meta")
    if not meta:
        st.info("Klik `Load Filters` di sidebar untuk memuat pilihan filter Pickup dari website.")
        return

    search_col, select_col = st.columns([1, 2])
    with search_col:
        pickup_customer_query = st.text_input("Cari Customer", key="pickup_customer_query")
        if st.button("Search Customer Pickup"):
            search_customers("pickup", username, password, pin, pickup_customer_query)
    with select_col:
        customer_results = st.session_state["pickup_customer_results"]
        customer_labels = ["-"] + [item["label"] for item in customer_results]
        pickup_customer_label = st.selectbox("Customer", customer_labels, key="pickup_customer_label")
        pickup_customer_value = next(
            (item["value"] for item in customer_results if item["label"] == pickup_customer_label),
            "-",
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        origin_region_value = select_value("Origin Region", meta["origin_regions"], "pickup_origin_region")
        origin_branch_options = filter_branch_options(meta["origin_branches"], origin_region_value)
        origin_branch_value = select_value("Cabang Asal", origin_branch_options, "pickup_origin_branch")
        status_value = select_value("Status Pickup", meta["status_pickup"], "pickup_status")
    with col2:
        destination_region_value = select_value("Destination Region", meta["destination_regions"], "pickup_destination_region")
        destination_branch_options = filter_branch_options(meta["destination_branches"], destination_region_value)
        destination_branch_value = select_value("Cabang Tujuan", destination_branch_options, "pickup_destination_branch")
        cod_value = select_value("Status COD", meta["status_cod"], "pickup_cod")
    with col3:
        date_basis_value = select_value("Berdasarkan Tanggal", meta["date_basis"], "pickup_date_basis")
        pickup_method_value = select_value("Metode Pickup", meta["pickup_method"], "pickup_method")
        grouping_value = select_value("Status Grouping", meta["grouping_status"], "pickup_grouping")

    date_col1, date_col2, date_col3, date_col4 = st.columns(4)
    with date_col1:
        start_date = st.date_input("From", value=date.today(), key="pickup_from")
    with date_col2:
        end_date = st.date_input("To", value=date.today(), key="pickup_to")
    with date_col3:
        batch_days = st.number_input("Batch per berapa hari", min_value=1, value=5, step=1, key="pickup_batch_days")
    with date_col4:
        export_kind = select_from_mapping(
            "Jenis Export",
            {
                "Export Report Pickup": "report_pickup",
                "Export Monitoring": "monitoring",
            },
            "pickup_export_kind",
        )

    output_dir = internal_output_dir("pickup_monitoring")

    metric_col1, metric_col2 = st.columns(2)
    with metric_col1:
        koli = st.text_input("Koli", value="-", key="pickup_koli")
    with metric_col2:
        kilo = st.text_input("Kilo", value="-", key="pickup_kilo")

    runtime_col1, runtime_col2, runtime_col3 = st.columns(3)
    with runtime_col1:
        timeout_seconds = st.number_input("Read timeout per request (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="pickup_timeout")
    with runtime_col2:
        retry_count = st.number_input("Retry saat timeout", min_value=1, value=3, step=1, key="pickup_retries")
    with runtime_col3:
        max_workers = st.number_input("Parallel workers", min_value=1, max_value=12, value=DEFAULT_MAX_WORKERS, step=1, key="pickup_max_workers")

    if st.button("Execute Pickup Export", type="primary"):
        if date_basis_value in ("0", "-"):
            st.error("Pilih dulu filter berdasarkan tanggal pickup.")
            return

        filters = {
            "customer": pickup_customer_value or "-",
            "origin_region": origin_region_value or "-",
            "origin_branch": origin_branch_value or "-",
            "destination_region": destination_region_value or "-",
            "destination_branch": destination_branch_value or "-",
            "pickup_status": status_value or "-",
            "cod_status": cod_value or "-",
            "pickup_method": pickup_method_value or "-",
            "grouping_status": grouping_value or "-",
            "date_basis": date_basis_value or "-",
            "koli": koli.strip() or "-",
            "kilo": kilo.strip() or "-",
        }

        overall_text = st.empty()
        overall_bar = st.progress(0.0)
        file_text = st.empty()
        file_bar = st.progress(0.0)
        progress_callback = make_progress_callback(overall_bar, overall_text, file_bar, file_text)

        try:
            session = build_authenticated_session(username, password, pin)
            results = run_pickup_export_batches(
                session=session,
                export_kind=export_kind,
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                batch_days=int(batch_days),
                output_dir=output_dir,
                timeout=int(timeout_seconds),
                max_retries=int(retry_count),
                max_workers=int(max_workers),
                progress_callback=progress_callback,
            )
            st.success(f"Selesai. {len(results)} file batch berhasil diunduh.")
            st.session_state["download_results"]["pickup_monitoring"] = [
                str(path) for path in normalize_download_paths(results, "saved_path")
            ]
            st.dataframe([result.__dict__ for result in results], width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    render_download_browser("pickup_monitoring", "Hasil Pickup Monitoring")


def render_pickup_manual_tab(username: str, password: str, pin: str) -> None:
    st.subheader("Pickup Manual > Monitoring Pickup")
    meta = st.session_state.get("pickup_manual_filters_meta")
    if not meta:
        st.info("Klik `Load Filters` di sidebar untuk memuat pilihan filter Pickup Manual dari website.")
        return

    search_col, select_col = st.columns([1, 2])
    with search_col:
        query = st.text_input("Cari Customer", key="pickup_manual_customer_query")
        if st.button("Search Customer Manual"):
            search_customers("pickup_manual", username, password, pin, query)
    with select_col:
        customer_results = st.session_state["pickup_manual_customer_results"]
        customer_labels = ["-"] + [item["label"] for item in customer_results]
        customer_label = st.selectbox("Customer", customer_labels, key="pickup_manual_customer_label")
        customer_value = next(
            (item["value"] for item in customer_results if item["label"] == customer_label),
            "-",
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        origin_region_value = select_value("Origin Region", meta["origin_regions"], "pickup_manual_origin_region")
        origin_branch_options = filter_branch_options(meta["origin_branches"], origin_region_value)
        origin_branch_value = select_value("Cabang Asal", origin_branch_options, "pickup_manual_origin_branch")
    with col2:
        status_value = select_value("Status Pickup", meta["status_pickup"], "pickup_manual_status")
        counter_value = select_value("Tipe Konter", meta["counter_type"], "pickup_manual_counter")
    with col3:
        date_basis_value = select_value("Berdasarkan Tanggal", meta["date_basis"], "pickup_manual_date_basis")
        export_kind = select_from_mapping(
            "Jenis Export",
            {
                "Export Pickup Manual Outstanding": "outstanding",
                "Export Pickup Manual Produktifitas": "productivity",
            },
            "pickup_manual_export_kind",
        )

    output_dir = internal_output_dir("pickup_manual_monitoring")

    date_col1, date_col2, date_col3 = st.columns(3)
    with date_col1:
        start_date = st.date_input("From", value=date.today(), key="pickup_manual_from")
    with date_col2:
        end_date = st.date_input("To", value=date.today(), key="pickup_manual_to")
    with date_col3:
        batch_days = st.number_input("Batch per berapa hari", min_value=1, value=5, step=1, key="pickup_manual_batch_days")

    metric_col1, metric_col2 = st.columns(2)
    with metric_col1:
        koli = st.text_input("Koli", value="-", key="pickup_manual_koli")
    with metric_col2:
        kilo = st.text_input("Kilo", value="-", key="pickup_manual_kilo")

    runtime_col1, runtime_col2, runtime_col3 = st.columns(3)
    with runtime_col1:
        timeout_seconds = st.number_input("Read timeout per request (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="pickup_manual_timeout")
    with runtime_col2:
        retry_count = st.number_input("Retry saat timeout", min_value=1, value=3, step=1, key="pickup_manual_retries")
    with runtime_col3:
        max_workers = st.number_input("Parallel workers", min_value=1, max_value=12, value=DEFAULT_MAX_WORKERS, step=1, key="pickup_manual_max_workers")

    if st.button("Execute Pickup Manual Export", type="primary"):
        if date_basis_value in ("0", "-"):
            st.error("Pilih dulu filter berdasarkan tanggal pickup.")
            return

        filters = {
            "customer": customer_value or "-",
            "origin_region": origin_region_value or "-",
            "origin_branch": origin_branch_value or "-",
            "destination_region": "-",
            "destination_branch": "-",
            "pickup_status": status_value or "-",
            "counter_type": counter_value or "-",
            "date_basis": date_basis_value or "-",
            "koli": koli.strip() or "-",
            "kilo": kilo.strip() or "-",
        }

        overall_text = st.empty()
        overall_bar = st.progress(0.0)
        file_text = st.empty()
        file_bar = st.progress(0.0)
        progress_callback = make_progress_callback(overall_bar, overall_text, file_bar, file_text)

        try:
            session = build_authenticated_session(username, password, pin)
            results = run_pickup_manual_export_batches(
                session=session,
                export_kind=export_kind,
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                batch_days=int(batch_days),
                output_dir=output_dir,
                timeout=int(timeout_seconds),
                max_retries=int(retry_count),
                max_workers=int(max_workers),
                progress_callback=progress_callback,
            )
            st.success(f"Selesai. {len(results)} file batch berhasil diunduh.")
            st.session_state["download_results"]["pickup_manual_monitoring"] = [
                str(path) for path in normalize_download_paths(results, "saved_path")
            ]
            st.dataframe([result.__dict__ for result in results], width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    render_download_browser("pickup_manual_monitoring", "Hasil Pickup Manual Monitoring")


def render_monitoring_gateway_tab(username: str, password: str, pin: str) -> None:
    st.subheader("Monitoring Proses Operational > Laporan Monitoring Proses")
    st.caption("Filter tab ini mengikuti form web aslinya, lalu range besar akan dipecah otomatis per batch N hari.")
    meta = st.session_state.get("gateway_filters_meta")
    if not meta:
        st.info("Klik `Load Filters` di sidebar untuk memuat pilihan Check Point dan Cabang dari website.")
        return
    output_dir = internal_output_dir("monitoring_gateway")

    col1, col2, col3 = st.columns(3)
    with col1:
        checkpoint = select_value("Check Point", meta["checkpoints"], "gateway_checkpoint")
        branch = select_value("Cabang", meta["branches"], "gateway_branch")
    with col2:
        from_time = st.text_input("Dari Jam", value="00:00:00", key="gateway_from_time")
        to_time = st.text_input("Sampai Jam", value="23:59:59", key="gateway_to_time")
    with col3:
        batch_days = st.number_input("Batch per berapa hari", min_value=1, value=5, step=1, key="gateway_batch_days")
    date_col1, date_col2, date_col3 = st.columns(3)
    with date_col1:
        start_date = st.date_input("From", value=date.today(), key="gateway_from")
    with date_col2:
        end_date = st.date_input("To", value=date.today(), key="gateway_to")
    with date_col3:
        skip_existing = st.checkbox("Skip file yang sudah ada", value=True, key="gateway_skip_existing")

    runtime_col1, runtime_col2, runtime_col3 = st.columns(3)
    with runtime_col1:
        timeout_seconds = st.number_input("Read timeout per request (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="gateway_timeout")
    with runtime_col2:
        retry_count = st.number_input("Retry saat timeout", min_value=1, value=3, step=1, key="gateway_retries")
    with runtime_col3:
        max_workers = st.number_input("Parallel workers", min_value=1, max_value=12, value=DEFAULT_MAX_WORKERS, step=1, key="gateway_max_workers")

    if st.button("Execute Monitoring Proses Export", type="primary"):
        overall_text = st.empty()
        overall_bar = st.progress(0.0)
        file_text = st.empty()
        file_bar = st.progress(0.0)
        progress_callback = make_progress_callback(overall_bar, overall_text, file_bar, file_text)

        try:
            session = build_authenticated_session(username, password, pin)
            results = run_monitoring_gateway_batches(
                session=session,
                checkpoint=checkpoint,
                branch=branch.strip() or "NASIONAL",
                from_time=from_time.strip() or "00:00:00",
                to_time=to_time.strip() or "23:59:59",
                start_date=start_date,
                end_date=end_date,
                batch_days=int(batch_days),
                output_root=output_dir,
                timeout=int(timeout_seconds),
                skip_existing=skip_existing,
                max_retries=int(retry_count),
                max_workers=int(max_workers),
                progress_callback=progress_callback,
            )
            st.success(f"Selesai. {len(results)} batch berhasil diproses.")
            st.session_state["download_results"]["monitoring_gateway"] = [
                str(path) for path in collect_monitoring_gateway_paths(results)
            ]
            st.dataframe([result.__dict__ for result in results], width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    render_download_browser("monitoring_gateway", "Hasil Monitoring Proses")


def render_pod_v2_tab(username: str, password: str, pin: str) -> None:
    st.subheader("Laporan POD > Export Laporan POD V2")
    st.caption("Form mengikuti halaman asli. Range besar akan dipecah otomatis per batch hari, lalu setiap batch diproses sampai file siap diunduh.")
    meta = st.session_state.get("pod_v2_filters_meta")
    if not meta:
        st.info("Klik `Load Filters` di sidebar untuk memuat pilihan filter Laporan POD V2 dari website.")
        return
    output_dir = internal_output_dir("pod_v2")

    search_col, select_col = st.columns([1, 2])
    with search_col:
        customer_query = st.text_input("Cari Customer POD", key="pod_customer_query")
        if st.button("Search Customer POD"):
            search_customers("pod", username, password, pin, customer_query)
    with select_col:
        customer_results = st.session_state["pod_customer_results"]
        customer_labels = ["-"] + [item["label"] for item in customer_results]
        customer_label = st.selectbox("Customer", customer_labels, key="pod_customer_label")
        customer_value = next(
            (item["value"] for item in customer_results if item["label"] == customer_label),
            "",
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        export_date_type = select_value("Berdasarkan Tanggal", meta["export_date_type"], "pod_v2_export_date_type")
        origin_area = select_value("Area Asal", meta["origin_area_branch_code"], "pod_v2_origin_area")
        origin_branch_options = filter_branch_options(meta["origin_branch_code"], origin_area)
        origin_branch = select_value("Cabang Asal", origin_branch_options, "pod_v2_origin_branch")
        report_type = select_value("Tipe Laporan", meta["report_type_code"], "pod_v2_report_type")
        flag_return = select_value("Flag Return", meta["flag_return"], "pod_v2_flag_return")
    with col2:
        destination_area = select_value("Area Tujuan", meta["destination_area_branch_code"], "pod_v2_destination_area")
        destination_branch_options = filter_branch_options(meta["destination_branch_code"], destination_area)
        destination_branch = select_value("Cabang Tujuan", destination_branch_options, "pod_v2_destination_branch")
        pod_status = select_value("Status POD", meta["pod_status_code"], "pod_v2_pod_status")
        transaction_type = select_value("Tipe Transaksi", meta["transaction_type_code"], "pod_v2_transaction_type")
        flag_rowstate = select_value("Rowstate", meta["flag_rowstate"], "pod_v2_flag_rowstate")
    with col3:
        service_type = select_value("Service", meta["service_type_code"], "pod_v2_service_type")
        courier_code = select_value("Kurir", meta["courier_code"], "pod_v2_courier_code")
        transportation_code = select_value("Transportasi", meta["transportation_code"], "pod_v2_transportation_code")
        shipment_type = select_value("Jenis Barang", meta["shipment_type_code"], "pod_v2_shipment_type")
        awb_type = select_value("Tipe AWB", meta["awb_type"], "pod_v2_awb_type")

    col4, col5, col6 = st.columns(3)
    with col4:
        start_date = st.date_input("From", value=date.today(), key="pod_v2_from")
        awb_master_id = st.text_input("No. AWB", value="", key="pod_v2_awb_master_id")
        tgl_terima = st.text_input("Tanggal POD", value="", key="pod_v2_tgl_terima")
    with col5:
        end_date = st.date_input("To", value=date.today(), key="pod_v2_to")
        customer_div = select_value("Divisi Customer", meta["customer_div"], "pod_v2_customer_div")
        insurance = select_value("Asuransi", meta["opt_insurance"], "pod_v2_insurance")
    with col6:
        batch_days = st.number_input("Batch per berapa hari", min_value=1, value=5, step=1, key="pod_v2_batch_days")

    runtime_col1, runtime_col2, runtime_col3, runtime_col4 = st.columns(4)
    with runtime_col1:
        timeout_seconds = st.number_input("Read timeout per request (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="pod_v2_timeout")
    with runtime_col2:
        retry_count = st.number_input("Retry saat timeout", min_value=1, value=3, step=1, key="pod_v2_retries")
    with runtime_col3:
        poll_timeout = st.number_input("Maksimum tunggu proses (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="pod_v2_poll_timeout")
    with runtime_col4:
        poll_interval = st.number_input("Interval cek status (detik)", min_value=1, max_value=30, value=2, step=1, key="pod_v2_poll_interval")

    if st.button("Execute Export POD V2", type="primary"):
        overall_text = st.empty()
        overall_bar = st.progress(0.0)
        file_text = st.empty()
        file_bar = st.progress(0.0)
        progress_callback = make_progress_callback(overall_bar, overall_text, file_bar, file_text)

        filters = {
            "export_date_type": export_date_type,
            "origin_area_branch_code": origin_area,
            "origin_branch_code": origin_branch,
            "destination_area_branch_code": destination_area,
            "destination_branch_code": destination_branch,
            "customer_code": customer_value,
            "tgl_terima": tgl_terima.strip(),
            "report_type_code": report_type,
            "pod_status_code": pod_status,
            "transaction_type_code": transaction_type,
            "service_type_code": service_type,
            "courier_code": courier_code,
            "transportation_code": transportation_code,
            "opt_insurance": insurance,
            "customer_div": customer_div,
            "flag_return": flag_return,
            "flag_rowstate": "1" if report_type == "4" else flag_rowstate,
            "shipment_type_code": shipment_type,
            "awb_type": awb_type,
            "awb_master_id": awb_master_id.strip(),
            "auth_token": meta["auth_token"],
            "is_encrypted": meta["is_encrypted"],
            "userdata": meta["userdata"],
        }

        try:
            session = build_authenticated_session(username, password, pin)
            results = run_pod_report_v2_batches(
                session=session,
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                batch_days=int(batch_days),
                output_dir=output_dir,
                timeout=int(timeout_seconds),
                max_retries=int(retry_count),
                poll_interval=int(poll_interval),
                poll_timeout=int(poll_timeout),
                progress_callback=progress_callback,
            )
            st.success(f"Selesai. {len(results)} file batch berhasil diunduh.")
            st.session_state["download_results"]["pod_v2"] = [
                str(path) for path in normalize_download_paths(results, "saved_path")
            ]
            st.dataframe([result.__dict__ for result in results], width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    render_download_browser("pod_v2", "Hasil Laporan POD V2")


def render_pod_by_awb_tab(username: str, password: str, pin: str) -> None:
    st.subheader("Laporan POD > Laporan POD BY AWB")
    st.caption("Input AWB tidak dibatasi di UI. Saat export, sistem akan otomatis memecah maksimal 500 AWB per file.")
    meta = st.session_state.get("pod_by_awb_filters_meta")
    if not meta:
        st.info("Klik `Load Filters` di sidebar untuk memuat pilihan filter POD BY AWB dari website.")
        return
    output_dir = internal_output_dir("pod_by_awb")

    key_value = select_value("Dasar Pencarian", meta["key"], "pod_by_awb_key")
    awb_text = st.text_area(
        "Paste Nomor AWB",
        value="",
        height=260,
        placeholder="Satu AWB per baris",
        key="pod_by_awb_text",
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        awb_per_file = st.number_input("Maksimum AWB per file", min_value=1, max_value=500, value=500, step=1, key="pod_by_awb_per_file")
    with col2:
        timeout_seconds = st.number_input("Read timeout per request (detik)", min_value=60, value=DEFAULT_REQUEST_TIMEOUT, step=30, key="pod_by_awb_timeout")
    with col3:
        retry_count = st.number_input("Retry saat timeout", min_value=1, value=3, step=1, key="pod_by_awb_retries")
    with col4:
        max_workers = st.number_input("Parallel workers", min_value=1, max_value=12, value=DEFAULT_MAX_WORKERS, step=1, key="pod_by_awb_max_workers")

    if st.button("Execute POD BY AWB Export", type="primary"):
        overall_text = st.empty()
        overall_bar = st.progress(0.0)
        file_text = st.empty()
        file_bar = st.progress(0.0)
        progress_callback = make_progress_callback(overall_bar, overall_text, file_bar, file_text)

        try:
            session = build_authenticated_session(username, password, pin)
            results = run_pod_by_awb_batches(
                session=session,
                key=key_value,
                raw_awbs=awb_text,
                output_dir=output_dir,
                awb_per_file=int(awb_per_file),
                timeout=int(timeout_seconds),
                max_retries=int(retry_count),
                max_workers=int(max_workers),
                progress_callback=progress_callback,
            )
            st.success(f"Selesai. {len(results)} file berhasil diunduh.")
            st.session_state["download_results"]["pod_by_awb"] = [
                str(path) for path in normalize_download_paths(results, "saved_path")
            ]
            if results and all(getattr(item, "data_row_count", 0) == 0 for item in results):
                st.warning(
                    "File berhasil dibuat, tetapi SAPX mengembalikan 0 baris data. "
                    "Biasanya ini berarti AWB tidak ditemukan oleh endpoint `POD BY AWB`."
                )
            st.dataframe([result.__dict__ for result in results], width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    render_download_browser("pod_by_awb", "Hasil POD BY AWB")


def main() -> None:
    init_state()
    st.title("SAPX Data Downloader")
    st.caption("Batch export downloader untuk Monitoring Proses, Pickup Monitoring, Pickup Manual, dan Laporan POD.")
    username, password, pin = render_sidebar()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Monitoring Proses", "Pickup Monitoring", "Pickup Manual Monitoring", "Laporan POD V2", "POD BY AWB"]
    )
    with tab1:
        render_monitoring_gateway_tab(username, password, pin)
    with tab2:
        render_pickup_tab(username, password, pin)
    with tab3:
        render_pickup_manual_tab(username, password, pin)
    with tab4:
        render_pod_v2_tab(username, password, pin)
    with tab5:
        render_pod_by_awb_tab(username, password, pin)


if __name__ == "__main__":
    main()
