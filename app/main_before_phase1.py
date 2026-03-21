import os
import json
import base64
import hashlib
import psycopg2
from fastapi import FastAPI, HTTPException, Request, Form
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "creditrepair")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

SECRET_KEY = os.getenv("SECRET_KEY")
SENDER_NAME = os.getenv("SENDER_NAME", "Clean Slate Consulting")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", r"C:\CreditRepairCRM\letters")

app = FastAPI(title="Credit Repair CRM API")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------
# DB + crypto helpers
# ---------------------------

def get_conn():
    if not DB_PASSWORD:
        raise RuntimeError("DB_PASSWORD is missing. Check your .env file.")
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def get_fernet() -> Fernet:
    """
    Derive a stable Fernet key from SECRET_KEY.
    Fernet key must be urlsafe base64-encoded 32 bytes.
    """
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is missing. Check your .env file.")
    raw = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()  # 32 bytes
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


_F = None


def fernet() -> Fernet:
    global _F
    if _F is None:
        _F = get_fernet()
    return _F


def enc_text(value: str | None) -> bytes | None:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    return fernet().encrypt(v.encode("utf-8"))


def dec_text(value: bytes | None) -> str:
    if value is None:
        return ""
    try:
        return fernet().decrypt(value).decode("utf-8")
    except InvalidToken:
        # If something was encrypted with an older scheme or corrupted
        return ""


# ---------------------------
# PDF generation
# ---------------------------

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


def generate_and_attach_pdf(cur, letter_id: str):
    cur.execute("""
        SELECT l.letter_text,
               l.subject,
               l.bureau::text,
               l.generated_at,
               l.client_id,
               c.first_name,
               c.last_name
        FROM letters l
        JOIN clients c ON c.id = l.client_id
        WHERE l.id = %s
    """, (letter_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Letter not found: {letter_id}")

    letter_text, subject, bureau, generated_at, client_id, first_name, last_name = row

    safe_name = f"{last_name}_{first_name}_{bureau}_{generated_at:%Y%m%d_%H%M%S}".replace(" ", "_")
    file_name = f"{safe_name}.pdf"
    out_path = os.path.join(OUTPUT_DIR, file_name)

    make_pdf(letter_text, out_path)

    try:
        cur.execute("SELECT attach_letter_pdf(%s, %s, %s, %s)",
                    (letter_id, file_name, out_path, subject))
        status = "attached"
    except psycopg2.errors.UniqueViolation:
        status = "already_attached"

    return {
        "letter_id": letter_id,
        "client_id": str(client_id),
        "bureau": bureau,
        "file_name": file_name,
        "file_path": out_path,
        "status": status
    }


# ---------------------------
# Scores grouped history
# ---------------------------

def fetch_score_history_grouped(cur, client_id: str, limit_each: int = 50):
    def q(where_sql: str, params: tuple):
        cur.execute(f"""
            SELECT id::text,
                   report_date,
                   bureau::text,
                   COALESCE(source,'') AS source,
                   COALESCE(model,'') AS model,
                   score,
                   COALESCE(notes,'') AS notes,
                   entered_at
            FROM credit_report_snapshots
            WHERE client_id = %s
              AND ({where_sql})
            ORDER BY report_date DESC, entered_at DESC
            LIMIT %s
        """, (client_id, *params, limit_each))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "report_date": str(r[1]),
                "bureau": r[2],
                "source": r[3],
                "model": r[4],
                "score": r[5],
                "notes": r[6],
                "entered_at": str(r[7]) if r[7] else ""
            }
            for r in rows
        ]

    return {
        "experian_site": q("source = %s AND bureau = 'experian'::bureau_type", ("experian_site",)),
        "transunion_site": q("source = %s AND bureau = 'transunion'::bureau_type", ("transunion_site",)),
        "equifax_site": q("source = %s AND bureau = 'equifax'::bureau_type", ("equifax_site",)),
        "creditkarma": q("source = %s", ("creditkarma",)),
        "myfico_free": q("source = %s", ("myfico_free",)),
        "manual": q("source = %s", ("manual",)),
    }


# ---------------------------
# Credentials (client portals)
# ---------------------------

PROVIDERS = ["creditkarma", "transunion_site", "equifax_site", "experian_site", "myfico"]


