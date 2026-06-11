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

rem -- Shortcuts: "ESFEX Studio" -> the console-less esfex-studio.exe that pip
rem    generated from the [project.gui-scripts] entry point. Double-clicking it
rem    opens the Studio with no terminal window. We create one in the Start Menu
rem    and one on the Desktop. The launcher's own icon is generic, so we point
rem    IconLocation at the bundled esfex.ico.
echo Creating the Start Menu and Desktop shortcuts...
set "SMDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\ESFEX"
set "EXE=%PREFIX%\Scripts\esfex-studio.exe"
set "ICON=%PREFIX%\Lib\site-packages\esfex\icons\esfex.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe='%EXE%'; $icon='%ICON%';" ^
  "if(!(Test-Path $exe)){$exe='%PYWEXE%'};" ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "function New-EsfexShortcut($path){" ^
  "  $s=$w.CreateShortcut($path);" ^
  "  if($exe -eq '%PYWEXE%'){$s.TargetPath='%PYWEXE%'; $s.Arguments='-m esfex studio'} else {$s.TargetPath=$exe; $s.Arguments=''};" ^
  "  $s.WorkingDirectory=$env:USERPROFILE;" ^
  "  if(Test-Path $icon){$s.IconLocation=$icon};" ^
  "  $s.Description='ESFEX — GIS-based power system designer';" ^
  "  $s.Save()" ^
  "};" ^
  "$d='%SMDIR%'; if(!(Test-Path $d)){New-Item -ItemType Directory -Path $d -Force | Out-Null};" ^
  "New-EsfexShortcut (Join-Path $d 'ESFEX Studio.lnk');" ^
  "$desk=[Environment]::GetFolderPath('Desktop');" ^
  "if($desk){New-EsfexShortcut (Join-Path $desk 'ESFEX Studio.lnk')}" || echo [warn] Could not create one or more shortcuts.

echo ESFEX post-install finished.
exit /b 0
