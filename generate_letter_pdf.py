import os
import sys
from pathlib import Path

import psycopg2
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - script can still run if dotenv is unavailable
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent
APP_ENV_PATH = PROJECT_ROOT / "app" / ".env"
if load_dotenv and APP_ENV_PATH.exists():
    load_dotenv(APP_ENV_PATH)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "creditrepair")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "letters"))


def get_connection():
    if not DB_PASSWORD:
        raise SystemExit("DB_PASSWORD is not configured. Add it to app/.env or the environment before running this script.")
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

def make_pdf(letter_text: str, out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    c = canvas.Canvas(out_path, pagesize=LETTER)
    width, height = LETTER

    left = 0.85 * inch
    top = height - 0.85 * inch
    line_height = 12

    y = top
    for raw_line in letter_text.splitlines():
        line = raw_line.rstrip("\n")

        while len(line) > 110:
            c.drawString(left, y, line[:110])
            line = line[110:]
            y -= line_height
            if y < 0.85 * inch:
                c.showPage()
                y = top

        c.drawString(left, y, line)
        y -= line_height
        if y < 0.85 * inch:
            c.showPage()
            y = top

    c.save()

def main(letter_id: str):
    conn = get_connection()
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("""
            SELECT l.letter_text,
                   l.subject,
                   l.bureau::text,
                   l.generated_at,
                   c.first_name,
                   c.last_name
            FROM letters l
            JOIN clients c ON c.id = l.client_id
            WHERE l.id = %s
        """, (letter_id,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"Letter not found: {letter_id}")

        letter_text, subject, bureau, generated_at, first_name, last_name = row

        safe_name = f"{last_name}_{first_name}_{bureau}_{generated_at:%Y%m%d_%H%M%S}".replace(" ", "_")
        file_name = f"{safe_name}.pdf"
        out_path = os.path.join(OUTPUT_DIR, file_name)

        make_pdf(letter_text, out_path)

        try:
            cur.execute(
                "SELECT attach_letter_pdf(%s, %s, %s, %s)",
                (letter_id, file_name, out_path, subject)
            )
            print("OK (attached)")
        except psycopg2.errors.UniqueViolation:
            print("OK (already attached)")

        print("PDF:", out_path)

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python generate_letter_pdf.py <letter_id>")
    main(sys.argv[1])