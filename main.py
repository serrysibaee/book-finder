#!/usr/bin/env python3
"""Local AI Book Finder.

Searches the Internet Archive and the web for a book, ranks the results with
simple rules (trusted free sources, title match, direct PDF links), discovers
direct PDF links on those pages, and streams valid PDFs into the user's
Downloads folder. No AI model is needed.
"""

import argparse
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_SEARCH_RESULTS = 20
MAX_RESULT_URLS = 6          # ranked web results passed to page scraping
MAX_PAGES_TO_SCRAPE = 8
MAX_PDF_CANDIDATES = 20
MAX_ARCHIVE_ITEMS = 4      # Internet Archive items added per search
STOP_WORDS = {"the", "and", "for", "with", "book", "books", "pdf", "free",
              "download", "ebook", "novel", "full", "text"}
MAX_HTML_BYTES = 2_000_000   # bounded page read
REQUEST_TIMEOUT = (15, 60)   # (connect, read) seconds
CHUNK_SIZE = 64 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

QUIT_WORDS = {"q", "quit", "exit"}
ARCHIVE_EXTS = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
                ".epub", ".mobi", ".azw", ".azw3", ".djvu")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
IA_RE = re.compile(r"archive\.org/(?:details|embed|stream)/([^/?#\s\"]+)")

DEBUG = False


def debug(*args) -> None:
    if DEBUG:
        print("[debug]", *args)


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

