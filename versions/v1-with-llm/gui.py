#!/usr/bin/env python3
"""Desktop window for Local AI Book Finder.

All search, discovery and download logic lives in main.py; this module only
runs it on background threads and shows the results in a Tkinter window.
"""

import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

# A windowed exe has no console: give libraries somewhere harmless to print.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import main as core

APP_TITLE = "Book Finder"
PAD = 10


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return ""


def display_name(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name) or url
    return name if len(name) <= 70 else name[:67] + "..."


class BookFinderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.llm = None
        self.model_state = "loading"
        self.busy = False
        self.cancel_event = threading.Event()
        self.rows: dict[str, dict] = {}   # tree item id -> {url, path, state}
        self.search_id = 0
        self.dest_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.use_ai = tk.BooleanVar(value=True)
        self.auto_next = tk.BooleanVar(value=True)

        self._build_ui()
        self.root.after(100, self._drain_events)
        threading.Thread(target=self._load_model, daemon=True).start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = self.root
        root.title(APP_TITLE)
        root.geometry("980x640")
        root.minsize(760, 480)

        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 16))
        style.configure("Muted.TLabel", foreground="#666666")
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", rowheight=26)

        outer = ttk.Frame(root, padding=PAD * 1.5)
        outer.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Book Finder", style="Title.TLabel").pack(side="left")
        self.model_label = ttk.Label(header, text="AI model: loading...",
                                     style="Muted.TLabel")
        self.model_label.pack(side="right")
        ttk.Label(outer, text="Find freely available PDF books (public domain, open "
                  "access, publisher-hosted). No paywall or DRM bypass.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, PAD))

        # Search row
        search = ttk.Frame(outer)
        search.pack(fill="x")
        self.query = ttk.Entry(search, font=("Segoe UI", 12))
        self.query.pack(side="left", fill="x", expand=True, ipady=4)
        self.query.bind("<Return>", lambda _e: self.start_search())
        self.query.focus_set()
        self.search_btn = ttk.Button(search, text="Search", style="Accent.TButton",
                                     command=self.start_search)
        self.search_btn.pack(side="left", padx=(PAD, 0), ipady=3, ipadx=8)

        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(6, PAD))
        ttk.Checkbutton(opts, text="Use local AI to pick the best websites",
                        variable=self.use_ai).pack(side="left")
        ttk.Checkbutton(opts, text="If a download fails, try the next result",
                        variable=self.auto_next).pack(side="left", padx=(PAD * 2, 0))

        # Results table
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        cols = ("file", "site", "size", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings",
                                 selectmode="extended")
        for col, title, width, stretch in (("file", "File", 420, True),
                                           ("site", "Website", 200, False),
                                           ("size", "Size", 80, False),
                                           ("status", "Status", 230, False)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=stretch,
                             anchor="e" if col == "size" else "w")
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("fail", foreground="#b42318")
        self.tree.tag_configure("active", foreground="#0b5cad")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())

        # Action buttons
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(PAD, 0))
        self.download_btn = ttk.Button(actions, text="Download selected",
                                       style="Accent.TButton",
                                       command=self.start_download)
        self.download_btn.pack(side="left", ipadx=6)
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self.cancel)
        self.cancel_btn.pack(side="left", padx=(6, 0))
        self.open_btn = ttk.Button(actions, text="Open file", command=self.open_selected)
        self.open_btn.pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Open folder", command=self.open_folder).pack(
            side="left", padx=(6, 0))

        ttk.Button(actions, text="Change...", command=self.choose_folder).pack(side="right")
        ttk.Label(actions, textvariable=self.dest_dir, style="Muted.TLabel").pack(
            side="right", padx=(0, 6))
        ttk.Label(actions, text="Save to:").pack(side="right", padx=(0, 4))

        # Progress + status
        prog = ttk.Frame(outer)
        prog.pack(fill="x", pady=(PAD, 0))
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status = ttk.Label(prog, text="Type a book title (and author) and press Enter.",
                                style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(4, 0))

        self._update_buttons()

    def _update_buttons(self):
        selected = self.tree.selection()
        self.search_btn.state(["disabled"] if self.busy else ["!disabled"])
        self.download_btn.state(["!disabled"] if selected and not self.busy else ["disabled"])
        self.cancel_btn.state(["!disabled"] if self.busy else ["disabled"])
        done = any(self.rows.get(i, {}).get("path") for i in selected)
        self.open_btn.state(["!disabled"] if done else ["disabled"])

    # ------------------------------------------------- thread -> UI events
    def post(self, func, *args):
        self.events.put((func, args))

    def _drain_events(self):
        try:
            while True:
                func, args = self.events.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def set_status(self, text: str):
        self.status.configure(text=text)

    def set_busy(self, busy: bool, indeterminate: bool = False):
        self.busy = busy
        if busy and indeterminate:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        self._update_buttons()

    # --------------------------------------------------------------- model
    def _load_model(self):
        try:
            llm = core.load_llm(core.ensure_model())
            self.post(self._model_ready, llm, None)
        except Exception as exc:
            self.post(self._model_ready, None, exc)

    def _model_ready(self, llm, exc):
        self.llm = llm
        if llm is not None:
            self.model_state = "ready"
            self.model_label.configure(text="AI model: ready")
        else:
            self.model_state = "failed"
            self.model_label.configure(text="AI model: unavailable (using plain search)")
            core.debug(f"model failed: {exc}")

    # -------------------------------------------------------------- search
    def start_search(self):
        query = self.query.get().strip()
        if not query or self.busy:
            return
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.search_id += 1
        self.cancel_event.clear()
        self.set_busy(True, indeterminate=True)
        llm = self.llm if self.use_ai.get() else None
        threading.Thread(target=self._search_worker,
                         args=(query, llm, self.search_id), daemon=True).start()

    def _search_worker(self, query: str, llm, search_id: int):
        try:
            self.post(self.set_status, f'Searching the Internet Archive for "{query}"...')
            archive_urls = core.search_archive(query)
            self.post(self.set_status, f'Searching the web for "{query}"...')
            try:
                results = core.search_web(query) or core.search_web(f"{query} pdf")
            except RuntimeError:
                results = []  # web search down/rate-limited: archive.org may suffice
            if not results and not archive_urls:
                self.post(self._search_done, [], "No search results. Try different words.")
                return
            if llm is not None:
                self.post(self.set_status,
                          f"Found {len(results)} results. AI is choosing the best websites...")
            elif self.use_ai.get() and self.model_state == "loading":
                self.post(self.set_status,
                          "AI model still loading; using plain search results...")
            urls = core.merge_urls(archive_urls,
                                   core.select_urls(llm, query, results) if results else [])
            if self.cancel_event.is_set():
                self.post(self._search_done, [], "Search cancelled.")
                return
            self.post(self.set_status, f"Looking for PDF files on {len(urls)} websites...")
            candidates = core.discover_pdfs(urls)
            if not candidates:
                self.post(self._search_done, [],
                          "No PDF files found. Try adding the author's name.")
                return
            self.post(self._search_done, candidates,
                      f"Found {len(candidates)} PDF files. Select one and click "
                      "Download (or double-click it).")
            self._probe_sizes(candidates, search_id)
        except Exception as exc:
            self.post(self._search_done, [], f"Search failed: {exc}")

    def _search_done(self, candidates: list[str], message: str):
        for url in candidates:
            item = self.tree.insert("", "end", values=(
                display_name(url), urlparse(url).netloc, "...", "Ready"))
            self.rows[item] = {"url": url, "path": None}
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
        self.set_busy(False)
        self.set_status(message)

    def _probe_sizes(self, candidates: list[str], search_id: int):
        """Fill the Size column with a quick HEAD request per file."""
        import requests

        def probe(url):
            try:
                r = requests.head(url, headers=core.HEADERS, timeout=(5, 10),
                                  allow_redirects=True)
                size = int(r.headers.get("Content-Length") or 0)
                return url, human_size(size) if r.ok and size else "?"
            except Exception:
                return url, "?"

        with ThreadPoolExecutor(max_workers=6) as pool:
            for url, size in pool.map(probe, candidates):
                self.post(self._set_size, url, size, search_id)

    def _set_size(self, url: str, size: str, search_id: int):
        if search_id != self.search_id:
            return
        for item, row in self.rows.items():
            if row["url"] == url and self.tree.exists(item):
                self.tree.set(item, "size", size)

    # ------------------------------------------------------------ download
    def start_download(self, items=None):
        if self.busy:
            return
        items = list(items or self.tree.selection())
        items = [i for i in items if not self.rows[i]["path"]]
        if not items:
            return
        self.cancel_event.clear()
        self.set_busy(True)
        dest = Path(self.dest_dir.get())
        fallback = self.auto_next.get() and len(items) == 1
        queue_items = list(items)
        if fallback:  # after the chosen file, try the rest in table order
            order = list(self.tree.get_children())
            start = order.index(items[0])
            queue_items += [i for i in order[start + 1:] + order[:start]
                            if not self.rows[i]["path"]]
        threading.Thread(target=self._download_worker,
                         args=(queue_items, dest, fallback), daemon=True).start()

    def _download_worker(self, queue_items: list[str], dest: Path, fallback: bool):
        saved, last_error = [], ""
        for n, item in enumerate(queue_items):
            if self.cancel_event.is_set():
                break
            if n > 0 and fallback and saved:
                break
            url = self.rows[item]["url"]
            self.post(self._set_row, item, "Connecting...", "active")
            self.post(self.set_status, f"Downloading {display_name(url)}")
            started = time.monotonic()
            last_post = [0.0]

            def progress(written, total, item=item):
                now = time.monotonic()
                if now - last_post[0] < 0.1:
                    return
                last_post[0] = now
                speed = written / max(now - started, 0.001)
                pct = written * 100 / total if total else None
                text = (f"{human_size(written)} of {human_size(total)}" if total
                        else human_size(written)) + f"  -  {human_size(speed)}/s"
                self.post(self._show_progress, item, pct, text)

            ok, message = core.download_pdf(url, dest, progress=progress,
                                            cancel=self.cancel_event)
            if ok:
                self.rows[item]["path"] = message
                saved.append(message)
                self.post(self._set_row, item, "Saved", "ok")
            else:
                last_error = message
                self.post(self._set_row, item, message.rstrip("."), "fail")
                if fallback and not self.cancel_event.is_set():
                    self.post(self.set_status, "That file failed; trying the next one...")
        self.post(self._download_done, saved, last_error)

    def _set_row(self, item: str, status: str, tag: str):
        if self.tree.exists(item):
            self.tree.set(item, "status", status)
            self.tree.item(item, tags=(tag,))

    def _show_progress(self, item: str, pct, text: str):
        if pct is None:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=pct)
        self._set_row(item, "Downloading" + (f" {pct:.0f}%" if pct is not None else ""),
                      "active")
        self.set_status(text)

    def _download_done(self, saved: list[str], last_error: str):
        self.set_busy(False)
        if saved:
            self.progress.configure(value=100)
            names = ", ".join(Path(p).name for p in saved)
            self.set_status(f"Saved: {names}  (double-click to open)")
        elif self.cancel_event.is_set():
            self.set_status("Download cancelled.")
        else:
            self.set_status(f"{last_error} Pick another file from the list.")

    def cancel(self):
        self.cancel_event.set()
        self.set_status("Cancelling...")

    # ---------------------------------------------------------------- misc
    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if self.rows[item]["path"]:
            self._open_path(self.rows[item]["path"])
        else:
            self.start_download([item])

    def open_selected(self):
        for item in self.tree.selection():
            if self.rows[item]["path"]:
                self._open_path(self.rows[item]["path"])
                return

    def open_folder(self):
        folder = Path(self.dest_dir.get())
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(str(folder))

    def choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.dest_dir.get(),
                                         title="Choose where to save PDFs")
        if chosen:
            self.dest_dir.set(str(Path(chosen)))

    def _open_path(self, path: str):
        try:
            os.startfile(path)  # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not open:\n{path}\n\n{exc}")


def run() -> int:
    try:  # crisp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    BookFinderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
