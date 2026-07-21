@echo off
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "MODBUS-KAFEI" --paths src --icon "assets\kafei-coffee.ico" --version-file "assets\version_info.txt" --add-data "assets;assets" run.py
if errorlevel 1 exit /b %errorlevel%
copy /y "%CD%\dist\MODBUS-KAFEI.exe" "%CD%\dist\MODBUS-KAFEI-v0.1.5.exe" >nul
if errorlevel 1 exit /b %errorlevel%
echo Built: %CD%\dist\MODBUS-KAFEI.exe
echo Cache-safe copy: %CD%\dist\MODBUS-KAFEI-v0.1.5.exe
