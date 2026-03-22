
import os
import json
import base64
import hashlib
from typing import Optional
from uuid import uuid4

import psycopg2
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
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
UPLOAD_DIR = os.getenv("UPLOAD_DIR", r"C:\CreditRepairCRM\uploads")

app = FastAPI(title="Credit Repair CRM API")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

PROVIDERS = ["creditkarma", "equifax_site", "experian_site", "myfico", "transunion_site"]
PHONE_TYPES = ["cell", "home", "other", "work"]
SUFFIXES = ["", "II", "III", "IV", "Jr", "Sr"]
STATUSES = [
    "candidate",
    "cancelled",
    "completed",
    "consultation only",
    "contract sent",
    "current client",
    "inactive",
    "pending payment",
    "referred out",
]
PAYMENT_METHODS = ["card", "cash", "zelle"]
BILLING_GROUPS = ["1st", "15th", "custom"]
PRICING_PLANS = [
    "consultation only",
    "couple/family discount",
    "intensive 6-month program",
    "standard program",
]
CONSULTATION_FEES = [75, 100, 150]
APPOINTMENT_MODES = ["in_person", "phone", "virtual"]
BUREAUS = ["equifax", "experian", "transunion"]
DISPUTE_REASONS = ["never late", "not included in bankruptcy", "not mine", "other"]
NEGATIVE_ITEM_STATUSES = ["corrected", "disputed", "open", "removed", "verified"]
PERSONAL_ERROR_TYPES = ["address", "associated person", "employer", "name", "other", "phone", "ssn variation"]
PERSONAL_ERROR_STATUSES = ["corrected", "disputed", "open", "removed", "verified"]
CREDIT_TYPES = ["auto loan", "car loan", "credit card", "other", "personal loan", "secured credit card", "secured loan", "store card"]
OUTBOUND_REFERRAL_STATUSES = ["completed", "contacted", "declined", "referred"]
REFERRAL_PARTNER_TYPES = ["accountant", "ADP", "banker", "bankruptcy attorney", "client", "estate attorney", "insurance agent", "lender", "mortgage lender", "other", "realtor", "title company"]
FOLLOWUP_TYPES = ["call back", "document reminder", "general", "payment follow-up", "redispute", "review bureau response", "send docs", "waiting on IDs"]
FOLLOWUP_STATUSES = ["done", "open", "waiting"]
DOCUMENT_CATEGORIES = ["contract", "id_back", "id_front", "other", "proof_address", "statement"]
DOC_REVIEW_STATUSES = ["approved", "needs_update", "pending", "rejected"]


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
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is missing. Check your .env file.")
    raw = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


_F = None


def fernet() -> Fernet:
    global _F
    if _F is None:
        _F = get_fernet()
    return _F


def enc_text(value: Optional[str]) -> Optional[bytes]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    return fernet().encrypt(v.encode("utf-8"))


def dec_text(value: Optional[bytes]) -> str:
    if value is None:
        return ""
    try:
        return fernet().decrypt(value).decode("utf-8")
    except InvalidToken:
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
# Fetch helpers
# ---------------------------

def fetch_score_history_grouped(cur, client_id: str, limit_each: int = 25):
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


def fetch_credentials(cur, client_id: str, reveal_id: Optional[str] = None):
    cur.execute("""
        SELECT id::text,
               provider,
               username_encrypted,
               password_encrypted,
               pin_encrypted,
               security_question_encrypted,
               security_answer_encrypted,
               COALESCE(note, '') AS note,
               updated_at
        FROM client_credentials
        WHERE client_id = %s
        ORDER BY provider, updated_at DESC
    """, (client_id,))
    rows = cur.fetchall()

    results = []
    for r in rows:
        cid, provider, u_enc, p_enc, pin_enc, q_enc, a_enc, note, updated_at = r
        item = {
            "id": cid,
            "provider": provider,
            "username": dec_text(u_enc),
            "password": "",
            "pin": "",
            "security_question": "",
            "security_answer": "",
            "note": note,
            "updated_at": str(updated_at) if updated_at else "",
            "revealed": reveal_id == cid
        }
        if reveal_id == cid:
            item["password"] = dec_text(p_enc)
            item["pin"] = dec_text(pin_enc)
            item["security_question"] = dec_text(q_enc)
            item["security_answer"] = dec_text(a_enc)
        results.append(item)
    return results


