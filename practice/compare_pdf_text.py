"""Compare two PDF libraries on YOUR CV: which one glues or splits fewer words?

    uv run --with pdfplumber python -m practice.compare_pdf_text /path/to/your_cv.pdf

Tip: type the command up to the space after compare_pdf_text, then drag your CV file
from Finder into the Terminal window to paste its path.

Runs only on your Mac. It prints counts and a few SHORT examples, never the whole CV.
Nothing is saved.

Result on 25 Oct (one real CV): pypdf 64 glued words, pdfplumber default 72, pdfplumber
x_tolerance=1.5: 0 glued and 0 split. So api/parse.py now uses pdfplumber at 1.5.
"""

import io
import re
import sys
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from api.parse import tidy

# Glued: a space is probably missing, e.g. "Built6", "6+FastAPI", "Intern|", "Kingdom•Python".
# Some hits are real names (EC2, S3, F1, an email address). Those appear for every library,
# so the comparison is still fair.
GLUED = re.compile(r"[A-Za-z][0-9]|[0-9%+][A-Za-z]|\S[|•]|[|•]\S")
WORD = re.compile(r"[A-Za-z]+")


def show(text: str, spots: list[tuple[int, int]]) -> None:
    for start, end in spots[:5]:
        print(f"{'':<26}...{text[max(start - 8, 0):end + 8]!r}...")


def report(name: str, text: str, pypdf_words: set[str]) -> None:
    glued = [m.span() for m in GLUED.finditer(text)]
    # Split: the opposite mistake, e.g. "Bu ilt". A short word (3 letters or fewer) that
    # never appears in pypdf's text is probably a piece of a split word.
    split = [m.span() for m in WORD.finditer(text)
             if len(m.group()) <= 3 and m.group() not in pypdf_words]
    print(f"{name:<24} {len(text):>6} characters, {len(glued):>3} glued, {len(split):>3} split?")
    if split:
        print(f"{'':<26}possible splits (check by eye; some are just words pypdf had glued):")
        show(text, split)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    data = Path(sys.argv[1]).expanduser().read_bytes()

    reader = PdfReader(io.BytesIO(data))
    baseline = tidy("\n".join(page.extract_text() or "" for page in reader.pages))
    pypdf_words = set(WORD.findall(baseline))
    report("pypdf", baseline, pypdf_words)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for tolerance in (3, 1.5):
            text = "\n".join(page.extract_text(x_tolerance=tolerance) or "" for page in pdf.pages)
            report(f"pdfplumber x_tol={tolerance}", tidy(text), pypdf_words)
    return 0


if __name__ == "__main__":
    sys.exit(main())