def fetch_credentials(cur, client_id: str, reveal_id: str | None = None):
    cur.execute("""
        SELECT id::text,
               provider,
               username_encrypted,
               password_encrypted,
               pin_encrypted,
               security_question_encrypted,
               security_answer_encrypted,
               COALESCE(note,'') AS note,
               updated_at
        FROM client_credentials
        WHERE client_id = %s
        ORDER BY provider, updated_at DESC
    """, (client_id,))
    rows = cur.fetchall()

    creds = []
    for r in rows:
        cid, provider, u_enc, p_enc, pin_enc, q_enc, a_enc, note, updated_at = r
        username = dec_text(u_enc)
        item = {
            "id": cid,
            "provider": provider,
            "username": username,
            "note": note,
            "updated_at": str(updated_at) if updated_at else "",
            "revealed": (reveal_id == cid),
            "password": "",
            "pin": "",
            "security_question": "",
            "security_answer": "",
        }
        if reveal_id == cid:
            item["password"] = dec_text(p_enc)
            item["pin"] = dec_text(pin_enc)
            item["security_question"] = dec_text(q_enc)
            item["security_answer"] = dec_text(a_enc)
        creds.append(item)

    return creds


def load_client_context(cur, client_id: str, reveal_cred_id: str | None = None):
    # dashboard
    cur.execute("""
        SELECT row_to_json(t)
        FROM (
          SELECT *
          FROM v_client_dashboard
          WHERE client_id = %s
        ) t
    """, (client_id,))
    row = cur.fetchone()
    dashboard = row[0] if row and row[0] else {}

    # groups
    score_groups = fetch_score_history_grouped(cur, client_id, 50)

    # credentials
    credentials = fetch_credentials(cur, client_id, reveal_cred_id)

    return dashboard, score_groups, credentials


# ---------------------------
# API models
# ---------------------------

class ProcessRoundRequest(BaseModel):
    client_id: str
    round_number: int
    include_personal_info: bool = True
    client_email: str


# ---------------------------
# API endpoints
# ---------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/clients/{client_id}/dashboard")
def client_dashboard(client_id: str):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT row_to_json(t)
                FROM (
                  SELECT *
                  FROM v_client_dashboard
                  WHERE client_id = %s
                ) t
            """, (client_id,))
            row = cur.fetchone()
        conn.close()

        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="Client not found")

        return row[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process-round")
def process_round(req: ProcessRoundRequest):
    try:
        if not SECRET_KEY:
            raise RuntimeError("SECRET_KEY is missing. Check your .env file.")

        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT process_dispute_round_run_json(%s,%s,%s,%s,%s,%s)::text",
                (req.client_id, req.round_number, req.include_personal_info, SECRET_KEY, SENDER_NAME, req.client_email)
            )
            result_text = cur.fetchone()[0]
        conn.close()
        return json.loads(result_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/letters/{letter_id}/pdf")
def letter_pdf(letter_id: str):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            result = generate_and_attach_pdf(cur, letter_id)
        conn.close()
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/round-runs/{round_run_id}/pdfs")
def round_run_pdfs(round_run_id: str):
    try:
        conn = get_conn()
        conn.autocommit = True
        results = []
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM letters WHERE round_run_id = %s ORDER BY bureau::text", (round_run_id,))
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                raise HTTPException(status_code=404, detail=f"No letters found for round_run_id: {round_run_id}")
            for lid in ids:
                results.append(generate_and_attach_pdf(cur, lid))
        conn.close()
        return {"round_run_id": round_run_id, "count": len(results), "results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process-round-with-pdfs")
def process_round_with_pdfs(req: ProcessRoundRequest):
    try:
        if not SECRET_KEY:
            raise RuntimeError("SECRET_KEY is missing. Check your .env file.")

        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT process_dispute_round_run_json(%s,%s,%s,%s,%s,%s)::text",
                (req.client_id, req.round_number, req.include_personal_info, SECRET_KEY, SENDER_NAME, req.client_email)
            )
            round_json = json.loads(cur.fetchone()[0])
            run_id = round_json.get("round_run_id")
            if not run_id:
                raise RuntimeError("No round_run_id returned")

            cur.execute("SELECT id::text FROM letters WHERE round_run_id = %s ORDER BY bureau::text", (run_id,))
            ids = [r[0] for r in cur.fetchall()]
            pdf_results = [generate_and_attach_pdf(cur, lid) for lid in ids]

        conn.close()
        return {"round": round_json, "pdfs": {"round_run_id": run_id, "count": len(pdf_results), "results": pdf_results}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# UI routes
# ---------------------------

@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/ui/search", response_class=HTMLResponse)
def ui_search(request: Request, q: str = ""):
    q = (q or "").strip()
    if not q:
        return templates.TemplateResponse("index.html", {"request": request, "error": "Enter a search term."})

    like = f"%{q}%"
    results = []

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT id::text, first_name, last_name, phone, email, status::text
                    FROM clients
                    WHERE COALESCE(first_name,'') ILIKE %s
                       OR COALESCE(last_name,'') ILIKE %s
                       OR COALESCE(phone,'') ILIKE %s
                       OR COALESCE(email,'') ILIKE %s
                       OR COALESCE(status::text,'') ILIKE %s
                    ORDER BY last_name NULLS LAST, first_name NULLS LAST
                    LIMIT 50
                """, (like, like, like, like, like))
                for r in cur.fetchall():
                    results.append({
                        "id": r[0],
                        "first_name": r[1],
                        "last_name": r[2],
                        "phone": r[3],
                        "email": r[4],
                        "status": r[5],
                    })
            except psycopg2.errors.UndefinedColumn:
                conn.rollback()
                cur.execute("""
                    SELECT id::text, first_name, last_name, phone, status::text
                    FROM clients
                    WHERE COALESCE(first_name,'') ILIKE %s
                       OR COALESCE(last_name,'') ILIKE %s
                       OR COALESCE(phone,'') ILIKE %s
                       OR COALESCE(status::text,'') ILIKE %s
                    ORDER BY last_name NULLS LAST, first_name NULLS LAST
                    LIMIT 50
                """, (like, like, like, like))
                for r in cur.fetchall():
                    results.append({
                        "id": r[0],
                        "first_name": r[1],
                        "last_name": r[2],
                        "phone": r[3],
                        "email": None,
                        "status": r[4],
                    })
        conn.close()

        return templates.TemplateResponse(
            "index.html",
            {"request": request, "q": q, "results": results, "results_count": len(results)}
        )
    except Exception as e:
        return templates.TemplateResponse("index.html", {"request": request, "error": str(e)})


