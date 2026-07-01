@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 백링크 자동 글쓰기

if exist "BacklinkWriter.exe" (
    start "" "BacklinkWriter.exe"
    exit /b 0
)
if exist "dist\BacklinkWriter\BacklinkWriter.exe" (
    start "" "dist\BacklinkWriter\BacklinkWriter.exe"
    exit /b 0
)

echo Python 개발 모드로 실행합니다...
python main.py
if errorlevel 1 pause
