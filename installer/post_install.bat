@echo off
rem ===========================================================================
rem  ESFEX Windows installer - post-install step (run by constructor).
rem
rem  constructor exposes the freshly-created environment prefix as %PREFIX%.
rem  conda-forge already supplied every heavy/native dependency (Qt, Julia, the
rem  GDAL/GEOS/PROJ + HDF5 + BLAS stack); here we only:
rem    1. pip-install ESFEX and its PyPI-only companions on top,
rem    2. create "ESFEX Studio" Start Menu + Desktop shortcuts,
rem    3. (best effort, LAST) warm up the Julia depot so the first launch is
rem       fast. This step can take minutes (it downloads Julia on machines
rem       without it), so it runs AFTER the shortcuts - a slow/hung warm-up
rem       must never prevent the shortcuts from being created.
rem  Steps 2-3 are best-effort: a failure must NOT abort the installation.
rem ===========================================================================

set "PYEXE=%PREFIX%\python.exe"
set "PYWEXE=%PREFIX%\pythonw.exe"

echo Installing ESFEX from PyPI...
"%PYEXE%" -m pip install --no-warn-script-location esfex || echo [warn] pip install esfex returned an error.

rem -- Shortcuts: "ESFEX Studio". Primary target is the console-less
rem    esfex-studio.exe that pip generates from the [project.gui-scripts]
rem    entry point (double-click -> Studio, no terminal window). If it is
rem    not present (older esfex on PyPI), fall back to the console esfex.exe
rem    with the `studio` argument. We create one shortcut in the Start Menu
rem    and one on the Desktop, with the bundled esfex.ico.
echo Creating the Start Menu and Desktop shortcuts...
set "SMDIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\ESFEX"
set "GUIEXE=%PREFIX%\Scripts\esfex-studio.exe"
set "CLIEXE=%PREFIX%\Scripts\esfex.exe"
set "ICON=%PREFIX%\Lib\site-packages\esfex\icons\esfex.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$gui='%GUIEXE%'; $cli='%CLIEXE%'; $icon='%ICON%';" ^
  "if(Test-Path $gui){$target=$gui; $targetArgs=''} else {$target=$cli; $targetArgs='studio'};" ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "function New-EsfexShortcut($path){" ^
  "  $s=$w.CreateShortcut($path);" ^
  "  $s.TargetPath=$target; $s.Arguments=$targetArgs;" ^
  "  $s.WorkingDirectory=$env:USERPROFILE;" ^
  "  if(Test-Path $icon){$s.IconLocation=$icon};" ^
  "  $s.Description='ESFEX - GIS-based power system designer';" ^
  "  $s.Save()" ^
  "};" ^
  "$d='%SMDIR%'; if(!(Test-Path $d)){New-Item -ItemType Directory -Path $d -Force | Out-Null};" ^
  "New-EsfexShortcut (Join-Path $d 'ESFEX Studio.lnk');" ^
  "$desk=[Environment]::GetFolderPath('Desktop');" ^
  "if($desk){New-EsfexShortcut (Join-Path $desk 'ESFEX Studio.lnk')}" || echo [warn] Could not create one or more shortcuts.

rem -- Best effort, LAST: instantiate the Julia environment so the first run
rem    does not stall compiling. Tolerate failure (no network / slow machine);
rem    it will run on first launch instead. Kept last so it can never block
rem    shortcut creation.
echo Warming up the Julia backend (this may take a few minutes; optional)...
"%PYEXE%" -m esfex precompile || echo [warn] Julia precompile skipped/failed; it will run on first launch instead.

echo ESFEX post-install finished.
exit /b 0