@app.post("/ui/client", response_class=HTMLResponse)
def ui_client(request: Request, client_id: str = Form(...)):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)
        conn.close()

        if not dashboard:
            return templates.TemplateResponse("index.html", {"request": request, "error": "Client not found."})

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS
            }
        )
    except Exception as e:
        return templates.TemplateResponse("index.html", {"request": request, "error": str(e)})


@app.post("/ui/reveal-credential", response_class=HTMLResponse)
def ui_reveal_credential(request: Request, client_id: str = Form(...), credential_id: str = Form(...)):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            dashboard, score_groups, credentials = load_client_context(cur, client_id, credential_id)
        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "Credential revealed (this page only)."
            }
        )
    except Exception as e:
        return templates.TemplateResponse("client.html", {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)})


@app.post("/ui/save-credential", response_class=HTMLResponse)
def ui_save_credential(
    request: Request,
    client_id: str = Form(...),
    provider: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    pin: str = Form(""),
    security_question: str = Form(""),
    security_answer: str = Form(""),
    note: str = Form(""),
):
    try:
        if provider not in PROVIDERS:
            raise RuntimeError("Invalid provider")

        u_enc = enc_text(username)
        p_enc = enc_text(password)
        pin_enc = enc_text(pin)
        q_enc = enc_text(security_question)
        a_enc = enc_text(security_answer)

        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            # Upsert by (client_id, provider)
            cur.execute("SELECT id FROM client_credentials WHERE client_id = %s AND provider = %s", (client_id, provider))
            row = cur.fetchone()

            if row:
                cur.execute("""
                    UPDATE client_credentials
                    SET username_encrypted = COALESCE(%s, username_encrypted),
                        password_encrypted = COALESCE(%s, password_encrypted),
                        pin_encrypted = COALESCE(%s, pin_encrypted),
                        security_question_encrypted = COALESCE(%s, security_question_encrypted),
                        security_answer_encrypted = COALESCE(%s, security_answer_encrypted),
                        note = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s AND provider = %s
                """, (u_enc, p_enc, pin_enc, q_enc, a_enc, note, client_id, provider))
            else:
                cur.execute("""
                    INSERT INTO client_credentials
                      (client_id, provider, username_encrypted, password_encrypted,
                       pin_encrypted, security_question_encrypted, security_answer_encrypted,
                       note, updated_at)
                    VALUES
                      (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                """, (client_id, provider, u_enc, p_enc, pin_enc, q_enc, a_enc, note))

            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)

        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "Credential saved."
            }
        )

    except Exception as e:
        return templates.TemplateResponse("client.html", {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)})


