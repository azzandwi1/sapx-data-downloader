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

Untuk download besar, gunakan mode CLI lokal. Mode ini menyimpan file langsung ke folder lokal per tanggal/batch, jadi tidak bergantung pada session Streamlit Cloud dan tidak perlu klik tombol `Download`.

Alasan:

- Browser tidak mengizinkan aplikasi cloud memaksa file otomatis tersimpan ke komputer user tanpa interaksi download.
- Streamlit Cloud punya disk dan session terbatas.
- Data multi-GB lebih stabil jika worker berjalan di laptop/VPS lokal dan langsung menulis ke disk.

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
set PYTHONPATH=D:\SAPX\Data Analisis\DATA DOWNLOADER\src
python -m sapx_downloader `
  --from-date 01-06-2026 `
  --to-date 07-06-2026 `
  --branch NASIONAL `
  --timeout 2700 `
  --max-retries 3 `
  --max-workers 1 `
  --out-dir "D:\SAPX\Data Analisis\HASIL DOWNLOAD\monitoring_juni"
```

Contoh full bulan:

```powershell
set PYTHONPATH=D:\SAPX\Data Analisis\DATA DOWNLOADER\src
python -m sapx_downloader `
  --year 2026 `
  --month 6 `
  --branch NASIONAL `
  --skip-existing `
  --timeout 2700 `
  --max-retries 3 `
  --max-workers 1
```

Contoh lewat PowerShell helper:

```powershell
.\scripts\run_monitoring_gateway.ps1 `
  -FromDate 01-06-2026 `
  -ToDate 30-06-2026 `
  -Branch NASIONAL `
  -OutDir "D:\SAPX\Data Analisis\HASIL DOWNLOAD\monitoring_juni" `
  -Timeout 2700 `
  -MaxRetries 3 `
  -MaxWorkers 1 `
  -SkipExisting
```

Gunakan `-MaxWorkers 1` untuk Monitoring Proses jika server SAPX sering timeout. Naikkan ke `2-4` hanya jika endpoint sedang stabil.

## Catatan

- UI pickup mendukung range tanggal bebas, tetapi download tetap dieksekusi dalam batch per N hari sesuai input Anda.
- UI `POD BY AWB` menerima input AWB tanpa batas, lalu otomatis memecah download maksimal `500 AWB` per file.
- UI `POD V2` memakai proses background seperti web asli, lalu menunggu file siap sebelum download.
- Output file disimpan ke folder `downloads\...` sesuai menu yang dipilih.
- Progress bar di UI menampilkan progres batch dan progres file yang sedang diunduh.