def fetch_notes(cur, client_id: str, limit: int = 30):
    cur.execute("""
        SELECT id::text,
               note_type,
               note_text,
               created_by,
               created_at
        FROM client_notes
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "note_type": r[1],
            "note_text": r[2],
            "created_by": r[3],
            "created_at": str(r[4]) if r[4] else "",
        }
        for r in rows
    ]


def fetch_appointments(cur, client_id: str, limit: int = 20):
    cur.execute("""
        SELECT id::text,
               appointment_mode,
               appointment_with,
               appointment_date,
               appointment_time,
               consultation_fee,
               location_name,
               address_line1,
               apt_unit,
               address_line2,
               city,
               state,
               zip,
               phone_to_call,
               meeting_link,
               email_enabled,
               sms_enabled,
               sms_opt_in,
               status,
               notes,
               created_at
        FROM client_appointments
        WHERE client_id = %s
        ORDER BY appointment_date DESC, appointment_time DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "appointment_mode": r[1],
            "appointment_with": r[2],
            "appointment_date": str(r[3]) if r[3] else "",
            "appointment_time": str(r[4]) if r[4] else "",
            "consultation_fee": r[5],
            "location_name": r[6] or "",
            "address_line1": r[7] or "",
            "apt_unit": r[8] or "",
            "address_line2": r[9] or "",
            "city": r[10] or "",
            "state": r[11] or "",
            "zip": r[12] or "",
            "phone_to_call": r[13] or "",
            "meeting_link": r[14] or "",
            "email_enabled": r[15],
            "sms_enabled": r[16],
            "sms_opt_in": r[17],
            "status": r[18] or "",
            "notes": r[19] or "",
            "created_at": str(r[20]) if r[20] else "",
        })
    return out


def fetch_followups(cur, client_id: str, limit: int = 100):
    cur.execute("""
        SELECT id::text, followup_type, due_date, COALESCE(status, 'open'), COALESCE(note_text, ''), created_at
        FROM client_followups
        WHERE client_id = %s
        ORDER BY due_date ASC NULLS LAST, created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "followup_type": r[1], "due_date": str(r[2]) if r[2] else '',
        "status": r[3], "note_text": r[4], "created_at": str(r[5]) if r[5] else ''
    } for r in rows]


def fetch_redispute_events(cur, client_id: str, limit: int = 100):
    cur.execute("""
        SELECT id::text, COALESCE(bureau, ''), event_date, COALESCE(round_number, 1), COALESCE(status, 'scheduled'), COALESCE(notes, ''), created_at
        FROM client_redispute_events
        WHERE client_id = %s
        ORDER BY event_date ASC NULLS LAST, created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "event_date": str(r[2]) if r[2] else '', "round_number": r[3],
        "status": r[4], "notes": r[5], "created_at": str(r[6]) if r[6] else ''
    } for r in rows]


def fetch_documents(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text,
               COALESCE(doc_type::text, '') AS doc_type,
               COALESCE(doc_category, '') AS doc_category,
               COALESCE(file_name, '') AS file_name,
               COALESCE(file_path, '') AS file_path,
               COALESCE(description, '') AS description,
               statement_date,
               refresh_every_days,
               remind_on,
               expires_on,
               COALESCE(review_status::text, 'pending') AS review_status,
               COALESCE(review_notes, '') AS review_notes,
               created_at
        FROM client_documents
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "doc_type": r[1], "doc_category": r[2], "file_name": r[3], "file_path": r[4],
        "description": r[5], "statement_date": str(r[6]) if r[6] else '', "refresh_every_days": r[7],
        "remind_on": str(r[8]) if r[8] else '', "expires_on": str(r[9]) if r[9] else '',
        "review_status": r[10], "review_notes": r[11], "created_at": str(r[12]) if r[12] else ''
    } for r in rows]


