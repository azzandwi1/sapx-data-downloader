$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"

Set-Location $projectRoot
python -m streamlit run (Join-Path $projectRoot "streamlit_app.py")
