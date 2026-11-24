"""Pure-Python PDF text extraction for uploaded master CVs.

``pypdf`` reads digital PDFs without native dependencies. A scanned image PDF
has no text layer, so it is rejected with guidance instead of silently sending
an empty document to the AI provider.
"""

from __future__ import annotations

import io
import re
from unicodedata import normalize

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MIN_EXTRACTED_CHARACTERS = 50

_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}
_LIGATURE_PATTERN = re.compile("|".join(re.escape(key) for key in _LIGATURES))
_HYPHEN_LINEBREAK = re.compile(r"(\w+)-\n(\w+)")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


class PDFExtractionError(Exception):
    """A PDF the application cannot turn into usable text."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def sanitize_extracted_text(text: str) -> str:
    """Normalize extraction artifacts that hurt prompt quality.

    Ligatures become plain letters, words hyphenated across a line break are
    rejoined, and runs of blank lines collapse. Nothing is summarized or
    rewritten: the user's own wording must reach the model unchanged.
    """

    normalized = normalize("NFKC", text)
    normalized = _LIGATURE_PATTERN.sub(lambda match: _LIGATURES[match.group(0)], normalized)
    normalized = normalized.replace("\xa0", " ")
    normalized = _HYPHEN_LINEBREAK.sub(r"\1\2", normalized)
    normalized = _EXCESS_NEWLINES.sub("\n\n", normalized)
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract cleaned text from a digital PDF, or raise :class:`PDFExtractionError`."""

    if not file_bytes:
        raise PDFExtractionError("empty_file", "The uploaded PDF is empty.")

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except (PdfReadError, ValueError) as error:
        raise PDFExtractionError(
            "invalid_pdf",
            "The file is not a readable PDF. Export it again as a digital PDF and retry.",
        ) from error

    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
        except Exception as error:  # pypdf raises provider-specific errors here.
            raise PDFExtractionError(
                "encrypted_pdf",
                "The PDF is password protected. Upload an unencrypted copy.",
            ) from error
        if not decrypted:
            raise PDFExtractionError(
                "encrypted_pdf",
                "The PDF is password protected. Upload an unencrypted copy.",
            )

    if len(reader.pages) == 0:
        raise PDFExtractionError("empty_pdf", "The uploaded PDF has no pages.")

    try:
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as error:  # A damaged content stream must not become a 500.
        raise PDFExtractionError(
            "unreadable_pdf",
            "The PDF text could not be read. Export it again and retry.",
        ) from error

    extracted = sanitize_extracted_text("\n\n".join(pages))
    if len(extracted) < MIN_EXTRACTED_CHARACTERS:
        raise PDFExtractionError(
            "no_text_layer",
            "No selectable text was found in this PDF. It may be a scan. "
            "Upload a digital PDF or paste the CV text instead.",
        )
    return extracted


def normalize_pasted_text(text: str) -> str:
    """Apply the same normalization to pasted CV text."""

    extracted = sanitize_extracted_text(text)
    if len(extracted) < MIN_EXTRACTED_CHARACTERS:
        raise PDFExtractionError(
            "text_too_short",
            "The pasted CV text is too short to parse. Paste the full CV text.",
        )
    return extracted