def fetch_upload_requests(cur, client_id: str, limit: int = 50):
    cur.execute("""
        SELECT id::text, token::text, COALESCE(request_type, 'general_upload'), COALESCE(allowed_doc_types, ''),
               expires_at, COALESCE(status, 'open'), created_at, used_at
        FROM client_upload_requests
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "token": r[1], "request_type": r[2], "allowed_doc_types": r[3],
        "expires_at": str(r[4]) if r[4] else '', "status": r[5],
        "created_at": str(r[6]) if r[6] else '', "used_at": str(r[7]) if r[7] else ''
    } for r in rows]


def fetch_referral_partners(cur):
    cur.execute("""
        SELECT id::text, name, COALESCE(company_name, ''), COALESCE(partner_type, '')
        FROM referral_partners
        WHERE COALESCE(is_active, TRUE) = TRUE
        ORDER BY lower(COALESCE(name, '')), lower(COALESCE(company_name, ''))
    """)
    rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "company_name": r[2],
            "partner_type": r[3],
        }
        for r in rows
    ]


def fetch_negative_items(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text, bureau, creditor_name, COALESCE(account_number, ''), COALESCE(account_type, ''),
               COALESCE(dispute_reason, ''), COALESCE(current_status, 'open'), COALESCE(notes, ''),
               created_at, updated_at
        FROM client_negative_items
        WHERE client_id = %s
        ORDER BY lower(COALESCE(creditor_name,'')), created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "creditor_name": r[2], "account_number": r[3], "account_type": r[4],
        "dispute_reason": r[5], "current_status": r[6], "notes": r[7],
        "created_at": str(r[8]) if r[8] else '', "updated_at": str(r[9]) if r[9] else ''
    } for r in rows]


def fetch_personal_info_errors(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text, COALESCE(bureau, ''), error_type, value_text, COALESCE(dispute_action, ''),
               COALESCE(current_status, 'open'), COALESCE(notes, ''), created_at, updated_at
        FROM client_personal_info_errors
        WHERE client_id = %s
        ORDER BY lower(COALESCE(error_type,'')), created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "error_type": r[2], "value_text": r[3], "dispute_action": r[4],
        "current_status": r[5], "notes": r[6], "created_at": str(r[7]) if r[7] else '',
        "updated_at": str(r[8]) if r[8] else ''
    } for r in rows]


def fetch_credit_products(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text, lender_name, credit_type, credit_limit, due_date, cutoff_date,
               secured_deposit_amount, origination_date, COALESCE(notes, ''), created_at
        FROM client_credit_products
        WHERE client_id = %s
        ORDER BY origination_date DESC NULLS LAST, created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "lender_name": r[1], "credit_type": r[2], "credit_limit": r[3], "due_date": r[4],
        "cutoff_date": r[5], "secured_deposit_amount": r[6], "origination_date": str(r[7]) if r[7] else '',
        "notes": r[8], "created_at": str(r[9]) if r[9] else ''
    } for r in rows]


def fetch_outbound_referrals(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT cor.id::text, cor.referral_date, COALESCE(cor.referral_reason, ''), COALESCE(cor.status, 'referred'),
               COALESCE(cor.notes, ''), rp.id::text, rp.name, COALESCE(rp.company_name, ''), COALESCE(rp.partner_type, '')
        FROM client_outbound_referrals cor
        JOIN referral_partners rp ON rp.id = cor.referral_partner_id
        WHERE cor.client_id = %s
        ORDER BY cor.referral_date DESC, rp.name
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "referral_date": str(r[1]) if r[1] else '', "referral_reason": r[2], "status": r[3],
        "notes": r[4], "partner_id": r[5], "partner_name": r[6], "company_name": r[7], "partner_type": r[8]
    } for r in rows]


def fetch_client_profile(cur, client_id: str, include_sensitive: bool = False):
    cur.execute("""
        SELECT c.id::text,
               c.first_name,
               COALESCE(c.middle_name, '') AS middle_name,
               c.last_name,
               COALESCE(c.suffix, '') AS suffix,
               c.phone,
               COALESCE(c.primary_phone_type, 'cell') AS primary_phone_type,
               COALESCE(c.secondary_phone, '') AS secondary_phone,
               COALESCE(c.secondary_phone_type, 'other') AS secondary_phone_type,
               COALESCE(c.primary_email, c.email, '') AS primary_email,
               COALESCE(c.secondary_email, '') AS secondary_email,
               COALESCE(c.preferred_email_choice, 'primary') AS preferred_email_choice,
               COALESCE(c.send_to_both_emails, FALSE) AS send_to_both_emails,
               c.date_of_birth,
               c.start_date,
               c.cancelled_date,
               COALESCE(c.is_active, TRUE) AS is_active,
               COALESCE(c.lifecycle_status, 'candidate') AS lifecycle_status,
               COALESCE(c.pending_payment, FALSE) AS pending_payment,
               COALESCE(c.consultation_fee, 100.00) AS consultation_fee,
               COALESCE(c.initial_fee, 175.00) AS initial_fee,
               COALESCE(c.monthly_fee, 125.00) AS monthly_fee,
               COALESCE(c.pricing_plan, 'standard program') AS pricing_plan,
               c.signup_date,
               c.first_payment_date,
               COALESCE(c.billing_group, '15th') AS billing_group,
               c.custom_billing_day,
               COALESCE(c.preferred_payment_method, 'zelle') AS preferred_payment_method,
               c.referred_by_partner_id::text,
               COALESCE(rp.name, '') AS referred_by_partner_name,
               COALESCE(c.referral_reason, '') AS referral_reason,
               COALESCE(c.ssn_last4, '') AS ssn_last4,
               c.ssn_full_enc,
               COALESCE(addr.line1, '') AS address_line1,
               COALESCE(addr.apt_unit, '') AS apt_unit,
               COALESCE(addr.line2, '') AS address_line2,
               COALESCE(addr.city, '') AS city,
               COALESCE(addr.state, '') AS state,
               COALESCE(addr.zip, '') AS zip
        FROM clients c
        LEFT JOIN referral_partners rp ON rp.id = c.referred_by_partner_id
        LEFT JOIN LATERAL (
            SELECT a.line1, COALESCE(a.apt_unit, '') AS apt_unit, a.line2, a.city, a.state, a.zip
            FROM client_addresses a
            WHERE a.client_id = c.id AND a.is_current = TRUE
            ORDER BY a.created_at DESC
            LIMIT 1
        ) addr ON TRUE
        WHERE c.id = %s
    """, (client_id,))
    row = cur.fetchone()
    if not row:
        return {}
    cols = [d[0] for d in cur.description]
    profile = dict(zip(cols, row))
    if include_sensitive:
        profile["ssn_full"] = dec_text(profile.get("ssn_full_enc"))
    else:
        profile["ssn_full"] = ""
    return profile


def load_client_workspace_context(cur, client_id: str, reveal_cred_id: Optional[str] = None):
    # Dashboard view for score summary / letters
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

    profile = fetch_client_profile(cur, client_id, include_sensitive=False)
    score_groups = fetch_score_history_grouped(cur, client_id, 25)
    credentials = fetch_credentials(cur, client_id, reveal_cred_id)
    notes = fetch_notes(cur, client_id, 30)
    appointments = fetch_appointments(cur, client_id, 20)
    followups = fetch_followups(cur, client_id, 100)
    redispute_events = fetch_redispute_events(cur, client_id, 100)
    documents = fetch_documents(cur, client_id, 200)
    upload_requests = fetch_upload_requests(cur, client_id, 50)
    referral_partners = fetch_referral_partners(cur)
    negative_items = fetch_negative_items(cur, client_id, 200)
    personal_info_errors = fetch_personal_info_errors(cur, client_id, 200)
    credit_products = fetch_credit_products(cur, client_id, 200)
    outbound_referrals = fetch_outbound_referrals(cur, client_id, 200)

    return dashboard, profile, score_groups, credentials, notes, appointments, followups, redispute_events, documents, upload_requests, referral_partners, negative_items, personal_info_errors, credit_products, outbound_referrals


def render_client_workspace(request: Request, client_id: str, message: str = "", error: str = "", reveal_cred_id: Optional[str] = None, active_tab: Optional[str] = None):
    tab_defs = [
        ("overview", "Overview"),
        ("profile", "Profile"),
        ("notes", "Notes"),
        ("calendar", "Calendar"),
        ("credentials_scores", "Credentials & Scores"),
        ("disputes", "Disputes"),
        ("personal_info", "Personal Info Errors"),
        ("documents", "Documents"),
        ("credit_products", "Credit Products"),
        ("referrals", "Referrals"),
    ]
    valid_tabs = {k for k, _ in tab_defs}
    chosen_tab = active_tab or request.query_params.get("tab") or "overview"
    if chosen_tab not in valid_tabs:
        chosen_tab = "overview"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            dashboard, profile, score_groups, credentials, notes, appointments, followups, redispute_events, documents, upload_requests, referral_partners, negative_items, personal_info_errors, credit_products, outbound_referrals = load_client_workspace_context(cur, client_id, reveal_cred_id)
        if not profile:
            return templates.TemplateResponse("index.html", {"request": request, "error": "Client not found."})
        return templates.TemplateResponse(
            "client.html",
            {
                "request": request,
                "client_id": client_id,
                "dashboard": dashboard,
                "profile": profile,
                "score_groups": score_groups,
                "credentials": credentials,
                "notes": notes,
                "appointments": appointments,
                "followups": followups,
                "redispute_events": redispute_events,
                "documents": documents,
                "upload_requests": upload_requests,
                "referral_partners": referral_partners,
                "negative_items": negative_items,
                "personal_info_errors": personal_info_errors,
                "credit_products": credit_products,
                "outbound_referrals": outbound_referrals,
                "providers": PROVIDERS,
                "bureaus": BUREAUS,
                "dispute_reasons": DISPUTE_REASONS,
                "negative_item_statuses": NEGATIVE_ITEM_STATUSES,
                "personal_error_types": PERSONAL_ERROR_TYPES,
                "personal_error_statuses": PERSONAL_ERROR_STATUSES,
                "credit_types": CREDIT_TYPES,
                "outbound_referral_statuses": OUTBOUND_REFERRAL_STATUSES,
                "referral_partner_types": REFERRAL_PARTNER_TYPES,
                "followup_types": FOLLOWUP_TYPES,
                "followup_statuses": FOLLOWUP_STATUSES,
                "document_categories": DOCUMENT_CATEGORIES,
                "doc_review_statuses": DOC_REVIEW_STATUSES,
                "tabs": tab_defs,
                "active_tab": chosen_tab,
                "message": message,
                "error": error,
            }
        )
    finally:
        conn.close()


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
def ui_client_post(request: Request, client_id: str = Form(...)):
    return render_client_workspace(request, client_id, active_tab="overview")


@app.get("/ui/client/{client_id}", response_class=HTMLResponse)
def ui_client_get(request: Request, client_id: str):
    return render_client_workspace(request, client_id)


@app.get("/ui/client/{client_id}/edit", response_class=HTMLResponse)
def ui_client_edit(request: Request, client_id: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            profile = fetch_client_profile(cur, client_id, include_sensitive=True)
            referral_partners = fetch_referral_partners(cur)
        if not profile:
            return templates.TemplateResponse("index.html", {"request": request, "error": "Client not found."})

        return templates.TemplateResponse(
            "client_edit.html",
            {
                "request": request,
                "client_id": client_id,
                "profile": profile,
                "referral_partners": referral_partners,
                "phone_types": PHONE_TYPES,
                "suffixes": SUFFIXES,
                "statuses": STATUSES,
                "payment_methods": PAYMENT_METHODS,
                "billing_groups": BILLING_GROUPS,
                "pricing_plans": PRICING_PLANS,
            }
        )
    finally:
        conn.close()


@app.post("/ui/client/save-profile", response_class=HTMLResponse)
def ui_save_client_profile(
    request: Request,
    client_id: str = Form(...),
    first_name: str = Form(...),
    middle_name: str = Form(""),
    last_name: str = Form(...),
    suffix: str = Form(""),
    primary_phone: str = Form(""),
    primary_phone_type: str = Form("cell"),
    secondary_phone: str = Form(""),
    secondary_phone_type: str = Form("other"),
    primary_email: str = Form(""),
    secondary_email: str = Form(""),
    preferred_email_choice: str = Form("primary"),
    send_to_both_emails: bool = Form(False),
    date_of_birth: str = Form(""),
    ssn_full: str = Form(""),
    is_active: bool = Form(False),
    lifecycle_status: str = Form("candidate"),
    pending_payment: bool = Form(False),
    start_date: str = Form(""),
    cancelled_date: str = Form(""),
    consultation_fee: float = Form(100.0),
    initial_fee: float = Form(175.0),
    monthly_fee: float = Form(125.0),
    pricing_plan: str = Form("standard program"),
    signup_date: str = Form(""),
    first_payment_date: str = Form(""),
    billing_group: str = Form("15th"),
    custom_billing_day: str = Form(""),
    preferred_payment_method: str = Form("zelle"),
    referred_by_partner_id: str = Form(""),
    referral_reason: str = Form(""),
    address_line1: str = Form(""),
    apt_unit: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            ssn_last4 = ssn_full[-4:] if ssn_full and len(ssn_full) >= 4 else None
            ssn_full_enc = enc_text(ssn_full)

            cur.execute("""
                UPDATE clients
                SET first_name = %s,
                    middle_name = NULLIF(%s, ''),
                    last_name = %s,
                    suffix = NULLIF(%s, ''),
                    phone = NULLIF(%s, ''),
                    primary_phone_type = NULLIF(%s, ''),
                    secondary_phone = NULLIF(%s, ''),
                    secondary_phone_type = NULLIF(%s, ''),
                    primary_email = NULLIF(%s, ''),
                    email = NULLIF(%s, ''),
                    secondary_email = NULLIF(%s, ''),
                    preferred_email_choice = %s,
                    send_to_both_emails = %s,
                    date_of_birth = NULLIF(%s, '')::date,
                    ssn_last4 = %s,
                    ssn_full_enc = COALESCE(%s, ssn_full_enc),
                    is_active = %s,
                    lifecycle_status = %s,
                    pending_payment = %s,
                    start_date = NULLIF(%s, '')::date,
                    cancelled_date = NULLIF(%s, '')::date,
                    consultation_fee = %s,
                    initial_fee = %s,
                    monthly_fee = %s,
                    pricing_plan = %s,
                    signup_date = NULLIF(%s, '')::date,
                    first_payment_date = NULLIF(%s, '')::date,
                    billing_group = %s,
                    custom_billing_day = NULLIF(%s, '')::smallint,
                    preferred_payment_method = %s,
                    referred_by_partner_id = NULLIF(%s, '')::uuid,
                    referral_reason = NULLIF(%s, ''),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                first_name, middle_name, last_name, suffix,
                primary_phone, primary_phone_type,
                secondary_phone, secondary_phone_type,
                primary_email, primary_email,
                secondary_email, preferred_email_choice, send_to_both_emails,
                date_of_birth, ssn_last4, ssn_full_enc,
                is_active, lifecycle_status, pending_payment,
                start_date, cancelled_date,
                consultation_fee, initial_fee, monthly_fee, pricing_plan,
                signup_date, first_payment_date,
                billing_group, custom_billing_day, preferred_payment_method,
                referred_by_partner_id, referral_reason, client_id
            ))

            cur.execute("""
                SELECT id
                FROM client_addresses
                WHERE client_id = %s AND is_current = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, (client_id,))
            addr = cur.fetchone()

            if addr:
                cur.execute("""
                    UPDATE client_addresses
                    SET line1 = NULLIF(%s, ''),
                        apt_unit = NULLIF(%s, ''),
                        line2 = NULLIF(%s, ''),
                        city = NULLIF(%s, ''),
                        state = NULLIF(%s, ''),
                        zip = NULLIF(%s, '')
                    WHERE id = %s
                """, (address_line1, apt_unit, address_line2, city, state, zip_code, addr[0]))
            else:
                cur.execute("""
                    INSERT INTO client_addresses
                      (client_id, is_current, line1, apt_unit, line2, city, state, zip)
                    VALUES
                      (%s, TRUE, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))
                """, (client_id, address_line1, apt_unit, address_line2, city, state, zip_code))

        conn.close()
        return render_client_workspace(request, client_id, message="Client profile saved.")
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-note", response_class=HTMLResponse)
def ui_add_note(
    request: Request,
    client_id: str = Form(...),
    note_text: str = Form(...),
    note_type: str = Form("general"),
    active_tab: str = Form("notes"),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_notes (client_id, note_text, note_type, created_by)
                VALUES (%s, %s, %s, %s)
            """, (client_id, note_text, note_type, "Carlos G Suarez"))
        conn.close()
        return render_client_workspace(request, client_id, message="Note added.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-note", response_class=HTMLResponse)
