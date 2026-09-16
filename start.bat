@echo off
rem Cryogram launcher.
cd /d "%~dp0"
rem A private Python downloaded on an earlier start lives in python\ and wins.
if exist python\python.exe (
  set "PY=python\python.exe"
  goto :have_python
)
where py >nul 2>nul
if %errorlevel%==0 ( set "PY=py -3" ) else ( set "PY=python" )
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto :have_python
echo Cryogram needs Python 3.10 or newer, and none was found on this computer.
echo It can download a private copy just for this folder (about 40 MB) - nothing else on your computer changes.
choice /c YN /m "Download it now"
if errorlevel 2 (
  echo Okay - install Python 3.10 or newer from python.org, tick "Add python.exe
  echo to PATH" in the installer, then try again.
  pause
  exit /b 1
)
echo Downloading Python...
if exist python rmdir /s /q python
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-x86_64-pc-windows-msvc-install_only.tar.gz' -OutFile 'python.tar.gz'"
if errorlevel 1 (
  echo The download didn't finish - usually this means no internet connection. Connect and try again.
  pause
  exit /b 1
)
rem The expected checksum is written into this script at release time, so a tampered download is refused.
certutil -hashfile python.tar.gz SHA256 | findstr /i "f05531bff16fa77b53be0776587b97b466070e768e6d5920894de988bdcd547a" >nul
if errorlevel 1 (
  echo The downloaded file doesn't match its expected checksum - not using it. Try again later.
  del python.tar.gz
  pause
  exit /b 1
)
tar -xzf python.tar.gz
del python.tar.gz
set "PY=python\python.exe"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo The downloaded Python doesn't run on this computer. Install Python 3.10 or newer from python.org instead.
  pause
  exit /b 1
)
:have_python
rem Everything else, the app's own environment, the server and the cryogram command, is boot.py's job
%PY% boot.py %*
if errorlevel 1 pause
