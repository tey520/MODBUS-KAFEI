$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "MODBUS-KAFEI" --paths src --icon "assets\kafei-coffee.ico" --version-file "assets\version_info.txt" --add-data "assets;assets" run.py
Copy-Item -LiteralPath "$projectRoot\dist\MODBUS-KAFEI.exe" -Destination "$projectRoot\dist\MODBUS-KAFEI-v0.1.5.exe" -Force
Write-Host "Built: $projectRoot\dist\MODBUS-KAFEI.exe"
Write-Host "Cache-safe copy: $projectRoot\dist\MODBUS-KAFEI-v0.1.5.exe"