def search_archive(query: str) -> list[str]:
    """Internet Archive item pages with freely downloadable PDFs, most popular first."""
    # Every meaningful query word must appear in the title or author;
    # a plain full-text search returns popular but unrelated items.
    words = [w for w in re.findall(r"\w+", query.lower())
             if len(w) > 2 and w not in STOP_WORDS]
    if not words:
        return []
    terms = " AND ".join(f"(title:{w} OR creator:{w})" for w in words)
    try:
        import requests
        resp = requests.get(
            "https://archive.org/advancedsearch.php",
            params={"q": f"{terms} AND mediatype:texts AND format:PDF"
                         " AND NOT access-restricted-item:true",
                    "fl[]": "identifier", "rows": MAX_ARCHIVE_ITEMS,
                    "sort[]": "downloads desc", "output": "json"},
            headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        debug("archive.org search failed:", exc)
        return []
    return [f"https://archive.org/details/{quote(d['identifier'])}"
            for d in docs if d.get("identifier")]


def search_web(query: str) -> list[dict]:
    """Return [{'title', 'url', 'body'}] for the query, or [] if nothing found."""
    try:
        from ddgs import DDGS
    except ImportError:
        raise RuntimeError("ddgs is not installed (pip install -r requirements.txt).")
    try:
        raw = DDGS().text(query, region="wt-wt", safesearch="moderate",
                          max_results=MAX_SEARCH_RESULTS)
    except Exception as exc:
        debug(traceback.format_exc())
        raise RuntimeError(f"Web search failed ({exc.__class__.__name__}). "
                           "The search service may be unavailable or rate-limited.")
    results = []
    for item in raw or []:
        url = (item.get("href") or "").strip()
        if url.startswith("http"):
            results.append({
                "title": (item.get("title") or "").strip(),
                "url": url,
                "body": (item.get("body") or "").strip(),
            })
    return results


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment and trailing slash for comparison."""
    url = url.strip().rstrip(".,;:)]}'\"")
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


def url_file_path(url: str) -> str:
    return unquote(urlparse(url).path).lower()


def looks_like_pdf_url(url: str) -> bool:
    return url_file_path(url).endswith(".pdf")


def is_ignored_download(url: str) -> bool:
    path = url_file_path(url)
    return any(path.endswith(ext) for ext in ARCHIVE_EXTS)


TRUSTED_SITES = (
    "archive.org", "gutenberg.org", "gutenberg.ca", "gutenberg.net.au",
    "standardebooks.org", "wikisource.org", "hathitrust.org", "openlibrary.org",
    "doabooks.org", "oapen.org", "openstax.org", "arxiv.org", "ncbi.nlm.nih.gov",
    "europeana.eu", "loc.gov", "bl.uk", "ibiblio.org", "planetebook.com",
    "feedbooks.com", "manybooks.net", "bookstacks.org", "libretexts.org",
)
TRUSTED_SUFFIXES = (".edu", ".gov", ".ac.uk", ".edu.au", ".edu.sa", ".ac.jp")
SKIPPED_SITES = (
    "amazon.", "goodreads.com", "ebay.", "barnesandnoble.com", "audible.",
    "books.google.", "play.google.com", "apple.com", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "pinterest.", "youtube.com",
    "tiktok.com", "scribd.com", "wikipedia.org", "imdb.com", "sparknotes.com",
    "litcharts.com", "quora.com", "reddit.com", "linkedin.com",
)
WORD_RE = re.compile(r"\w+", re.UNICODE)


def site_matches(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site) or host.startswith(site)


def score_result(query: str, result: dict) -> float | None:
    """Rule-based score for a search result; None means skip it entirely."""
    host = urlparse(result["url"]).netloc.lower().removeprefix("www.")
    if any(site_matches(host, s) for s in SKIPPED_SITES):
        return None
    score = 0.0
    if any(site_matches(host, s) for s in TRUSTED_SITES) or host.endswith(TRUSTED_SUFFIXES):
        score += 5
    if looks_like_pdf_url(result["url"]):
        score += 4
    text = f"{result['title']} {result['body']}".lower()
    if "pdf" in text:
        score += 1
    if any(w in text for w in ("public domain", "free download", "open access")):
        score += 1
    words = {w for w in WORD_RE.findall(query.lower()) if len(w) > 2}
    if words:
        found = set(WORD_RE.findall(f"{result['title']} {result['url']}".lower()))
        score += 3 * len(words & found) / len(words)
    return score


def rank_results(query: str, results: list[dict]) -> list[str]:
    """Best result URLs first; shops, social media and reviews are dropped."""
    scored = []
    for order, result in enumerate(results):
        score = score_result(query, result)
        if score is not None:
            scored.append((-score, order, result["url"]))  # ties keep search order
    return [url for _, _, url in sorted(scored)][:MAX_RESULT_URLS]


def merge_urls(*groups: list[str]) -> list[str]:
    """Concatenate URL lists, dropping duplicates (first occurrence wins)."""
    seen, merged = set(), []
    for url in (u for g in groups for u in g):
        if normalize_url(url) not in seen:
            seen.add(normalize_url(url))
            merged.append(url)
    return merged


# ---------------------------------------------------------------------------
# PDF discovery
# ---------------------------------------------------------------------------

def is_pdf_bytes(head: bytes) -> bool:
    """True if the PDF magic signature appears near the start of the data."""
    idx = head.find(b"%PDF-")
    return idx != -1 and idx < 1024


def describe_invalid(head: bytes, content_type: str) -> str:
    stripped = head.lstrip()[:200].lower()
    if head[:2] == b"PK" or stripped.startswith(b"pk\x03\x04"):
        return "the server sent a ZIP archive"
    if stripped.startswith((b"<!doctype", b"<html", b"<head", b"<?xml", b"<")):
        return "the server sent an HTML page"
    if content_type:
        return f"unexpected content type '{content_type.split(';')[0]}'"
    return "no PDF signature found"


def add_ia_pdf_links(page_url: str, add) -> None:
    """For Internet Archive item pages, add their individual PDF files."""
    match = IA_RE.search(page_url)
    if not match:
        return
    ident = match.group(1)
    try:
        from requests import get as _get
        resp = _get(f"https://archive.org/metadata/{quote(ident)}",
                    headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        debug("IA metadata failed:", exc)
        return
    metadata = data.get("metadata") or {}
    if metadata.get("access-restricted-item") in (True, "true"):
        debug(f"IA item {ident} is access-restricted; skipping.")
        return
    # Use the stable /download/ URL; archive.org redirects it to the right
    # storage node. ("server" and "d1" are both bare hostnames, so joining
    # them produced unreachable URLs.)
    for entry in data.get("files") or []:
        name = entry.get("name") or ""
        if not name.lower().endswith(".pdf"):
            continue
        add(f"https://archive.org/download/{quote(ident)}/{quote(name)}")


def discover_pdfs(urls: list[str]) -> list[str]:
    """Visit result pages (bounded) and collect direct PDF candidate URLs."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError("requests/beautifulsoup4 missing (pip install -r requirements.txt).")

    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str, base: str | None = None) -> None:
        if not url:
            return
        url = urljoin(base, url) if base else url
        if not url.startswith("http") or is_ignored_download(url):
            return
        key = normalize_url(url)
        if key in seen:
            return
        seen.add(key)
        candidates.append(url)

    for page in urls[:MAX_PAGES_TO_SCRAPE]:
        if len(candidates) >= MAX_PDF_CANDIDATES:
            break
        try:
            if looks_like_pdf_url(page):
                add(page)
                continue
            if IA_RE.search(page):  # metadata API lists the PDFs directly
                add_ia_pdf_links(page, add)
                continue
            resp = requests.get(page, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                                stream=True, allow_redirects=True)
            with resp:
                final = resp.url
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                head = b""
                for chunk in resp.iter_content(8192):
                    head += chunk
                    if len(head) >= 1024:
                        break
                if ctype == "application/pdf" or is_pdf_bytes(head):
                    add(final)
                    continue
                if looks_like_pdf_url(final):
                    add(final)
                    continue
                if ctype and "html" not in ctype:
                    debug(f"skipping non-HTML page: {final} ({ctype})")
                    continue

                body = head
                for chunk in resp.iter_content(8192):
                    body += chunk
                    if len(body) >= MAX_HTML_BYTES:
                        break
                html = body.decode(resp.encoding or "utf-8", errors="replace")

                add_ia_pdf_links(final, add)

                soup = BeautifulSoup(html, "html.parser")
                meta = soup.find("meta", attrs={"name": re.compile(r"^citation_pdf_url$", re.I)})
                if meta and meta.get("content"):
                    add(meta["content"].strip(), base=final)
                for anchor in soup.find_all("a", href=True):
                    href = anchor["href"].strip()
                    if href.lower().startswith(("javascript:", "mailto:", "#")):
                        continue
                    if looks_like_pdf_url(urljoin(final, href)):
                        add(href, base=final)
        except Exception as exc:
            debug(f"scrape failed for {page}: {exc}")
            continue

    return candidates[:MAX_PDF_CANDIDATES]


