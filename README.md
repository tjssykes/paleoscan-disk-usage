# PaleoScan Project Disk Usage Viewer

A small Python/Tkinter desktop app that reads a PaleoScan `PaleoScanProductProjectListBis.xml`
project list, scans each project folder on disk, and shows a per-project breakdown of disk
usage by subfolder — plus a summary tab across all projects and CSV export.

## Features

- Parses project paths from `<path value="..."/>` entries in the PaleoScan project list XML
- One tab per project showing:
  - Every immediate subfolder with its total (recursive) size and file count
  - Loose files at the project root grouped into a single "Files" row
  - Sortable columns, % of project size
- A **Summary** tab (shown first) aggregating totals across every scanned project
- **Export to CSV** — writes a summary section and a full per-project detail section
- Background scanning thread, so the UI stays responsive on large projects
- No third-party dependencies — pure Python standard library (`tkinter`, `xml`, `os`, `csv`)

## Requirements

- Python 3.9+ with Tkinter (Tkinter ships with the standard Windows/macOS installers; on
  Linux you may need to install it separately, e.g. `sudo apt install python3-tk`)

## Running it

```bash
python3 paleoscan_disk_usage.py
```

On Windows, you can also just double-click `paleoscan_disk_usage.pyw` — the `.pyw` extension
runs it via `pythonw.exe` with no console window.

By default, the app looks for `~/.paleoscan/PaleoScanProductProjectListBis.xml` on startup.
If it's not found there, use the **Browse...** button to select the file manually.

## Building a standalone Windows .exe

If you don't want to rely on a local Python install, package it with
[PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "PaleoScanDiskUsage" paleoscan_disk_usage.py
```

The resulting `.exe` will be in `dist/`. This repo also includes a GitHub Actions workflow
(`.github/workflows/build-windows.yml`) that builds this automatically and attaches the `.exe`
to a GitHub Release whenever you push a version tag (e.g. `v1.0.0`).

## XML format expected

```xml
<PaleoScanProductProjectListBis>
 <project>
  <path value="C:\Path\To\Project"/>
 </project>
 ...
</PaleoScanProductProjectListBis>
```

## License

MIT — see [LICENSE](LICENSE).

<img width="1596" height="990" alt="image" src="https://github.com/user-attachments/assets/1e0a1452-a97f-4c05-a3d1-d834a8191edf" />
