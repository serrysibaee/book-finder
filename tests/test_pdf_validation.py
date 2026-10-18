"""Tests for PDF validation, selection parsing, and filename sanitization.

Run from the project root:
    python -m unittest discover -s tests -t .
or:
    python -m pytest tests/
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Minimal stand-in for requests.Response (streaming + context manager)."""

    def __init__(self, body=b"", status=200, headers=None, url="https://x.example/file",
                 encoding="utf-8"):
        self._body = body
        self.status_code = status
        self.headers = headers or {}
        self.url = url
        self.encoding = encoding

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# PDF magic-signature validation
# ---------------------------------------------------------------------------

class TestPdfSignature(unittest.TestCase):
    def test_valid_pdf_at_start(self):
        self.assertTrue(main.is_pdf_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"))

    def test_valid_pdf_with_leading_junk_within_1024(self):
        self.assertTrue(main.is_pdf_bytes(b"\xef\xbb\xbf" + b"x" * 100 + b"%PDF-1.4\n"))

    def test_magic_beyond_1024_is_rejected(self):
        self.assertFalse(main.is_pdf_bytes(b"j" * 1200 + b"%PDF-1.4"))

    def test_html_rejected(self):
        self.assertFalse(main.is_pdf_bytes(b"<!DOCTYPE html><html><body>404</body></html>"))

    def test_zip_rejected(self):
        self.assertFalse(main.is_pdf_bytes(b"PK\x03\x04" + b"\x00" * 50))

    def test_empty_rejected(self):
        self.assertFalse(main.is_pdf_bytes(b""))

    def test_describe_invalid_names_culprits(self):
        self.assertIn("ZIP", main.describe_invalid(b"PK\x03\x04", ""))
        self.assertIn("HTML", main.describe_invalid(b"<!DOCTYPE html>", "text/html"))
        self.assertIn("content type", main.describe_invalid(b"zzzz", "application/octet-stream"))


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

class TestSanitizeFilename(unittest.TestCase):
    def test_invalid_windows_characters_removed(self):
        self.assertEqual(main.sanitize_filename('a<b>c:d"e|f?g*h'), "abcdefgh")

    def test_path_basename_kept_only(self):
        self.assertEqual(main.sanitize_filename("C:\\Users\\someone\\book?.pdf"), "book.pdf")
        self.assertEqual(main.sanitize_filename("a/b/c\\d.txt"), "d.txt")

    def test_path_traversal_stripped(self):
        self.assertEqual(main.sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(main.sanitize_filename("..\\..\\Windows\\System32\\evil"), "evil")

    def test_url_encoded_traversal_stripped(self):
        self.assertEqual(main.sanitize_filename("%2e%2e%2fsecret"), "secret")

    def test_trailing_dots_and_spaces_stripped(self):
        self.assertEqual(main.sanitize_filename("My Book... "), "My Book")

    def test_empty_and_dot_only_fall_back_to_default(self):
        self.assertEqual(main.sanitize_filename(""), "book")
        self.assertEqual(main.sanitize_filename("..."), "book")

    def test_windows_reserved_name_prefixed(self):
        self.assertEqual(main.sanitize_filename("CON"), "_CON")
        self.assertEqual(main.sanitize_filename("com1.pdf"), "_com1.pdf")

    def test_long_names_truncated(self):
        name = main.sanitize_filename("A" * 500)
        self.assertLessEqual(len(name), 120)

    def test_unicode_preserved(self):
        self.assertEqual(main.sanitize_filename("الهند - تاريخ"), "الهند - تاريخ")

    def test_unique_path_avoids_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "Book.pdf").write_bytes(b"x")
            p1 = main.unique_path(d, "Book")
            self.assertEqual(p1.name, "Book (1).pdf")
            p1.write_bytes(b"x")
            p2 = main.unique_path(d, "Book")
            self.assertEqual(p2.name, "Book (2).pdf")


# ---------------------------------------------------------------------------
# Candidate-selection input parsing
# ---------------------------------------------------------------------------

class TestParseSelection(unittest.TestCase):
    def test_enter_means_next_book(self):
        self.assertEqual(main.parse_selection("", 3), "next")
        self.assertEqual(main.parse_selection("   ", 3), "next")

    def test_valid_index(self):
        self.assertEqual(main.parse_selection("1", 3), 1)
        self.assertEqual(main.parse_selection("3", 3), 3)

    def test_out_of_range(self):
        self.assertEqual(main.parse_selection("0", 3), "invalid")
        self.assertEqual(main.parse_selection("4", 3), "invalid")
        self.assertEqual(main.parse_selection("99", 3), "invalid")

    def test_non_numeric(self):
        self.assertEqual(main.parse_selection("abc", 3), "invalid")
        self.assertEqual(main.parse_selection("-1", 3), "invalid")
        self.assertEqual(main.parse_selection("1.5", 3), "invalid")


# ---------------------------------------------------------------------------
# Rule-based result ranking
# ---------------------------------------------------------------------------

def result(url, title="", body=""):
    return {"url": url, "title": title, "body": body}


class TestRankResults(unittest.TestCase):
    QUERY = "pride and prejudice austen"

    def test_trusted_site_beats_unknown_site(self):
        out = main.rank_results(self.QUERY, [
            result("https://random-blog.example/post", "Pride and Prejudice"),
            result("https://archive.org/details/pp", "Pride and Prejudice"),
        ])
        self.assertEqual(out[0], "https://archive.org/details/pp")

    def test_shops_and_social_media_are_dropped(self):
        out = main.rank_results(self.QUERY, [
            result("https://www.amazon.com/dp/123", "Pride and Prejudice"),
            result("https://www.goodreads.com/book/1", "Pride and Prejudice"),
            result("https://en.wikipedia.org/wiki/Pride_and_Prejudice"),
            result("https://example.edu/pp.pdf", "Pride and Prejudice"),
        ])
        self.assertEqual(out, ["https://example.edu/pp.pdf"])

    def test_direct_pdf_and_title_match_rank_higher(self):
        out = main.rank_results(self.QUERY, [
            result("https://site-a.example/page", "Unrelated cooking book"),
            result("https://site-b.example/austen/pride.pdf", "Pride and Prejudice"),
        ])
        self.assertEqual(out[0], "https://site-b.example/austen/pride.pdf")

    def test_ties_keep_search_order_and_limit(self):
        results = [result(f"https://s{i}.example/x") for i in range(20)]
        out = main.rank_results(self.QUERY, results)
        self.assertEqual(out, [r["url"] for r in results[:main.MAX_RESULT_URLS]])

    def test_empty(self):
        self.assertEqual(main.rank_results(self.QUERY, []), [])


# ---------------------------------------------------------------------------
# Streaming download validation (mocked HTTP)
# ---------------------------------------------------------------------------

class TestDownloadPdf(unittest.TestCase):
    PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"

    def _download(self, response, url="https://example.com/files/book.pdf"):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        with mock.patch("requests.get", return_value=response):
            ok, msg = main.download_pdf(url, dest_dir=Path(tmp))
        files = sorted(p.name for p in Path(tmp).iterdir())
        return ok, msg, files, Path(tmp)

    def test_valid_pdf_is_saved(self):
        resp = FakeResponse(self.PDF_BYTES,
                            headers={"Content-Type": "application/pdf"},
                            url="https://example.com/files/book.pdf")
        ok, msg, files, dest = self._download(resp)
        self.assertTrue(ok, msg)
        self.assertTrue(msg.endswith("book.pdf"))
        self.assertEqual(files, ["book.pdf"])
        self.assertTrue((dest / "book.pdf").read_bytes().startswith(b"%PDF-"))

    def test_html_response_is_rejected(self):
        resp = FakeResponse(b"<!DOCTYPE html><html>Access denied</html>",
                            headers={"Content-Type": "text/html"},
                            url="https://example.com/paywall")
        ok, msg, files, _ = self._download(resp)
        self.assertFalse(ok)
        self.assertIn("HTML", msg)
        self.assertEqual(files, [])  # no partial file left behind

    def test_zip_response_is_rejected(self):
        resp = FakeResponse(b"PK\x03\x04" + b"\x00" * 200,
                            headers={"Content-Type": "application/zip"},
                            url="https://example.com/book.zip")
        ok, msg, files, _ = self._download(resp)
        self.assertFalse(ok)
        self.assertIn("ZIP", msg)
        self.assertEqual(files, [])

    def test_http_error_is_reported(self):
        resp = FakeResponse(b"not found", status=404,
                            headers={"Content-Type": "text/plain"})
        ok, msg, files, _ = self._download(resp)
        self.assertFalse(ok)
        self.assertIn("Download failed", msg)
        self.assertEqual(files, [])

    def test_duplicate_filename_not_overwritten(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / "book.pdf").write_bytes(b"%PDF-1.4\nexisting\n")
        resp = FakeResponse(self.PDF_BYTES,
                            headers={"Content-Type": "application/pdf"},
                            url="https://example.com/book.pdf")
        with mock.patch("requests.get", return_value=resp):
            ok, msg = main.download_pdf("https://example.com/book.pdf", dest_dir=tmp)
        self.assertTrue(ok, msg)
        self.assertTrue(msg.endswith("book (1).pdf"))
        self.assertEqual((tmp / "book.pdf").read_bytes(),
                         b"%PDF-1.4\nexisting\n")  # untouched
        self.assertEqual((tmp / "book (1).pdf").read_bytes(), self.PDF_BYTES)


# ---------------------------------------------------------------------------
# PDF discovery helpers
# ---------------------------------------------------------------------------

class TestDiscoveryHelpers(unittest.TestCase):
    def test_looks_like_pdf_url_with_query_string(self):
        self.assertTrue(main.looks_like_pdf_url("https://x.example/a/b.PDF?dl=1&v=2"))
        self.assertFalse(main.looks_like_pdf_url("https://x.example/a/b.pdf.html"))
        self.assertFalse(main.looks_like_pdf_url("https://x.example/books"))

    def test_ignored_archive_urls(self):
        self.assertTrue(main.is_ignored_download("https://x.example/book.zip"))
        self.assertTrue(main.is_ignored_download("https://x.example/book.epub"))
        self.assertFalse(main.is_ignored_download("https://x.example/book.pdf"))


if __name__ == "__main__":
    unittest.main()
