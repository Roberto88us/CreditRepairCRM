from pathlib import Path

from generate_letter_pdf import make_pdf


def test_make_pdf_writes_valid_pdf(tmp_path: Path) -> None:
    out_path = tmp_path / "sample_letter.pdf"
    letter_text = "Client Name\nAccount 1234\nDispute this item.\n"

    make_pdf(letter_text, str(out_path))

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert out_path.read_bytes().startswith(b"%PDF")


def test_make_pdf_creates_parent_directories(tmp_path: Path) -> None:
    out_path = tmp_path / "nested" / "folder" / "letter.pdf"

    make_pdf("Line 1\nLine 2\n", str(out_path))

    assert out_path.exists()
