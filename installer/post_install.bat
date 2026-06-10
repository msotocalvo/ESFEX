@echo off
rem ===========================================================================
rem  ESFEX Windows installer — post-install step (run by constructor).
rem
rem  constructor exposes the freshly-created environment prefix as %PREFIX%.
rem  conda-forge already supplied every heavy/native dependency (Qt, Julia, the
rem  GDAL/GEOS/PROJ + HDF5 + BLAS stack); here we only:
rem    1. pip-install ESFEX and its PyPI-only companions on top,
rem    2. (best effort) warm up the Julia depot so the first launch is fast,
rem    3. create an "ESFEX Studio" Start Menu shortcut that launches
rem       `python -m esfex studio` — PATH-independent, so the user just clicks.
rem  Steps 2-3 are best-effort: a failure must NOT abort the installation.
rem ===========================================================================

set "PYEXE=%PREFIX%\python.exe"
set "PYWEXE=%PREFIX%\pythonw.exe"

echo Installing ESFEX from PyPI...
"%PYEXE%" -m pip install --no-warn-script-location esfex || echo [warn] pip install esfex returned an error.

rem -- Best effort: instantiate the Julia environment so the first run does not
rem    stall compiling. Tolerate failure (no network / slow machine).
echo Warming up the Julia backend (this may take a few minutes; optional)...
"%PYEXE%" -m esfex precompile || echo [warn] Julia precompile skipped/failed; it will run on first launch instead.

rem -- Start Menu shortcut: "ESFEX Studio" -> pythonw -m esfex studio.
echo Creating the Start Menu shortcut...
set "SMDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\ESFEX"
set "ICON=%PREFIX%\Lib\site-packages\esfex\icons\esfex.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d='%SMDIR%'; if(!(Test-Path $d)){New-Item -ItemType Directory -Path $d -Force | Out-Null};" ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$s=$w.CreateShortcut(Join-Path $d 'ESFEX Studio.lnk');" ^
  "$s.TargetPath='%PYWEXE%';" ^
  "$s.Arguments='-m esfex studio';" ^
  "$s.WorkingDirectory=$env:USERPROFILE;" ^
  "if(Test-Path '%ICON%'){$s.IconLocation='%ICON%'};" ^
  "$s.Description='ESFEX — GIS-based power system designer';" ^
  "$s.Save()" || echo [warn] Could not create the Start Menu shortcut.

echo ESFEX post-install finished.
exit /b 0
