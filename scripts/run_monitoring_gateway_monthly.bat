@echo off
setlocal

if "%~1"=="" (
  echo Usage: run_monitoring_gateway_monthly.bat YEAR MONTH
  exit /b 1
)

if "%~2"=="" (
  echo Usage: run_monitoring_gateway_monthly.bat YEAR MONTH
  exit /b 1
)

set PROJECT_ROOT=%~dp0..
set PYTHONPATH=%PROJECT_ROOT%\src

pushd "%PROJECT_ROOT%"
python -m sapx_downloader --year %1 --month %2 --skip-existing
popd

endlocal