# ---------------------------------------------------------------------------
# Safe downloading
# ---------------------------------------------------------------------------

def sanitize_filename(name: str, default: str = "book") -> str:
    """Make a string safe to use as a Windows filename (no path traversal)."""
    name = unquote(name or "").replace("\\", "/")
    name = name.rsplit("/", 1)[-1]              # basename only
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > 120:
        name = name[:120].rstrip()
    if not name:
        name = default
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        name = "_" + name
    return name


def unique_path(directory: Path, stem: str, suffix: str = ".pdf") -> Path:
    """Return directory/stem.pdf, adding ' (1)', ' (2)' ... if it exists."""
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def parse_selection(raw: str, count: int):
    """Classify candidate-menu input: 'next', 'invalid', or a 1-based index."""
    raw = raw.strip()
    if not raw:
        return "next"
    if raw.isdigit():
        number = int(raw)
        if 1 <= number <= count:
            return number
    return "invalid"


def describe_error(exc: Exception) -> str:
    """Short, user-readable reason for a failed request."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        hints = {401: "login required", 403: "site refused access",
                 404: "file not found", 429: "rate-limited, try later"}
        return f"HTTP {status}" + (f", {hints[status]}" if status in hints else "")
    name = exc.__class__.__name__
    if "SSL" in name:
        return "secure connection (SSL) error"
    if "Timeout" in name:
        return "server took too long to respond"
    if "Connection" in name:
        return "could not connect to the server"
    return name


def filename_from_response(resp) -> str:
    """Best file stem: Content-Disposition filename, else the final URL path."""
    disp = resp.headers.get("Content-Disposition") or ""
    match = (re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disp, re.I)
             or re.search(r'filename\s*=\s*"?([^";]+)"?', disp, re.I))
    name = unquote(match.group(1).strip()) if match else unquote(urlparse(resp.url).path)
    return sanitize_filename(Path(name).stem)


def download_pdf(url: str, dest_dir: Path | None = None, progress=None,
                 cancel=None) -> tuple[bool, str]:
    """Stream a PDF to the Downloads folder with magic-byte validation.

    Returns (ok, message). On success the message is the saved file path.
    A temporary '.part' file is used and removed on any failure.
    progress(written_bytes, total_bytes_or_0) is called as data arrives;
    cancel is an optional threading.Event that aborts the download.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests is not installed.")
    if dest_dir is None:
        dest_dir = Path.home() / "Downloads"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create Downloads folder: {exc}"

    tmp_path: Path | None = None
    try:
        resp = None
        for attempt in range(2):  # one retry for transient connection drops
            try:
                resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                                    stream=True, allow_redirects=True)
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 1:
                    raise
                debug(f"retrying {url}")
        with resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()

            fd, tmp_name = tempfile.mkstemp(dir=dest_dir, suffix=".part")
            tmp_path = Path(tmp_name)
            got_magic = False
            written = 0
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if cancel is not None and cancel.is_set():
                        return False, "Cancelled."
                    if not chunk:
                        continue
                    if not got_magic:
                        if not is_pdf_bytes(chunk[:1024]):
                            return False, f"Rejected: {describe_invalid(chunk, ctype)}."
                        got_magic = True
                    handle.write(chunk)
                    written += len(chunk)
                    if progress is not None:
                        progress(written, total)
            if not got_magic or written == 0:
                return False, "Rejected: empty response."

            stem = filename_from_response(resp)
            final_path = unique_path(dest_dir, stem)
            os.replace(tmp_path, final_path)
            tmp_path = None
            return True, str(final_path)
    except ImportError as exc:
        debug(traceback.format_exc())
        raise RuntimeError(f"Missing dependency: {exc}")
    except Exception as exc:
        debug(traceback.format_exc())
        return False, f"Download failed ({describe_error(exc)})."
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Main interaction loop
# ---------------------------------------------------------------------------