@app.post("/ui/delete-credential", response_class=HTMLResponse)
def ui_delete_credential(request: Request, client_id: str = Form(...), credential_id: str = Form(...)):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_credentials WHERE id = %s AND client_id = %s", (credential_id, client_id))
            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)
        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "Credential deleted."
            }
        )
    except Exception as e:
        return templates.TemplateResponse("client.html", {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)})


@app.post("/ui/process-round-with-pdfs", response_class=HTMLResponse)
def ui_process_round_with_pdfs(
    request: Request,
    client_id: str = Form(...),
    round_number: int = Form(...),
    client_email: str = Form(...),
    include_personal_info: bool = Form(False),
):
    try:
        if not SECRET_KEY:
            raise RuntimeError("SECRET_KEY missing. Check .env")

        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT process_dispute_round_run_json(%s,%s,%s,%s,%s,%s)::text",
                (client_id, round_number, include_personal_info, SECRET_KEY, SENDER_NAME, client_email)
            )
            round_json = json.loads(cur.fetchone()[0])
            run_id = round_json.get("round_run_id")
            if not run_id:
                raise RuntimeError("No round_run_id returned")

            cur.execute("SELECT id::text FROM letters WHERE round_run_id = %s ORDER BY bureau::text", (run_id,))
            ids = [r[0] for r in cur.fetchall()]
            pdf_results = [generate_and_attach_pdf(cur, lid) for lid in ids]

        conn.close()

        return templates.TemplateResponse(
            "run_result.html",
            {"request": request, "client_id": client_id, "round_number": round_number, "round": round_json, "pdfs": pdf_results}
        )

    except Exception as e:
        return templates.TemplateResponse("client.html", {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)})


@app.post("/ui/add-score", response_class=HTMLResponse)
def ui_add_score(
    request: Request,
    client_id: str = Form(...),
    report_date: str = Form(...),
    bureau: str = Form(...),
    source: str = Form(...),
    model: str = Form(...),
    score: int = Form(...),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO credit_report_snapshots
                  (client_id, report_date, bureau, score, model, source)
                VALUES
                  (%s, %s, %s, %s, %s, %s)
            """, (client_id, report_date, bureau, score, model, source))

            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)

        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "Score saved."
            }
        )

    except Exception as e:
        return templates.TemplateResponse(
            "client.html",
            {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)}
        )


@app.post("/ui/add-myfico", response_class=HTMLResponse)
def ui_add_myfico(
    request: Request,
    client_id: str = Form(...),
    report_date: str = Form(...),
    experian_score: int = Form(...),
    transunion_score: int = Form(...),
    equifax_score: int = Form(...),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            rows = [
                ("experian", experian_score),
                ("transunion", transunion_score),
                ("equifax", equifax_score),
            ]
            for bureau, sc in rows:
                cur.execute("""
                    INSERT INTO credit_report_snapshots
                      (client_id, report_date, bureau, source, model, score)
                    VALUES
                      (%s, %s, %s, %s, %s, %s)
                """, (client_id, report_date, bureau, "myfico_free", "FICO 8", sc))

            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)

        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "MyFICO scores saved (EX/TU/EQ)."
            }
        )

    except Exception as e:
        return templates.TemplateResponse(
            "client.html",
            {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)}
        )


@app.post("/ui/delete-score", response_class=HTMLResponse)
def ui_delete_score(request: Request, client_id: str = Form(...), snapshot_id: str = Form(...)):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM credit_report_snapshots WHERE id = %s AND client_id = %s",
                        (snapshot_id, client_id))
            dashboard, score_groups, credentials = load_client_context(cur, client_id, None)
        conn.close()

        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "score_groups": score_groups,
                "credentials": credentials,
                "providers": PROVIDERS,
                "message": "Score entry deleted."
            }
        )

    except Exception as e:
        return templates.TemplateResponse(
            "client.html",
            {"request": request, "client_id": client_id, "dashboard": {}, "error": str(e)}
        )