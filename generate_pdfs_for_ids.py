import os
import sys
import psycopg2
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "creditrepair"
DB_USER = "postgres"
DB_PASSWORD = "CreditRepair@1974!"

OUTPUT_DIR = r"C:\CreditRepairCRM\letters"

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

def generate_pdf_for_letter(cur, letter_id: str):
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
        print(f"SKIP (not found): {letter_id}")
        return

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
        print("OK (attached):", bureau, letter_id)
    except psycopg2.errors.UniqueViolation:
        print("OK (already attached):", bureau, letter_id)

    print("PDF:", out_path)

def main(letter_ids):
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = True

    with conn.cursor() as cur:
        for lid in letter_ids:
            generate_pdf_for_letter(cur, lid)

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python generate_pdfs_for_ids.py <letter_id1> <letter_id2> <letter_id3> ...")
    main(sys.argv[1:])