import os
import sys
import json
import psycopg2
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "creditrepair"
DB_USER = "postgres"
DB_PASSWORD = "CreditRepair@1974!"

SECRET_KEY = "CreditRepairDevKey_2026!"
SENDER_NAME = "Clean Slate Consulting"
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

def fetch_letter_and_make_pdf(cur, letter_id: str):
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
        raise RuntimeError(f"Letter not found: {letter_id}")

    letter_text, subject, bureau, generated_at, first_name, last_name = row

    safe_name = f"{last_name}_{first_name}_{bureau}_{generated_at:%Y%m%d_%H%M%S}".replace(" ", "_")
    file_name = f"{safe_name}.pdf"
    out_path = os.path.join(OUTPUT_DIR, file_name)

    make_pdf(letter_text, out_path)

    try:
        cur.execute("SELECT attach_letter_pdf(%s, %s, %s, %s)",
                    (letter_id, file_name, out_path, subject))
        attach_status = "attached"
    except psycopg2.errors.UniqueViolation:
        attach_status = "already_attached"

    return out_path, attach_status

def main(client_id: str, round_number: int, include_personal_info: bool, client_email: str):
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = True

    with conn.cursor() as cur:
        # Run the RUN-AWARE round function (returns round_run_id)
        cur.execute("""
            SELECT process_dispute_round_run_json(%s, %s, %s, %s, %s, %s)::text
        """, (
            client_id,
            round_number,
            include_personal_info,
            SECRET_KEY,
            SENDER_NAME,
            client_email
        ))
        result_text = cur.fetchone()[0]
        result = json.loads(result_text)

        print("Round processed:", result["round_number"])
        print("Round run ID:", result["round_run_id"])
        print("Update email ID:", result["email_id"])
        print("-----")

        for item in result["letters"]:
            bureau = item["bureau"]
            letter_id = item["letter_id"]
            pdf_path, attach_status = fetch_letter_and_make_pdf(cur, letter_id)
            print(f"{bureau}: {letter_id} ({attach_status})")
            print(f"PDF: {pdf_path}")
            print("-----")

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: python process_round_run_and_generate_pdfs.py <client_id> <round_number> <include_personal_info:true|false> <client_email>"
        )

    client_id = sys.argv[1]
    round_number = int(sys.argv[2])
    include_personal_info = sys.argv[3].strip().lower() == "true"
    client_email = sys.argv[4]

    main(client_id, round_number, include_personal_info, client_email)