def ui_delete_note(request: Request, client_id: str = Form(...), note_id: str = Form(...), active_tab: str = Form("notes")):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_notes WHERE id = %s AND client_id = %s", (note_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Note deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-appointment", response_class=HTMLResponse)
def ui_add_appointment(
    request: Request,
    client_id: str = Form(...),
    appointment_mode: str = Form("phone"),
    appointment_date: str = Form(...),
    appointment_time: str = Form(...),
    consultation_fee: float = Form(100.0),
    location_name: str = Form("Carlos G Suarez"),
    address_line1: str = Form("8345 SW 41st Ter"),
    apt_unit: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form("Miami"),
    state: str = Form("FL"),
    zip_code: str = Form("33155"),
    phone_to_call: str = Form(""),
    meeting_link: str = Form(""),
    email_enabled: bool = Form(True),
    sms_enabled: bool = Form(False),
    sms_opt_in: bool = Form(False),
    notes: str = Form(""),
    active_tab: str = Form("calendar"),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_appointments
                  (client_id, appointment_mode, appointment_with, appointment_date, appointment_time,
                   consultation_fee, location_name, address_line1, apt_unit, address_line2, city, state, zip,
                   phone_to_call, meeting_link, email_enabled, sms_enabled, sms_opt_in, notes)
                VALUES
                  (%s, %s, %s, %s::date, %s::time, %s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), %s, %s, %s,
                   NULLIF(%s,''), NULLIF(%s,''), %s, %s, %s, NULLIF(%s,''))
            """, (
                client_id, appointment_mode, "Carlos G Suarez", appointment_date, appointment_time,
                consultation_fee, location_name, address_line1, apt_unit, address_line2, city, state, zip_code,
                phone_to_call, meeting_link, email_enabled, sms_enabled, sms_opt_in, notes
            ))
        conn.close()
        return render_client_workspace(request, client_id, message="Appointment saved. Email/SMS automation hooks are ready for the next step.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-appointment", response_class=HTMLResponse)
def ui_delete_appointment(request: Request, client_id: str = Form(...), appointment_id: str = Form(...), active_tab: str = Form("appointments")):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_appointments WHERE id = %s AND client_id = %s", (appointment_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Appointment deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/reveal-credential", response_class=HTMLResponse)
def ui_reveal_credential(request: Request, client_id: str = Form(...), credential_id: str = Form(...), active_tab: str = Form("credentials_scores")):
    try:
        return render_client_workspace(request, client_id, message="Credential revealed on this screen only.", reveal_cred_id=credential_id, active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


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
    active_tab: str = Form("credentials_scores"),
):
    try:
        if provider not in PROVIDERS:
            raise RuntimeError("Invalid provider")

        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
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
                """, (
                    enc_text(username), enc_text(password), enc_text(pin),
                    enc_text(security_question), enc_text(security_answer),
                    note, client_id, provider
                ))
            else:
                cur.execute("""
                    INSERT INTO client_credentials
                      (client_id, provider, username_encrypted, password_encrypted,
                       pin_encrypted, security_question_encrypted, security_answer_encrypted,
                       note, updated_at)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (
                    client_id, provider, enc_text(username), enc_text(password),
                    enc_text(pin), enc_text(security_question), enc_text(security_answer),
                    note
                ))
        conn.close()
        return render_client_workspace(request, client_id, message="Credential saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-credential", response_class=HTMLResponse)
def ui_delete_credential(request: Request, client_id: str = Form(...), credential_id: str = Form(...), active_tab: str = Form("credentials_scores")):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_credentials WHERE id = %s AND client_id = %s", (credential_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Credential deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-score", response_class=HTMLResponse)
def ui_add_score(
    request: Request,
    client_id: str = Form(...),
    report_date: str = Form(...),
    bureau: str = Form(...),
    source: str = Form(...),
    model: str = Form(...),
    score: int = Form(...),
    active_tab: str = Form("credentials_scores"),
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
        conn.close()
        return render_client_workspace(request, client_id, message="Score saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-myfico", response_class=HTMLResponse)
def ui_add_myfico(
    request: Request,
    client_id: str = Form(...),
    report_date: str = Form(...),
    experian_score: int = Form(...),
    transunion_score: int = Form(...),
    equifax_score: int = Form(...),
    active_tab: str = Form("credentials_scores"),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            for bureau, sc in [("experian", experian_score), ("transunion", transunion_score), ("equifax", equifax_score)]:
                cur.execute("""
                    INSERT INTO credit_report_snapshots
                      (client_id, report_date, bureau, source, model, score)
                    VALUES
                      (%s, %s, %s, %s, %s, %s)
                """, (client_id, report_date, bureau, "myfico_free", "FICO 8", sc))
        conn.close()
        return render_client_workspace(request, client_id, message="MyFICO scores saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-score", response_class=HTMLResponse)
def ui_delete_score(request: Request, client_id: str = Form(...), snapshot_id: str = Form(...), active_tab: str = Form("credentials_scores")):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM credit_report_snapshots WHERE id = %s AND client_id = %s",
                        (snapshot_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Score entry deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-negative-item", response_class=HTMLResponse)
def ui_add_negative_item(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(...),
    creditor_name: str = Form(...),
    account_number: str = Form(""),
    account_type: str = Form(""),
    dispute_reason: str = Form(...),
    current_status: str = Form("open"),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_negative_items
                  (client_id, bureau, creditor_name, account_number, account_type, dispute_reason, current_status, notes)
                VALUES
                  (%s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), %s, %s, NULLIF(%s,''))
            """, (client_id, bureau, creditor_name, account_number, account_type, dispute_reason, current_status, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Negative/dispute item saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-negative-item", response_class=HTMLResponse)
def ui_delete_negative_item(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("disputes")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_negative_items WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Negative/dispute item deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-personal-info-error", response_class=HTMLResponse)
def ui_add_personal_info_error(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(""),
    error_type: str = Form(...),
    value_text: str = Form(...),
    dispute_action: str = Form("delete"),
    current_status: str = Form("open"),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_personal_info_errors
                  (client_id, bureau, error_type, value_text, dispute_action, current_status, notes)
                VALUES
                  (%s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), %s, NULLIF(%s,''))
            """, (client_id, bureau, error_type, value_text, dispute_action, current_status, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal information error saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-personal-info-error", response_class=HTMLResponse)
def ui_delete_personal_info_error(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("personal_info")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_personal_info_errors WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal information error deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-credit-product", response_class=HTMLResponse)
def ui_add_credit_product(
    request: Request,
    client_id: str = Form(...),
    lender_name: str = Form(...),
    credit_type: str = Form(...),
    credit_limit: str = Form(""),
    due_date: str = Form(""),
    cutoff_date: str = Form(""),
    secured_deposit_amount: str = Form(""),
    origination_date: str = Form(""),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_credit_products
                  (client_id, lender_name, credit_type, credit_limit, due_date, cutoff_date, secured_deposit_amount, origination_date, notes)
                VALUES
                  (%s, %s, %s, NULLIF(%s,'')::numeric, NULLIF(%s,'')::smallint, NULLIF(%s,'')::smallint,
                   NULLIF(%s,'')::numeric, NULLIF(%s,'')::date, NULLIF(%s,''))
            """, (client_id, lender_name, credit_type, credit_limit, due_date, cutoff_date, secured_deposit_amount, origination_date, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Credit product saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-credit-product", response_class=HTMLResponse)
def ui_delete_credit_product(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("credit_products")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_credit_products WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Credit product deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/save-referral-partner", response_class=HTMLResponse)
def ui_save_referral_partner(
    request: Request,
    client_id: str = Form(...),
    name: str = Form(...),
    company_name: str = Form(""),
    partner_type: str = Form("other"),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    active_tab: str = Form("referrals"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO referral_partners (name, company_name, partner_type, phone, email, notes)
                VALUES (%s, NULLIF(%s,''), %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))
            """, (name, company_name, partner_type, phone, email, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Referral partner saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-outbound-referral", response_class=HTMLResponse)
def ui_add_outbound_referral(
    request: Request,
    client_id: str = Form(...),
    referral_partner_id: str = Form(...),
    referral_date: str = Form(...),
    referral_reason: str = Form(""),
    status: str = Form("referred"),
    notes: str = Form(""),
    active_tab: str = Form("referrals"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_outbound_referrals
                  (client_id, referral_partner_id, referral_date, referral_reason, status, notes)
                VALUES
                  (%s, %s, %s::date, NULLIF(%s,''), %s, NULLIF(%s,''))
            """, (client_id, referral_partner_id, referral_date, referral_reason, status, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Outbound referral saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/delete-outbound-referral", response_class=HTMLResponse)
def ui_delete_outbound_referral(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("referrals")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_outbound_referrals WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Outbound referral deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e))


@app.post("/ui/add-followup", response_class=HTMLResponse)
def ui_add_followup(
    request: Request,
    client_id: str = Form(...),
    followup_type: str = Form("general"),
    due_date: str = Form(""),
    status: str = Form("open"),
    note_text: str = Form(""),
    active_tab: str = Form("calendar"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_followups (client_id, followup_type, due_date, status, note_text)
                VALUES (%s, %s, NULLIF(%s,'')::date, %s, NULLIF(%s,''))
            """, (client_id, followup_type, due_date, status, note_text))
        conn.close()
        return render_client_workspace(request, client_id, message="Follow-up saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-followup", response_class=HTMLResponse)
def ui_delete_followup(request: Request, client_id: str = Form(...), followup_id: str = Form(...), active_tab: str = Form("calendar")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_followups WHERE id = %s AND client_id = %s", (followup_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Follow-up deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/add-redispute-event", response_class=HTMLResponse)
def ui_add_redispute_event(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(""),
    event_date: str = Form(...),
    round_number: int = Form(1),
    status: str = Form("scheduled"),
    notes: str = Form(""),
    active_tab: str = Form("calendar"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_redispute_events (client_id, bureau, event_date, round_number, status, notes)
                VALUES (%s, NULLIF(%s,''), %s::date, %s, %s, NULLIF(%s,''))
            """, (client_id, bureau, event_date, round_number, status, notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Redispute event saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-redispute-event", response_class=HTMLResponse)
def ui_delete_redispute_event(request: Request, client_id: str = Form(...), event_id: str = Form(...), active_tab: str = Form("calendar")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_redispute_events WHERE id = %s AND client_id = %s", (event_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Redispute event deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/create-upload-request", response_class=HTMLResponse)
def ui_create_upload_request(
    request: Request,
    client_id: str = Form(...),
    request_type: str = Form("general_upload"),
    allowed_doc_types: str = Form(""),
    expires_days: int = Form(30),
    active_tab: str = Form("documents"),
):
    try:
        token = str(uuid4())
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_upload_requests (client_id, token, request_type, allowed_doc_types, expires_at, status)
                VALUES (%s, %s::uuid, %s, NULLIF(%s,''), CURRENT_TIMESTAMP + (%s || ' days')::interval, 'open')
            """, (client_id, token, request_type, allowed_doc_types, expires_days))
        conn.close()
        upload_link = str(request.base_url).rstrip('/') + f"/upload/{token}"
        return render_client_workspace(request, client_id, message=f"Upload request created: {upload_link}", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.get("/upload/{token}", response_class=HTMLResponse)
def upload_page(request: Request, token: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ur.id::text, ur.client_id::text, COALESCE(ur.request_type,'general_upload'), COALESCE(ur.allowed_doc_types,''),
                       ur.expires_at, COALESCE(ur.status,'open'), c.first_name, c.last_name
                FROM client_upload_requests ur
                JOIN clients c ON c.id = ur.client_id
                WHERE ur.token = %s::uuid
                LIMIT 1
            """, (token,))
            row = cur.fetchone()
        if not row:
            return HTMLResponse("<h3>Upload link not found.</h3>", status_code=404)
        req = {
            "id": row[0], "client_id": row[1], "request_type": row[2], "allowed_doc_types": row[3],
            "expires_at": str(row[4]) if row[4] else '', "status": row[5], "client_name": f"{row[6]} {row[7]}"
        }
        return templates.TemplateResponse("upload.html", {"request": request, "token": token, "upload_request": req, "document_categories": DOCUMENT_CATEGORIES})
    finally:
        conn.close()


@app.post("/upload/{token}", response_class=HTMLResponse)
def upload_submit(
    request: Request,
    token: str,
    doc_category: str = Form("other"),
    description: str = Form(""),
    statement_date: str = Form(""),
    refresh_every_days: str = Form(""),
    file: UploadFile = File(...),
):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, client_id::text, COALESCE(status,'open')
                FROM client_upload_requests
                WHERE token = %s::uuid
                LIMIT 1
            """, (token,))
            row = cur.fetchone()
            if not row:
                return HTMLResponse("<h3>Upload link not found.</h3>", status_code=404)
            upload_request_id, client_id, status = row
            if status != 'open':
                return HTMLResponse("<h3>This upload link is no longer open.</h3>", status_code=400)

            client_dir = os.path.join(UPLOAD_DIR, client_id)
            os.makedirs(client_dir, exist_ok=True)
            safe_name = f"{uuid4()}_{os.path.basename(file.filename)}"
            save_path = os.path.join(client_dir, safe_name)
            with open(save_path, 'wb') as f:
                f.write(file.file.read())

            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, statement_date,
                   refresh_every_days, review_status, upload_request_id, created_at)
                VALUES
                  (%s, 'client_upload', %s, %s, NULLIF(%s,''), %s, NULLIF(%s,'')::date,
                   NULLIF(%s,'')::smallint, 'pending', %s::uuid, CURRENT_TIMESTAMP)
            """, (client_id, file.filename, save_path, description, doc_category, statement_date, refresh_every_days, upload_request_id))

            cur.execute("UPDATE client_upload_requests SET used_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (upload_request_id,))

        return HTMLResponse("<h3>Thank you. Your document was uploaded successfully.</h3>")
    finally:
        conn.close()


@app.post("/ui/add-document-meta", response_class=HTMLResponse)
def ui_add_document_meta(
    request: Request,
    client_id: str = Form(...),
    doc_category: str = Form("other"),
    file_name: str = Form(...),
    file_path: str = Form(""),
    description: str = Form(""),
    statement_date: str = Form(""),
    refresh_every_days: str = Form(""),
    review_status: str = Form("pending"),
    review_notes: str = Form(""),
    active_tab: str = Form("documents"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, statement_date,
                   refresh_every_days, review_status, review_notes, created_at)
                VALUES
                  (%s, 'manual_log', %s, NULLIF(%s,''), NULLIF(%s,''), %s, NULLIF(%s,'')::date,
                   NULLIF(%s,'')::smallint, %s, NULLIF(%s,''), CURRENT_TIMESTAMP)
            """, (client_id, file_name, file_path, description, doc_category, statement_date, refresh_every_days, review_status, review_notes))
        conn.close()
        return render_client_workspace(request, client_id, message="Document record saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-document", response_class=HTMLResponse)
def ui_delete_document(request: Request, client_id: str = Form(...), document_id: str = Form(...), active_tab: str = Form("documents")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_documents WHERE id = %s AND client_id = %s", (document_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Document record deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


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
        return render_client_workspace(request, client_id, error=str(e))
