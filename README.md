# SAPX Data Downloader

Project ini dipakai untuk mengotomasi export data SAPX yang di website aslinya harus dipecah per range kecil lalu diunduh berulang.

Saat ini fitur yang sudah ada:

- `Monitoring Proses Operational > Laporan Monitoring Proses > Manifest Incoming`
- `Pickup > Monitoring Pickup`
- `Pickup Manual > Monitoring Pickup`
- `Laporan POD > Export Laporan POD V2`
- `Laporan POD > Laporan POD BY AWB`
- UI `Streamlit` untuk memilih filter, tanggal bebas, dan batch per N hari

## Struktur

```text
DATA DOWNLOADER/
|-- config/
|   `-- monitoring_gateway.env.example
|-- downloads/
|-- logs/
|-- scripts/
|   |-- run_monitoring_gateway.ps1
|   |-- run_monitoring_gateway_monthly.bat
|   `-- run_streamlit.ps1
|-- src/
|   `-- sapx_downloader/
|       |-- __init__.py
|       |-- __main__.py
|       |-- auth.py
|       |-- html_utils.py
|       |-- monitoring_gateway.py
|       |-- pickup_exports.py
|       `-- pod_exports.py
|-- work/
|-- streamlit_app.py
|-- requirements.txt
`-- README.md
```

## Dependency

```powershell
python -m pip install -r requirements.txt
```

## Menjalankan UI

```powershell
cd "D:\SAPX\Data Analisis\DATA DOWNLOADER"
.\scripts\run_streamlit.ps1
```

Di UI:

1. klik `Load Filters`
2. isi `Username`, `Password`, dan `PIN`
3. centang `Ingat saya di browser ini` jika ingin login tersimpan di browser user tersebut
4. pilih menu yang dibutuhkan
5. isi filter yang dibutuhkan
6. pilih `From`, `To`, dan `Batch per berapa hari`
7. pilih `Jenis Export`
8. klik `Execute`

Catatan login UI:

- Field login default kosong saat app dibuka.
- Fitur `Ingat saya` menyimpan kredensial di browser user masing-masing, bukan di server Streamlit.
- Tombol `Lupakan login tersimpan` akan menghapus data login yang tersimpan di browser tersebut.

## Menjalankan CLI Monitoring Gateway

Untuk mode CLI, kredensial tetap bisa diberikan lewat environment variable atau argumen command line.

Environment variable yang didukung:

- `CORESYS_USERNAME`
- `CORESYS_PASSWORD`
- `CORESYS_PIN`

Contoh isi:

```env
CORESYS_USERNAME=USERNAME_ANDA
CORESYS_PASSWORD=PASSWORD_ANDA
CORESYS_PIN=123456
```

Contoh range manual:

```powershell
python -m sapx_downloader `
  --from-date 01-06-2026 `
  --to-date 07-06-2026 `
  --branch NASIONAL
```

Contoh full bulan:

```powershell
python -m sapx_downloader `
  --year 2026 `
  --month 6 `
  --branch NASIONAL `
  --skip-existing
```

## Catatan

- UI pickup mendukung range tanggal bebas, tetapi download tetap dieksekusi dalam batch per N hari sesuai input Anda.
- UI `POD BY AWB` menerima input AWB tanpa batas, lalu otomatis memecah download maksimal `500 AWB` per file.
- UI `POD V2` memakai proses background seperti web asli, lalu menunggu file siap sebelum download.
- Output file disimpan ke folder `downloads\...` sesuai menu yang dipilih.
- Progress bar di UI menampilkan progres batch dan progres file yang sedang diunduh.
