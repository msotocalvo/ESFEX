# ESFEX Windows installer

A native Windows `.exe` installer built with [conda `constructor`](https://github.com/conda/constructor).

## Why constructor (and not PyInstaller)

ESFEX is a hybrid app: a **PySide6 (Qt)** GUI, the **Julia** optimizer (via
`pyjuliacall`), and a heavy **GDAL/GEOS/PROJ + HDF5 + BLAS** native stack
(geopandas/rasterio). Freezing all of that — especially Julia and GDAL — with a
classic Python freezer is fragile. `constructor` instead lays down a full
conda environment in which conda-forge supplies every native dependency as a
prebuilt binary, then `post_install.bat` pip-installs ESFEX on top.

End users therefore need **neither Python nor Julia pre-installed**, and the
installer creates a **Start Menu "ESFEX Studio" shortcut** that runs
`python -m esfex studio` from the bundled environment — so the
`Scripts\`-not-on-`PATH` problem (see [#17](https://github.com/Net-Zero-Horizon/ESFEX/issues/17))
never arises.

## Files

| File | Purpose |
|------|---------|
| `construct.yaml`  | Installer spec: conda-forge specs (mirrors `environment.yml`) + Julia + menuinst. Version is kept in sync with `pyproject.toml` by CI. |
| `post_install.bat`| pip-installs ESFEX (+ companions), warms up the Julia depot, creates the Start Menu shortcut. Best-effort — failures don't abort the install. |

## Build locally

```bash
conda install -n base -c conda-forge constructor menuinst
constructor installer/ --output-dir dist
# -> dist/ESFEX-<version>-Windows-x86_64.exe
```

(Building the `.exe` must run on Windows; on Linux/macOS `constructor` can still
validate the spec and build the matching native installer for that OS.)

## CI

`.github/workflows/installer-windows.yml` builds the installer on every `v*`
tag (and on manual dispatch), uploads it as a workflow artifact, and attaches
it to the GitHub Release.

## Notes / things to validate on the first real build

- **Size** is large (~1–2 GB): Julia + Qt + GDAL. Expected for this stack.
- **Julia precompile** in `post_install.bat` (`esfex precompile`) is best-effort
  and can be slow; consider shipping a prebuilt sysimage later to trade install
  size for instant startup.
- **`pyjuliacall` ↔ bundled `julia`**: confirm `juliacall` uses the bundled
  conda-forge Julia rather than downloading its own on first run (set
  `PYTHON_JULIAPKG_*` / juliacall offline config if needed).
- **Per-user prefix** (`%LOCALAPPDATA%\ESFEX`) avoids requiring admin rights.
