# Local AI Book Finder

A compact Windows command-line tool that uses a small local, CPU-only LLM
(`Qwen3-0.6B` GGUF) to help find and download **legally available PDF books**
via web search.

- Searches the web with `ddgs` (DuckDuckGo)
- Lets the local LLM pick the most promising result URLs (URLs only, no prose)
- Visits those pages and discovers direct PDF links
- Streams the chosen PDF into your `Downloads` folder with strict validation
  (`%PDF-` magic signature, ZIP/HTML rejection, no silent overwrites)
- Never bypasses paywalls, DRM, authentication, or library borrowing limits

## Requirements

- Windows 10/11 x64
- Python 3.11 (64-bit)
- ~1 GB free disk space (model is ~640 MB, cached in your Hugging Face cache)
- Internet connection (search + first-run model download)

## Installation

```powershell
cd <this folder>
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

> `requirements.txt` includes the official **CPU-only** wheel index for
> `llama-cpp-python` (`https://abetlen.github.io/llama-cpp-python/whl/cpu`),
> so no C++ compiler is needed on Windows. If that index is ever unavailable,
> install the Microsoft C++ Build Tools and `pip install llama-cpp-python`
> will compile from source (slower).

## Usage

### Desktop app (recommended)

```powershell
.venv\Scripts\python gui.py
```

or double-click `dist\BookFinder\BookFinder.exe` after building.

1. Type a book title (plus author) and press **Enter**.
2. The app searches the Internet Archive and the web, then lists the PDF files
   it found with their website and size.
3. Select a file and click **Download selected** (or double-click it). A
   progress bar shows size and speed; **Cancel** stops it.
4. If a download fails, the app automatically tries the next file (you can
   turn this off). Select several rows to download them all.
5. Double-click a saved file to open it. **Change...** picks the save folder
   (default: `Downloads`).

### Terminal version

```powershell
.venv\Scripts\python main.py
```

Interaction:

```text
Book to search (q to quit): frankenstein mary shelley
Found 20 search results. Selecting URLs with the local LLM ...

Search result URLs:
https://www.gutenberg.org/ebooks/84
...

Looking for PDFs ...

PDF candidates:
1. https://www.gutenberg.org/cache/epub/84/pg84.pdf
2. ...
PDF number (Enter = next book): 1
Downloaded: C:\Users\you\Downloads\pg84.pdf
Book to search (q to quit):
```

- Press **Enter** at the PDF prompt to skip to the next book.
- Type `q`, `quit`, or `exit` at the book prompt to quit.
- `--debug` (or `BOOKFINDER_DEBUG=1`) prints diagnostic tracebacks.

## Model download behavior

On first launch the app downloads
`Qwen/Qwen3-0.6B-GGUF` → `Qwen3-0.6B-Q8_0.gguf` (~640 MB) into the standard
Hugging Face cache (`~/.cache/huggingface` on most systems,
`C:\Users\<you>\.cache\huggingface\hub` on Windows) via `huggingface-hub`.
Later launches reuse the cache. The model is **never bundled** into the
executable. If the download or model init fails, the app warns and continues
using raw search results.

Inference settings: CPU only (`n_gpu_layers=0`), up to 8 threads,
4096-token context.

## What gets downloaded

Only files that pass these checks are saved:

- Streaming download with ordinary browser User-Agent
- HTTP status checked, redirects followed
- `%PDF-` magic signature must appear within the first 1024 bytes
  (rejects HTML error pages, paywall redirects, and ZIP archives)
- Written chunk-by-chunk to a `.part` temp file, renamed to `.pdf` only on
  success; partial files are deleted on failure
- Filenames sanitized for Windows; existing files are never overwritten
  (`book.pdf` → `book (1).pdf`)

Destination: `C:\Users\<you>\Downloads\<title>.pdf`

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .
```

Tests cover PDF signature validation, ZIP/HTML rejection (mocked HTTP),
filename sanitization + path traversal, duplicate-name handling, candidate
number validation, and LLM URL allowlisting. No network or model needed.

## Packaging a standalone .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Output: `dist\BookFinder\BookFinder.exe` (onedir, desktop window, no Python
required on the target machine). The optional `--onefile` command is provided
commented inside `build.ps1`.

### Testing the packaged exe on a clean Windows PC

1. Copy the whole `dist\BookFinder\` folder to the target machine (no Python
   install needed).
2. Ensure internet access and ~1 GB free disk for the model cache.
3. Run `BookFinder.exe`, wait for the first-run model download, search a book.
4. Confirm a PDF lands in the user's `Downloads` folder.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Web search failed` | DuckDuckGo rate-limited the IP; wait a minute and retry. |
| Model download fails | Check connection/disk space; delete partial files in `~\.cache\huggingface\hub` and retry. |
| `llama-cpp-python` build error | Install MSVC Build Tools, or use a prebuilt wheel (see Installation). |
| Slow first token | Expected on weak CPUs; the model is small but still runs on CPU. |
| "Rejected: HTML page" | That candidate was not a real PDF; pick another number. |
| No PDF candidates | The top pages had no PDF links; try a more specific query (e.g. add the author or "pdf"). |

## Legal note

The tool only searches public web results and downloads files the hosting
server actually provides. It does not bypass paywalls, DRM, authentication, or
library borrowing restrictions, and it does not judge legality beyond obvious
signals (public domain / open access / publisher-hosted). You are responsible
for respecting copyright in your jurisdiction.

## Project layout

```text
local_pdf_searcher/
├── main.py                       # search/download logic + terminal UI
├── gui.py                        # desktop window (Tkinter)
├── requirements.txt
├── README.md
├── build.ps1                     # PyInstaller build script
├── .gitignore
└── tests/
    └── test_pdf_validation.py
```
