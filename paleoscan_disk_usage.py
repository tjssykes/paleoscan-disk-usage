#!/usr/bin/env python3
r"""
PaleoScan Project Disk Usage Viewer
------------------------------------
Parses a PaleoScanProductProjectListBis.xml file to find project paths
(the "value" attribute of every <path> element, e.g.
<path value="C:\...\SomeProject"/>), then scans each project folder on
disk and shows a per-project tab breaking down disk usage by immediate
subfolder. Loose files sitting directly in the project root are
grouped together into a single "Files" entry.

Requires only the Python standard library (tkinter, xml, os).

Usage:
    python3 paleoscan_disk_usage.py

Default XML location searched on startup:
    ~/.paleoscan/PaleoScanProductProjectListBis.xml
You can also pick a file manually via the "Browse..." button.

Dr Tim J S Sykes
Calibrated GeoScience UK Ltd
e: calibratedgs@outlook.com
w: www.calibratedgeoscience.co.uk
"""

from __future__ import annotations

import os
import re
import csv
import sys
import queue
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


DEFAULT_XML_PATH = Path.home() / ".paleoscan" / "PaleoScanProductProjectListBis.xml"


# ----------------------------------------------------------------------
# XML parsing
# ----------------------------------------------------------------------

def extract_project_paths(xml_path: Path) -> list[str]:
    """
    Return a de-duplicated, order-preserved list of every project path
    found in the given XML file. The PaleoScan format looks like:

        <project>
          <path value="C:\\...\\SomeProject"/>
        </project>

    i.e. a <path> element with a "value" attribute. We match that
    (case-insensitively, tag/attribute) via a real XML parse first,
    falling back to a regex scan if the file isn't well-formed XML.
    """
    paths: list[str] = []
    seen = set()

    def add(value: str):
        value = (value or "").strip()
        if value and value not in seen:
            seen.add(value)
            paths.append(value)

    text = xml_path.read_text(encoding="utf-8", errors="replace")

    try:
        root = ET.fromstring(text)
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]  # strip any XML namespace
            if tag.lower() == "path":
                for attr_name, attr_val in elem.attrib.items():
                    if attr_name.lower() == "value":
                        add(attr_val)
    except ET.ParseError:
        pass

    # Fallback / supplement: regex scan in case the XML was malformed
    # or ElementTree didn't pick anything up for some reason.
    if not paths:
        for m in re.finditer(r'<path\b[^>]*\bvalue\s*=\s*"([^"]*)"', text, re.IGNORECASE):
            add(m.group(1))
        for m in re.finditer(r"<path\b[^>]*\bvalue\s*=\s*'([^']*)'", text, re.IGNORECASE):
            add(m.group(1))

    return paths


# ----------------------------------------------------------------------
# Disk scanning
# ----------------------------------------------------------------------

def human_size(num_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} EB"


def dir_size(path: Path) -> tuple[int, int]:
    """Recursively compute (total_size_bytes, file_count) for a directory,
    skipping items we can't access rather than failing the whole scan."""
    total = 0
    count = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        sub_total, sub_count = dir_size(Path(entry.path))
                        total += sub_total
                        count += sub_count
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                        count += 1
                except (PermissionError, FileNotFoundError, OSError):
                    continue
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return total, count


def analyze_project(path: Path):
    """
    Returns a dict:
        {
          "ok": bool,
          "error": str or None,
          "rows": [ {"name": str, "type": "Folder"/"Files", "size": int, "count": int}, ... ],
          "total_size": int,
        }
    Rows are for each immediate subfolder (recursive size) plus one
    aggregated "Files" row for loose files at the top level.
    """
    result = {"ok": False, "error": None, "rows": [], "total_size": 0}

    if not path.exists():
        result["error"] = "Path does not exist or is not accessible."
        return result
    if not path.is_dir():
        result["error"] = "Path is not a directory."
        return result

    rows = []
    files_total = 0
    files_count = 0

    try:
        with os.scandir(path) as it:
            entries = list(it)
    except (PermissionError, FileNotFoundError, OSError) as e:
        result["error"] = f"Cannot read directory: {e}"
        return result

    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                size, count = dir_size(Path(entry.path))
                rows.append({
                    "name": entry.name,
                    "type": "Folder",
                    "size": size,
                    "count": count,
                })
            elif entry.is_file(follow_symlinks=False):
                sz = entry.stat(follow_symlinks=False).st_size
                files_total += sz
                files_count += 1
        except (PermissionError, FileNotFoundError, OSError):
            continue

    if files_count > 0:
        rows.append({
            "name": "Files (top-level)",
            "type": "Files",
            "size": files_total,
            "count": files_count,
        })

    rows.sort(key=lambda r: r["size"], reverse=True)
    total_size = sum(r["size"] for r in rows)

    result["ok"] = True
    result["rows"] = rows
    result["total_size"] = total_size
    return result


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

class PaleoScanDiskUsageApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PaleoScan Project Disk Usage")
        self.geometry("980x640")
        self.minsize(720, 480)

        self.xml_path_var = tk.StringVar(
            value=str(DEFAULT_XML_PATH) if DEFAULT_XML_PATH.exists() else ""
        )
        self.status_var = tk.StringVar(value="Ready.")
        self._worker_queue: "queue.Queue" = queue.Queue()
        self._scanning = False
        self.project_records: list[dict] = []  # [{"path": str, "tab_title": str, "analysis": dict}, ...]

        self._build_top_bar()
        self._build_notebook()
        self._build_status_bar()

        if self.xml_path_var.get():
            self.after(200, self.start_scan)

    # -- UI construction -------------------------------------------------

    def _build_top_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Project list XML:").pack(side="left")

        entry = ttk.Entry(bar, textvariable=self.xml_path_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)

        ttk.Button(bar, text="Browse...", command=self.browse_file).pack(side="left")
        self.scan_btn = ttk.Button(bar, text="Scan", command=self.start_scan)
        self.scan_btn.pack(side="left", padx=(6, 0))
        self.export_btn = ttk.Button(bar, text="Export CSV...", command=self.export_csv, state="disabled")
        self.export_btn.pack(side="left", padx=(6, 0))

    def _build_notebook(self):
        container = ttk.Frame(self, padding=(8, 0, 8, 8))
        container.pack(side="top", fill="both", expand=True)
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)

        placeholder = ttk.Frame(self.notebook)
        ttk.Label(
            placeholder,
            text="Select the PaleoScanProductProjectListBis.xml file and click Scan.",
            padding=20,
        ).pack()
        self.notebook.add(placeholder, text="Info")

    def _build_status_bar(self):
        status = ttk.Frame(self, relief="sunken")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self.status_var, anchor="w", padding=(6, 3)).pack(
            side="left", fill="x", expand=True
        )
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=140)
        self.progress.pack(side="right", padx=6, pady=3)

    # -- Actions -----------------------------------------------------------

    def browse_file(self):
        initial_dir = str((Path.home() / ".paleoscan")) if (Path.home() / ".paleoscan").exists() else str(Path.home())
        path = filedialog.askopenfilename(
            title="Select PaleoScanProductProjectListBis.xml",
            initialdir=initial_dir,
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.xml_path_var.set(path)

    def start_scan(self):
        if self._scanning:
            return
        xml_path_str = self.xml_path_var.get().strip()
        if not xml_path_str:
            messagebox.showwarning("No file selected", "Please select the project list XML file first.")
            return
        xml_path = Path(xml_path_str)
        if not xml_path.exists():
            messagebox.showerror("File not found", f"Could not find:\n{xml_path}")
            return

        self._scanning = True
        self.scan_btn.config(state="disabled")
        self.export_btn.config(state="disabled")
        self.status_var.set("Reading project list...")
        self.progress.start(12)

        self.project_records = []
        for tab in self.notebook.tabs():
            self.notebook.forget(tab)

        thread = threading.Thread(target=self._scan_worker, args=(xml_path,), daemon=True)
        thread.start()
        self.after(100, self._poll_worker)

    def _scan_worker(self, xml_path: Path):
        try:
            project_paths = extract_project_paths(xml_path)
        except Exception as e:
            self._worker_queue.put(("error", f"Failed to parse XML:\n{e}"))
            return

        if not project_paths:
            self._worker_queue.put(("error", "No path_value entries were found in that file."))
            return

        self._worker_queue.put(("count", len(project_paths)))

        for i, p in enumerate(project_paths, start=1):
            project_path = Path(p)
            self._worker_queue.put(("progress", i, len(project_paths), project_path.name or str(project_path)))
            analysis = analyze_project(project_path)
            self._worker_queue.put(("project", str(project_path), analysis))

        self._worker_queue.put(("done", None))

    def _poll_worker(self):
        try:
            while True:
                msg = self._worker_queue.get_nowait()
                kind = msg[0]

                if kind == "count":
                    self.status_var.set(f"Found {msg[1]} project path(s). Scanning disk usage...")

                elif kind == "progress":
                    _, i, total, name = msg
                    self.status_var.set(f"Scanning ({i}/{total}): {name}")

                elif kind == "project":
                    _, path_str, analysis = msg
                    self._add_project_tab(path_str, analysis)

                elif kind == "error":
                    self.progress.stop()
                    self.scan_btn.config(state="normal")
                    self._scanning = False
                    self.status_var.set("Error.")
                    messagebox.showerror("Error", msg[1])
                    return

                elif kind == "done":
                    self.progress.stop()
                    self.scan_btn.config(state="normal")
                    self._scanning = False
                    self._build_summary_tab()
                    if self.project_records:
                        self.export_btn.config(state="normal")
                        self.notebook.select(0)
                    self.status_var.set("Scan complete.")
                    return

        except queue.Empty:
            pass

        if self._scanning:
            self.after(100, self._poll_worker)

    # -- Tab building --------------------------------------------------------

    def _add_project_tab(self, path_str: str, analysis: dict):
        project_path = Path(path_str)
        tab_title = project_path.name or path_str
        # keep tab titles reasonably short & unique
        existing_titles = {self.notebook.tab(t, "text") for t in self.notebook.tabs()}
        base_title = tab_title
        n = 2
        while tab_title in existing_titles:
            tab_title = f"{base_title} ({n})"
            n += 1

        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text=tab_title)

        header = ttk.Frame(frame)
        header.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(header, text=path_str, font=("TkDefaultFont", 9, "italic")).pack(side="left")

        self.project_records.append({"path": path_str, "tab_title": tab_title, "analysis": analysis})

        if not analysis["ok"]:
            ttk.Label(
                frame,
                text=f"Could not scan this project:\n{analysis['error']}",
                foreground="red",
                padding=20,
            ).pack()
            return

        total_label = ttk.Label(
            header,
            text=f"Total: {human_size(analysis['total_size'])}",
            font=("TkDefaultFont", 9, "bold"),
        )
        total_label.pack(side="right")

        columns = ("name", "type", "size", "items", "pct")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("name", text="Subfolder / Files", command=lambda: self._sort_tree(tree, "name", False))
        tree.heading("type", text="Type", command=lambda: self._sort_tree(tree, "type", False))
        tree.heading("size", text="Size", command=lambda: self._sort_tree(tree, "size", True))
        tree.heading("items", text="Items", command=lambda: self._sort_tree(tree, "items", True))
        tree.heading("pct", text="% of Project")
        tree.column("name", width=260, anchor="w")
        tree.column("type", width=80, anchor="center")
        tree.column("size", width=110, anchor="e")
        tree.column("items", width=80, anchor="e")
        tree.column("pct", width=100, anchor="e")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        total = analysis["total_size"] or 1
        for row in analysis["rows"]:
            pct = 100.0 * row["size"] / total
            tree.insert(
                "", "end",
                values=(row["name"], row["type"], human_size(row["size"]), row["count"], f"{pct:.1f}%"),
                tags=(str(row["size"]),),
            )

        if not analysis["rows"]:
            ttk.Label(frame, text="This project folder is empty.", padding=10).pack()

    def _build_summary_tab(self):
        """Insert a 'Summary' tab as the first tab, aggregating totals
        across every scanned project."""
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.insert(0, frame, text="Summary")

        ok_records = [r for r in self.project_records if r["analysis"]["ok"]]
        failed_records = [r for r in self.project_records if not r["analysis"]["ok"]]
        grand_total = sum(r["analysis"]["total_size"] for r in ok_records)

        header = ttk.Frame(frame)
        header.pack(side="top", fill="x", pady=(0, 6))
        summary_text = f"{len(self.project_records)} project(s) scanned"
        if failed_records:
            summary_text += f"  •  {len(failed_records)} could not be read"
        ttk.Label(header, text=summary_text).pack(side="left")
        ttk.Label(
            header,
            text=f"Grand total: {human_size(grand_total)}",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(side="right")

        columns = ("name", "path", "size", "items", "pct", "status")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("name", text="Project", command=lambda: self._sort_tree(tree, "name", False))
        tree.heading("path", text="Path", command=lambda: self._sort_tree(tree, "path", False))
        tree.heading("size", text="Total Size", command=lambda: self._sort_tree(tree, "size", True))
        tree.heading("items", text="Items", command=lambda: self._sort_tree(tree, "items", True))
        tree.heading("pct", text="% of All Projects")
        tree.heading("status", text="Status")
        tree.column("name", width=160, anchor="w")
        tree.column("path", width=320, anchor="w")
        tree.column("size", width=100, anchor="e")
        tree.column("items", width=70, anchor="e")
        tree.column("pct", width=110, anchor="e")
        tree.column("status", width=70, anchor="center")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        total_for_pct = grand_total or 1
        # Sort by size descending by default, failed projects last.
        sorted_records = sorted(
            self.project_records,
            key=lambda r: (not r["analysis"]["ok"], -r["analysis"]["total_size"] if r["analysis"]["ok"] else 0),
        )
        for rec in sorted_records:
            analysis = rec["analysis"]
            if analysis["ok"]:
                size = analysis["total_size"]
                items = sum(row["count"] for row in analysis["rows"])
                pct = f"{100.0 * size / total_for_pct:.1f}%"
                status = "OK"
            else:
                size = 0
                items = 0
                pct = "-"
                status = "Error"
            tree.insert(
                "", "end",
                values=(rec["tab_title"], rec["path"], human_size(size) if analysis["ok"] else "-", items, pct, status),
                tags=(str(size),),
            )

        if not self.project_records:
            ttk.Label(frame, text="No projects were found.", padding=10).pack()

    def export_csv(self):
        if not self.project_records:
            messagebox.showinfo("Nothing to export", "Run a scan first.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Export disk usage to CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="paleoscan_disk_usage.csv",
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                # -- Section 1: per-project summary --
                writer.writerow(["Summary"])
                writer.writerow(["Project", "Path", "Total Size (bytes)", "Total Size", "Items", "Status", "Error"])
                for rec in self.project_records:
                    analysis = rec["analysis"]
                    if analysis["ok"]:
                        items = sum(row["count"] for row in analysis["rows"])
                        writer.writerow([
                            rec["tab_title"], rec["path"], analysis["total_size"],
                            human_size(analysis["total_size"]), items, "OK", "",
                        ])
                    else:
                        writer.writerow([rec["tab_title"], rec["path"], 0, "-", 0, "Error", analysis["error"]])

                grand_total = sum(r["analysis"]["total_size"] for r in self.project_records if r["analysis"]["ok"])
                writer.writerow(["GRAND TOTAL", "", grand_total, human_size(grand_total), "", "", ""])

                # -- Section 2: full subfolder/file breakdown for every project --
                writer.writerow([])
                writer.writerow(["Details"])
                writer.writerow(["Project", "Path", "Entry", "Type", "Size (bytes)", "Size", "Items", "% of Project"])
                for rec in self.project_records:
                    analysis = rec["analysis"]
                    if not analysis["ok"]:
                        continue
                    total = analysis["total_size"] or 1
                    for row in analysis["rows"]:
                        pct = 100.0 * row["size"] / total
                        writer.writerow([
                            rec["tab_title"], rec["path"], row["name"], row["type"],
                            row["size"], human_size(row["size"]), row["count"], f"{pct:.1f}%",
                        ])

            self.status_var.set(f"Exported to {save_path}")
            messagebox.showinfo("Export complete", f"Results exported to:\n{save_path}")
        except OSError as e:
            messagebox.showerror("Export failed", f"Could not write CSV file:\n{e}")

    @staticmethod
    def _sort_tree(tree: ttk.Treeview, col: str, numeric: bool):
        items = [(tree.set(k, col), k) for k in tree.get_children("")]
        if numeric:
            def key(v):
                raw = v[0]
                if col == "items":
                    try:
                        return int(raw)
                    except ValueError:
                        return 0
                # size column shows "12.3 MB" - use the hidden tag (raw bytes) instead
                return 0
            if col == "size":
                items = [(int(tree.item(k, "tags")[0]) if tree.item(k, "tags") else 0, k) for k in tree.get_children("")]
            else:
                items.sort(key=key, reverse=True)
                for idx, (_, k) in enumerate(items):
                    tree.move(k, "", idx)
                return
            items.sort(key=lambda x: x[0], reverse=True)
        else:
            items.sort(key=lambda x: x[0].lower())
        for idx, (_, k) in enumerate(items):
            tree.move(k, "", idx)


def main():
    app = PaleoScanDiskUsageApp()
    app.mainloop()


if __name__ == "__main__":
    main()