def handle_query(query: str) -> None:
    archive_urls = search_archive(query)
    try:
        results = search_web(query) or search_web(f"{query} pdf")
    except RuntimeError as exc:
        print(exc)
        results = []
    print(f"Found {len(archive_urls)} Internet Archive items and "
          f"{len(results)} web results.")
    urls = merge_urls(archive_urls, rank_results(query, results))
    if not urls:
        print("No usable search-result URLs.")
        return
    print("\nSearch result URLs:")
    for url in urls:
        print(url)

    print("\nLooking for PDFs ...")
    candidates = discover_pdfs(urls)
    if not candidates:
        print("No PDF candidates found.")
        return

    while True:
        print("\nPDF candidates:")
        for i, url in enumerate(candidates, 1):
            print(f"{i}. {url}")
        choice = input("PDF number (Enter = next book): ")
        selection = parse_selection(choice, len(candidates))
        if selection == "next":
            return
        if selection == "invalid":
            print("Invalid selection.")
            continue
        ok, message = download_pdf(candidates[selection - 1])
        if ok:
            print(f"Downloaded: {message}")
            return
        print(message)


def main(argv=None) -> int:
    global DEBUG
    # Don't crash printing non-ASCII paths/titles on legacy Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser =argparse.ArgumentParser(description="Book finder (PDF search + download).")
    parser.add_argument("--debug", action="store_true",
                        help="print tracebacks and diagnostic details")
    args = parser.parse_args(argv)
    DEBUG = args.debug or os.environ.get("BOOKFINDER_DEBUG") == "1"

    print("Book Finder - legal PDF search")
    print("Sources are limited to what search engines return; no paywall or DRM bypass.\n")

    while True:
        try:
            query = input("Book to search (q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0
        if query.lower() in QUIT_WORDS:
            print("Bye.")
            return 0
        if not query:
            continue
        try:
            handle_query(query)
        except KeyboardInterrupt:
            print("\nSearch interrupted.")
        except RuntimeError as exc:
            print(exc)
        except Exception as exc:
            debug(traceback.format_exc())
            print(f"Unexpected error ({exc.__class__.__name__}). Use --debug for details.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBye.")
        sys.exit(130)
