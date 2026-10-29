@echo off
rem 切换齐治桥接器的启动模式（写入 mode.txt，下一次点击资源时生效）。
rem 用法：set-mode.cmd bridge|both|putty|auto
setlocal
if "%~1"=="" (
  echo Usage: %~nx0 bridge ^| both ^| putty ^| auto
  exit /b 1
)
set "MODE=%~1"
if /i not "%MODE%"=="bridge" if /i not "%MODE%"=="both" if /i not "%MODE%"=="putty" if /i not "%MODE%"=="auto" (
  echo Unknown mode: %MODE%
  echo Allowed: bridge, both, putty, auto
  exit /b 1
)
set "APPDIR=%LOCALAPPDATA%\sshbridge\QizhiSSHBridge"
if not exist "%APPDIR%" mkdir "%APPDIR%" >nul 2>&1
> "%APPDIR%\mode.txt" echo %MODE%
echo [OK] launch mode = %MODE%
echo      file: %APPDIR%\mode.txt
