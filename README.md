# Book Finder

A small Windows desktop app that finds **freely and legally available PDF
books** and downloads them with one click.

Type a title, get a list of PDFs from the Internet Archive and the open web,
pick one, done. No account, no AI model, nothing to set up.

## Features

- **Two sources in one search.** The Internet Archive catalogue (matched on
  title and author) plus a DuckDuckGo web search.
- **Smart ranking without AI.** Web results are scored by simple rules:
  trusted free sources (archive.org, Project Gutenberg, university and
  government sites, ...) first, then title match and direct `.pdf` links.
  Shops, social media and review sites are skipped.
- **Reliable downloads.**
  - Live progress bar with size and speed, and a Cancel button
  - If a file fails, the next one is tried automatically
  - Select several rows to download them in one go
- **Safe files only.**
  - Every file is checked for a real PDF signature; HTML error pages and
    ZIP archives are rejected
  - Downloads go to a temporary `.part` file and are renamed only when
    complete
  - Existing files are never overwritten (`book.pdf` becomes `book (1).pdf`)
- **Clear errors**, e.g. `HTTP 403, site refused access` or
  `could not connect to the server`.

## Download and run

1. Get the latest `BookFinder` folder from the
   [Releases](../../releases) page, or build it yourself (see below).
2. Open the folder and run `BookFinder.exe`. Python is not needed.

> Keep the whole `BookFinder` folder together. The `.exe` needs the files
> next to it.

## How to use

1. Type a book title, ideally with the author (e.g. `the time machine wells`),
   and press **Enter**.
2. Select a PDF in the list and click **Download selected**, or double-click
   it.
3. Double-click a saved file to open it, or click **Open folder**.

Files are saved to your `Downloads` folder. Click **Change...** to pick
another one.

## Run from source

Requires Windows 10/11 and Python 3.11+.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python gui.py      # desktop app
.venv\Scripts\python main.py     # terminal version
```

Add `--debug` to the terminal version (or set `BOOKFINDER_DEBUG=1`) to
print diagnostic details.

## Build the .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

This creates the virtual environment if needed, installs dependencies and
PyInstaller, and writes the app to `dist\BookFinder\BookFinder.exe`
(about 50 MB).

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .
```

The tests cover PDF validation, ZIP/HTML rejection, file name cleaning,
duplicate names and result ranking. They need no network.

## Troubleshooting

| Problem | What to do |
|---|---|
| `HTTP 403` / `HTTP 404` | The website refused or no longer has the file. Pick another one from the list. |
| `HTTP 500` | The website's server had an error. Try again later or pick another file. |
| `Rejected: the server sent an HTML page` | That link was a web page, not a PDF. Pick another file. |
| `Web search failed` | DuckDuckGo is rate-limiting you. Wait a minute and retry; Internet Archive results still work. |
| No PDF files found | Add the author's name, or try the book's original title. |
| Windows SmartScreen warning | The `.exe` isn't code-signed. Click **More info** and then **Run anyway**. |

## Project layout

```text
├── gui.py                  # desktop window (Tkinter)
├── main.py                 # search, ranking, download logic + terminal version
├── build.ps1               # builds the .exe with PyInstaller
├── requirements.txt
├── tests/                  # unit tests
└── versions/v1-with-llm/   # earlier version that used a local LLM for ranking
```

## Legal note

Book Finder only lists files that public websites already offer for
download. It does not bypass paywalls, DRM, logins or library lending
limits. Many books online are still under copyright: make sure you have the
right to download a book in your country.
