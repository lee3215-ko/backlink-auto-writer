@echo off
setlocal
cd /d "%~dp0"

echo [0/4] Syncing user data (logs -^> AppData)...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\sync-user-data.ps1"
if errorlevel 1 goto fail

echo [1/4] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 goto fail

set "BW_DIST=%LOCALAPPDATA%\BacklinkWriter_build\dist"
set "BW_WORK=%LOCALAPPDATA%\BacklinkWriter_build\work"
if not exist "%BW_DIST%" mkdir "%BW_DIST%"
if not exist "%BW_WORK%" mkdir "%BW_WORK%"

echo [2/4] Building BacklinkWriter...
python -m PyInstaller build.spec --noconfirm --clean --distpath "%BW_DIST%" --workpath "%BW_WORK%"
if errorlevel 1 goto fail

echo [2b/4] Copying build output to dist\BacklinkWriter...
if not exist "dist" mkdir "dist"
if exist "dist\BacklinkWriter" rmdir /s /q "dist\BacklinkWriter"
xcopy /E /I /Y "%BW_DIST%\BacklinkWriter" "dist\BacklinkWriter" >nul
if errorlevel 1 goto fail

echo [3/4] Installing Playwright Chromium into dist...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install-playwright.ps1"
if errorlevel 1 goto fail

echo [3b/4] Bundling log sync token (if present)...
if exist "log_sync_token.txt" copy /Y "log_sync_token.txt" "dist\BacklinkWriter\log_sync_token.txt" >nul
if exist "log_sync_token_for_clients.txt" if not exist "dist\BacklinkWriter\log_sync_token.txt" copy /Y "log_sync_token_for_clients.txt" "dist\BacklinkWriter\log_sync_token.txt" >nul
if exist "blocked_sites.json" copy /Y "blocked_sites.json" "dist\BacklinkWriter\blocked_sites.json" >nul

echo [4/4] Done.
echo Output: dist\BacklinkWriter\BacklinkWriter.exe
goto end

:fail
echo Build failed.
exit /b 1

:end
endlocal
