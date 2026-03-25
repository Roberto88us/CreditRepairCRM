
import os
import json
import base64
import hashlib
import re
import mimetypes
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4
from xml.sax.saxutils import escape

import psycopg2
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.utils import ImageReader

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
SIGNATURE_DIR = os.path.join(UPLOAD_DIR, "client_signatures")

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
REQUESTED_ACTIONS = ["delete", "correct", "update", "investigate"]
INQUIRY_DISPUTE_REASONS = ["not my inquiry", "did not apply", "unauthorized pull", "other"]
INQUIRY_REQUESTED_ACTIONS = ["delete", "investigate"]
CREDIT_TYPES = ["auto loan", "car loan", "credit card", "other", "personal loan", "secured credit card", "secured loan", "store card"]
OUTBOUND_REFERRAL_STATUSES = ["completed", "contacted", "declined", "referred"]
REFERRAL_PARTNER_TYPES = ["accountant", "ADP", "banker", "bankruptcy attorney", "client", "estate attorney", "insurance agent", "lender", "mortgage lender", "other", "realtor", "title company"]
FOLLOWUP_TYPES = ["call back", "document reminder", "general", "payment follow-up", "redispute", "review bureau response", "send docs", "waiting on IDs"]
FOLLOWUP_STATUSES = ["done", "open", "waiting"]
DOCUMENT_CATEGORIES = [
    "service_agreement",
    "credit_report",
    "bureau_response",
    "collection_company_mail",
    "id_document",
    "proof_of_address",
    "credit_offer",
    "prior_dispute_letter",
    "supporting_document",
    "miscellaneous",
    "contract",
    "id_front",
    "id_back",
    "statement",
    "other",
]
DOCUMENT_SECTIONS = [
    ("agreements", "Agreements"),
    ("reports", "Reports"),
    ("bureau_responses", "Bureau Responses"),
    ("collection_mail", "Collection Company Mail"),
    ("ids_proof_of_address", "IDs & Proof of Address"),
    ("credit_offers", "Credit Offers"),
    ("miscellaneous", "Miscellaneous"),
]
DOCUMENT_SECTION_LABELS = {k: v for k, v in DOCUMENT_SECTIONS}
DOCUMENT_REFERENCE_DEFAULT_LABELS = {
    "experian": "Report Number",
    "transunion": "File#",
    "equifax": "Dispute Number",
}
BUREAU_DISPUTE_ADDRESSES = {
    "experian": ["Experian", "P.O. Box 4500", "Allen, TX 75013"],
    "transunion": ["TransUnion Consumer Solutions", "P.O. Box 2000", "Chester, PA 19016-2000"],
    "equifax": ["Equifax Information Services LLC", "P.O. Box 740256", "Atlanta, GA 30374-0256"],
}
DOC_REVIEW_STATUSES = ["approved", "needs_update", "pending", "rejected"]
EMAIL_DIRECTIONS = ["outbound", "inbound"]
EMAIL_STATUSES = ["sent", "received", "draft", "queued", "logged"]
EMAIL_TYPES = ["dispute_update", "client_reply", "document_request", "general", "internal_note"]
EMAIL_SOURCES = ["manual", "gmail_import", "system"]


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
# Date / format helpers
# ---------------------------

def format_short_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    s = str(value).strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return s


def format_long_date(value) -> str:
    if not value:
        value = date.today()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%B %d, %Y").replace(" 0", " ")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%B %d, %Y").replace(" 0", " ")
        except ValueError:
            pass
    return s


def parse_date_input(value: Optional[str]) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Invalid date format: {value}. Use MM/DD/YYYY.")


# ---------------------------
# PDF generation
# ---------------------------


def _normalize_letter_paragraphs(letter_text: str) -> list[str]:
    raw = (letter_text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if not raw:
        return []
    chunks = []
    for block in re.split(r"\n\s*\n", raw):
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        para = " ".join(lines)
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            chunks.append(para)
    return chunks


def _fit_signature(path: str, max_width: float = 2.0 * inch, max_height: float = 0.7 * inch):
    if not path or not os.path.exists(path):
        return None
    try:
        img = ImageReader(path)
        w, h = img.getSize()
        if not w or not h:
            return None
        scale = min(max_width / w, max_height / h)
        return RLImage(path, width=w * scale, height=h * scale)
    except Exception:
        return None


def _normalize_document_section(section: str, category: str) -> str:
    section = (section or "").strip().lower()
    category = (category or "").strip().lower()
    if section in DOCUMENT_SECTION_LABELS:
        return section
    mapping = {
        "service_agreement": "agreements",
        "contract": "agreements",
        "credit_report": "reports",
        "bureau_response": "bureau_responses",
        "collection_company_mail": "collection_mail",
        "id_document": "ids_proof_of_address",
        "id_front": "ids_proof_of_address",
        "id_back": "ids_proof_of_address",
        "proof_of_address": "ids_proof_of_address",
        "statement": "ids_proof_of_address",
        "credit_offer": "credit_offers",
        "prior_dispute_letter": "miscellaneous",
        "supporting_document": "miscellaneous",
        "other": "miscellaneous",
        "miscellaneous": "miscellaneous",
    }
    return mapping.get(category, "miscellaneous")


def _extract_letter_body_paragraphs(letter_text: str) -> list[str]:
    raw = (letter_text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\(\s*Round\s*\d+\s*\)", "", raw, flags=re.IGNORECASE)
    lines = [ln.strip() for ln in raw.split("\n")]

    start_idx = 0
    for i, line in enumerate(lines):
        lower = line.lower()
        if lower.startswith("to whom it may concern") or lower.startswith("dear "):
            start_idx = i + 1
            break

    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        lower = lines[i].lower()
        if lower.startswith(("sincerely", "regards", "respectfully", "best regards", "best,")):
            end_idx = i
            break

    body_lines = []
    for line in lines[start_idx:end_idx]:
        if not line:
            body_lines.append("")
            continue
        lower = line.lower()
        if lower.startswith("re:"):
            continue
        if line in BUREAU_DISPUTE_ADDRESSES.get("experian", []) or line in BUREAU_DISPUTE_ADDRESSES.get("transunion", []) or line in BUREAU_DISPUTE_ADDRESSES.get("equifax", []):
            continue
        if re.fullmatch(r"[A-Za-z]+\s+\d{1,2},\s+\d{4}", line):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
            continue
        body_lines.append(line)

    body = "\n".join(body_lines)
    body = re.sub(
        r"This is my\s+[^.]{0,120}?\bnotice\b[^.]*\.",
        "I am writing to dispute inaccurate information appearing on my credit file and to request a reasonable reinvestigation of the items listed below.",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"This is my\s+[^.]{0,120}?\brequest\b[^.]*\.",
        "I am writing to dispute inaccurate information appearing on my credit file and to request a reasonable reinvestigation of the items listed below.",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"\b[Rr]ound\s*\d+\b", "", body)
    body = re.sub(r"\s{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return _normalize_letter_paragraphs(body)


def _default_reference_label_for_bureau(bureau: str) -> str:
    return DOCUMENT_REFERENCE_DEFAULT_LABELS.get((bureau or "").lower(), "Reference Number")


def _document_kind_label(doc_section: str) -> str:
    return "Response" if (doc_section or "") == "bureau_responses" else "Report"


def _build_reference_line(primary_doc: Optional[dict], bureau: str) -> str:
    bureau_label = (bureau or "").title()
    if not primary_doc:
        return f"{bureau_label} Credit Report Dispute"
    kind = _document_kind_label(primary_doc.get("doc_section", ""))
    doc_date = primary_doc.get("document_date") or primary_doc.get("statement_date") or primary_doc.get("created_at")
    parts = [f"{bureau_label} {kind}"]
    if doc_date:
        parts.append(f"dated {format_long_date(doc_date)}")
    ref_value = (primary_doc.get("reference_value") or "").strip()
    if ref_value:
        ref_label = (primary_doc.get("reference_label") or _default_reference_label_for_bureau(bureau)).strip()
        parts.append(f"{ref_label}: {ref_value}")
    return " | ".join(parts)


def _fetch_selected_primary_document(cur, round_run_id: Optional[str], client_id: str, bureau: str) -> Optional[dict]:
    bureau = (bureau or "").lower()
    column_map = {
        "experian": "primary_experian_document_id",
        "transunion": "primary_transunion_document_id",
        "equifax": "primary_equifax_document_id",
    }
    row = None
    column_name = column_map.get(bureau)
    if round_run_id and column_name:
        cur.execute(f"""
            SELECT d.id::text,
                   COALESCE(d.doc_section, '') AS doc_section,
                   COALESCE(d.doc_category, '') AS doc_category,
                   COALESCE(d.file_name, '') AS file_name,
                   COALESCE(d.file_path, '') AS file_path,
                   COALESCE(d.bureau, '') AS bureau,
                   COALESCE(d.source_name, '') AS source_name,
                   d.document_date,
                   d.statement_date,
                   COALESCE(d.reference_label, '') AS reference_label,
                   COALESCE(d.reference_value, '') AS reference_value,
                   d.created_at
            FROM round_run_dispute_meta m
            LEFT JOIN client_documents d ON d.id = m.{column_name}
            WHERE m.round_run_id = %s
            LIMIT 1
        """, (round_run_id,))
        row = cur.fetchone()
        if row and not row[0]:
            row = None

    if not row:
        cur.execute("""
            SELECT id::text,
                   COALESCE(doc_section, '') AS doc_section,
                   COALESCE(doc_category, '') AS doc_category,
                   COALESCE(file_name, '') AS file_name,
                   COALESCE(file_path, '') AS file_path,
                   COALESCE(bureau, '') AS bureau,
                   COALESCE(source_name, '') AS source_name,
                   document_date,
                   statement_date,
                   COALESCE(reference_label, '') AS reference_label,
                   COALESCE(reference_value, '') AS reference_value,
                   created_at
            FROM client_documents
            WHERE client_id = %s
              AND lower(COALESCE(bureau, '')) = %s
              AND COALESCE(doc_section, '') IN ('reports', 'bureau_responses')
            ORDER BY COALESCE(document_date, statement_date) DESC NULLS LAST, created_at DESC
            LIMIT 1
        """, (client_id, bureau))
        row = cur.fetchone()

    if not row:
        return None
    return {
        "id": row[0],
        "doc_section": _normalize_document_section(row[1], row[2]),
        "doc_category": row[2],
        "file_name": row[3],
        "file_path": row[4],
        "bureau": row[5],
        "source_name": row[6],
        "document_date": str(row[7]) if row[7] else '',
        "statement_date": str(row[8]) if row[8] else '',
        "reference_label": row[9],
        "reference_value": row[10],
        "created_at": str(row[11]) if row[11] else '',
    }


def make_pdf(letter_text: str, out_path: str, client_profile: Optional[dict] = None, formal_date: Optional[str] = None,
             signature_path: Optional[str] = None, include_signature: bool = False, bureau: str = "",
             primary_doc: Optional[dict] = None):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    styles = getSampleStyleSheet()
    address_style = ParagraphStyle(
        "AddressBlock",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=11,
        leading=13,
        spaceAfter=0,
        textColor=colors.black,
    )
    right_date_style = ParagraphStyle("RightDate", parent=address_style, alignment=2)
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        spaceAfter=10,
        textColor=colors.black,
    )
    salutation_style = ParagraphStyle("Salutation", parent=body_style, spaceAfter=10)
    subject_style = ParagraphStyle("LetterSubject", parent=body_style, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=12)
    closing_style = ParagraphStyle("LetterClosing", parent=body_style, spaceBefore=12, spaceAfter=4)
    signature_name_style = ParagraphStyle("SignatureName", parent=body_style, fontName="Helvetica-Bold", spaceAfter=0)
    signature_meta_style = ParagraphStyle("SignatureMeta", parent=body_style, spaceBefore=0, spaceAfter=0)

    doc = SimpleDocTemplate(
        out_path,
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.8 * inch,
    )

    profile = client_profile or {}
    story = []
    full_name = " ".join([part for part in [profile.get("first_name"), profile.get("middle_name"), profile.get("last_name"), profile.get("suffix")] if part]).strip()

    address_lines = []
    if profile.get("address_line1"):
        line1 = profile.get("address_line1")
        if profile.get("apt_unit"):
            line1 += f", Unit {profile.get('apt_unit')}"
        address_lines.append(line1)
    if profile.get("address_line2"):
        address_lines.append(profile.get("address_line2"))
    city_bits = [profile.get("city") or "", profile.get("state") or "", profile.get("zip") or ""]
    city_line = ""
    if city_bits[0] or city_bits[1] or city_bits[2]:
        city_line = f"{city_bits[0]}, {city_bits[1]} {city_bits[2]}".strip().strip(",")
    if city_line:
        address_lines.append(city_line)

    left_lines = [full_name] if full_name else []
    left_lines.extend(address_lines)
    left_html = "<br/>".join([escape(ln) for ln in left_lines if ln]) or "&nbsp;"
    date_html = escape(formal_date or format_long_date(date.today()))
    header_table = Table([[Paragraph(left_html, address_style), Paragraph(date_html, right_date_style)]], colWidths=[3.95 * inch, 2.05 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    bureau_lines = BUREAU_DISPUTE_ADDRESSES.get((bureau or "").lower(), [])
    for line in bureau_lines:
        story.append(Paragraph(escape(line), address_style))
    if bureau_lines:
        story.append(Spacer(1, 12))

    story.append(Paragraph(f"Re: {escape(_build_reference_line(primary_doc, bureau))}", subject_style))
    story.append(Paragraph("To Whom It May Concern,", salutation_style))

    paragraphs = _extract_letter_body_paragraphs(letter_text)
    if not paragraphs:
        paragraphs = ["I am writing to dispute inaccurate information appearing on my credit file and to request a reasonable reinvestigation of the items listed below."]
    for para in paragraphs:
        story.append(Paragraph(escape(para), body_style))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Sincerely,", closing_style))
    if include_signature:
        sig = _fit_signature(signature_path or "")
        if sig is not None:
            story.append(Spacer(1, 6))
            story.append(sig)
            story.append(Spacer(1, 4))

    if full_name:
        story.append(Paragraph(escape(full_name), signature_name_style))
    dob_display = format_short_date(profile.get("date_of_birth"))
    if dob_display:
        story.append(Paragraph(f"DOB: {escape(dob_display)}", signature_meta_style))
    ssn_full = (profile.get("ssn_full") or "").strip()
    if ssn_full:
        story.append(Paragraph(f"SSN: {escape(ssn_full)}", signature_meta_style))

    doc.build(story)


def generate_and_attach_pdf(cur, letter_id: str):
    cur.execute("""
        SELECT l.letter_text,
               l.subject,
               l.bureau::text,
               l.generated_at,
               l.client_id,
               l.round_run_id::text,
               c.first_name,
               c.middle_name,
               c.last_name,
               COALESCE(c.suffix, '') AS suffix,
               c.date_of_birth,
               c.ssn_full_enc,
               COALESCE(addr.line1, '') AS address_line1,
               COALESCE(addr.apt_unit, '') AS apt_unit,
               COALESCE(addr.line2, '') AS address_line2,
               COALESCE(addr.city, '') AS city,
               COALESCE(addr.state, '') AS state,
               COALESCE(addr.zip, '') AS zip,
               COALESCE(c.signature_file_path, '') AS signature_file_path,
               COALESCE(l.use_client_signature, COALESCE(c.use_signature_on_letters, FALSE)) AS use_client_signature
        FROM letters l
        JOIN clients c ON c.id = l.client_id
        LEFT JOIN LATERAL (
            SELECT a.line1, COALESCE(a.apt_unit, '') AS apt_unit, a.line2, a.city, a.state, a.zip
            FROM client_addresses a
            WHERE a.client_id = c.id AND a.is_current = TRUE
            ORDER BY a.created_at DESC
            LIMIT 1
        ) addr ON TRUE
        WHERE l.id = %s
    """, (letter_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Letter not found: {letter_id}")

    (letter_text, subject, bureau, generated_at, client_id, round_run_id, first_name, middle_name, last_name, suffix,
     date_of_birth, ssn_full_enc, address_line1, apt_unit, address_line2, city, state, zip_code,
     signature_file_path, use_client_signature) = row

    safe_name = f"{last_name}_{first_name}_{bureau}_{generated_at:%Y%m%d_%H%M%S}".replace(" ", "_")
    file_name = f"{safe_name}.pdf"
    out_path = os.path.join(OUTPUT_DIR, file_name)

    client_profile = {
        "first_name": first_name,
        "middle_name": middle_name or "",
        "last_name": last_name,
        "suffix": suffix or "",
        "date_of_birth": date_of_birth,
        "ssn_full": dec_text(ssn_full_enc),
        "address_line1": address_line1 or "",
        "apt_unit": apt_unit or "",
        "address_line2": address_line2 or "",
        "city": city or "",
        "state": state or "",
        "zip": zip_code or "",
    }
    primary_doc = _fetch_selected_primary_document(cur, round_run_id, str(client_id), bureau)

    make_pdf(
        letter_text,
        out_path,
        client_profile=client_profile,
        formal_date=format_long_date(generated_at),
        signature_path=signature_file_path,
        include_signature=bool(use_client_signature),
        bureau=bureau or "",
        primary_doc=primary_doc,
    )

    try:
        cur.execute("SELECT attach_letter_pdf(%s, %s, %s, %s)", (letter_id, file_name, out_path, subject))
        status = "attached"
    except psycopg2.errors.UniqueViolation:
        status = "already_attached"

    return {
        "letter_id": letter_id,
        "client_id": str(client_id),
        "bureau": bureau,
        "file_name": file_name,
        "file_path": out_path,
        "status": status,
    }


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
               COALESCE(doc_section, '') AS doc_section,
               COALESCE(file_name, '') AS file_name,
               COALESCE(file_path, '') AS file_path,
               COALESCE(description, '') AS description,
               COALESCE(bureau, '') AS bureau,
               COALESCE(source_name, '') AS source_name,
               document_date,
               COALESCE(reference_label, '') AS reference_label,
               COALESCE(reference_value, '') AS reference_value,
               statement_date,
               refresh_every_days,
               remind_on,
               expires_on,
               COALESCE(review_status::text, 'pending') AS review_status,
               COALESCE(review_notes, '') AS review_notes,
               created_at
        FROM client_documents
        WHERE client_id = %s
        ORDER BY COALESCE(document_date, statement_date) DESC NULLS LAST, created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    out = []
    for r in rows:
        section_key = _normalize_document_section(r[3], r[2])
        ref_label = (r[10] or _default_reference_label_for_bureau(r[7])).strip() if r[11] else (r[10] or '').strip()
        out.append({
            "id": r[0],
            "doc_type": r[1],
            "doc_category": r[2],
            "doc_section": section_key,
            "doc_section_label": DOCUMENT_SECTION_LABELS.get(section_key, section_key.replace('_', ' ').title()),
            "file_name": r[4],
            "file_path": r[5],
            "description": r[6],
            "bureau": r[7],
            "source_name": r[8],
            "document_date": str(r[9]) if r[9] else '',
            "reference_label": ref_label,
            "reference_value": r[11] or '',
            "reference_display": (f"{ref_label}: {r[11]}" if r[11] else ""),
            "statement_date": str(r[12]) if r[12] else '',
            "refresh_every_days": r[13],
            "remind_on": str(r[14]) if r[14] else '',
            "expires_on": str(r[15]) if r[15] else '',
            "review_status": r[16],
            "review_notes": r[17],
            "created_at": str(r[18]) if r[18] else '',
            "can_open": bool(r[5]),
            "document_kind": _document_kind_label(section_key) if section_key in {"reports", "bureau_responses"} else "",
        })
    return out


def group_documents_by_section(documents):
    grouped = {key: [] for key, _ in DOCUMENT_SECTIONS}
    for doc in documents or []:
        grouped.setdefault(doc.get("doc_section") or "miscellaneous", []).append(doc)
    return grouped


def build_dispute_source_groups(documents):
    groups = {bureau: [] for bureau in BUREAUS}
    for doc in documents or []:
        if doc.get("doc_section") not in {"reports", "bureau_responses"}:
            continue
        bureau = (doc.get("bureau") or "").lower()
        if bureau not in groups:
            continue
        groups[bureau].append(doc)
    return groups


def fetch_last_document_selection(cur, client_id: str):
    cur.execute("""
        SELECT COALESCE(primary_experian_document_id::text, ''),
               COALESCE(primary_transunion_document_id::text, ''),
               COALESCE(primary_equifax_document_id::text, '')
        FROM round_run_dispute_meta
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (client_id,))
    row = cur.fetchone()
    return {
        "experian": row[0] if row else "",
        "transunion": row[1] if row else "",
        "equifax": row[2] if row else "",
    }


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


def fetch_account_disputes(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text,
               COALESCE(bureau, ''),
               COALESCE(creditor_name, ''),
               COALESCE(account_number_last4, ''),
               COALESCE(dispute_reason, ''),
               COALESCE(requested_action, ''),
               COALESCE(status, 'open'),
               COALESCE(notes, ''),
               COALESCE(is_active, TRUE),
               first_seen_date,
               removed_date,
               COALESCE(round_added, 1),
               last_included_round,
               removed_round,
               created_at
        FROM client_account_disputes
        WHERE client_id = %s
        ORDER BY COALESCE(is_active, TRUE) DESC,
                 lower(COALESCE(creditor_name,'')),
                 created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "creditor_name": r[2], "account_number_last4": r[3],
        "dispute_reason": r[4], "requested_action": r[5], "status": r[6], "notes": r[7],
        "is_active": bool(r[8]), "first_seen_date": str(r[9]) if r[9] else '',
        "removed_date": str(r[10]) if r[10] else '', "round_added": r[11] or 1,
        "last_included_round": r[12], "removed_round": r[13], "created_at": str(r[14]) if r[14] else ''
    } for r in rows]


def fetch_personal_info_disputes(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text,
               COALESCE(bureau, ''),
               COALESCE(info_type, ''),
               COALESCE(reported_value, ''),
               COALESCE(correct_value, ''),
               COALESCE(requested_action, ''),
               COALESCE(notes, ''),
               COALESCE(is_active, TRUE),
               created_at,
               removed_date,
               COALESCE(round_added, 1),
               last_included_round,
               removed_round
        FROM client_personal_info_disputes
        WHERE client_id = %s
        ORDER BY COALESCE(is_active, TRUE) DESC,
                 lower(COALESCE(info_type,'')),
                 created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "info_type": r[2], "reported_value": r[3],
        "correct_value": r[4], "requested_action": r[5], "notes": r[6],
        "is_active": bool(r[7]), "created_at": str(r[8]) if r[8] else '',
        "removed_date": str(r[9]) if r[9] else '', "round_added": r[10] or 1,
        "last_included_round": r[11], "removed_round": r[12]
    } for r in rows]


def fetch_inquiry_disputes(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text,
               COALESCE(bureau, ''),
               COALESCE(furnisher_name, ''),
               inquiry_date,
               COALESCE(dispute_reason, ''),
               COALESCE(requested_action, 'delete'),
               COALESCE(notes, ''),
               COALESCE(is_active, TRUE),
               created_at,
               removed_date,
               COALESCE(round_added, 1),
               last_included_round,
               removed_round
        FROM client_inquiry_disputes
        WHERE client_id = %s
        ORDER BY COALESCE(is_active, TRUE) DESC,
                 inquiry_date DESC NULLS LAST,
                 lower(COALESCE(furnisher_name,'')),
                 created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        "id": r[0], "bureau": r[1], "furnisher_name": r[2],
        "inquiry_date": str(r[3]) if r[3] else '', "dispute_reason": r[4],
        "requested_action": r[5], "notes": r[6], "is_active": bool(r[7]),
        "created_at": str(r[8]) if r[8] else '', "removed_date": str(r[9]) if r[9] else '',
        "round_added": r[10] or 1, "last_included_round": r[11], "removed_round": r[12]
    } for r in rows]


def fetch_account_name_options(cur, client_id: str, limit: int = 300):
    cur.execute("""
        SELECT DISTINCT creditor_name
        FROM client_account_disputes
        WHERE client_id = %s
          AND NULLIF(TRIM(COALESCE(creditor_name, '')), '') IS NOT NULL
        ORDER BY creditor_name
        LIMIT %s
    """, (client_id, limit))
    return [r[0] for r in cur.fetchall()]


def infer_round_number(*values):
    for value in values:
        if value is None:
            continue
        match = re.search(r"round\s*(\d+)", str(value), flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    return None


def fetch_saved_letters(cur, client_id: str, limit: int = 100):
    cur.execute("""
        SELECT l.id::text,
               COALESCE(meta.round_number, NULL),
               l.bureau::text,
               COALESCE(l.subject, ''),
               l.generated_at,
               COALESCE(meta.include_accounts, TRUE),
               COALESCE(meta.include_personal_info, FALSE),
               COALESCE(meta.include_inquiries, FALSE),
               COALESCE(meta.letter_instructions, ''),
               COALESCE(l.round_run_id::text, '')
        FROM letters l
        LEFT JOIN round_run_dispute_meta meta ON meta.round_run_id = l.round_run_id
        WHERE l.client_id = %s
        ORDER BY l.generated_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    out = []
    for r in rows:
        round_number = r[1] or infer_round_number(r[3])
        categories = []
        if r[5]:
            categories.append('Accounts')
        if r[6]:
            categories.append('Personal Info')
        if r[7]:
            categories.append('Inquiries')
        category_label = ' + '.join(categories) if categories else 'Accounts'
        bureau_label = (r[2] or '').title()
        if round_number:
            title = f"Dispute Letter - Round {round_number} - {bureau_label} - {category_label}"
        else:
            title = r[3] or f"Dispute Letter - {bureau_label} - {category_label}"

        raw_subject = r[3] or ''
        generic_subject = (not raw_subject.strip()
            or bool(re.fullmatch(r"\s*Dispute Letter\s*-\s*Round\s*\d+\s*", raw_subject, flags=re.IGNORECASE))
            or bool(re.fullmatch(r"\s*Round\s*\d+\s*dispute\s*letter\s*", raw_subject, flags=re.IGNORECASE)))
        display_subject = title if generic_subject else raw_subject

        out.append({
            'id': r[0], 'round_number': round_number, 'bureau': r[2], 'subject': display_subject,
            'raw_subject': raw_subject, 'generated_at': str(r[4]) if r[4] else '', 'include_accounts': bool(r[5]),
            'include_personal_info': bool(r[6]), 'include_inquiries': bool(r[7]),
            'letter_instructions': r[8], 'round_run_id': r[9], 'title': title,
        })
    return out


def fetch_client_emails(cur, client_id: str, limit: int = 200):
    cur.execute("""
        SELECT id::text,
               COALESCE(direction, 'outbound'),
               COALESCE(subject, ''),
               COALESCE(body_text, ''),
               COALESCE(from_email, ''),
               COALESCE(to_email, ''),
               COALESCE(cc_email, ''),
               email_date,
               related_round,
               COALESCE(email_type, 'general'),
               COALESCE(status, 'logged'),
               COALESCE(source, 'manual'),
               created_at
        FROM client_emails
        WHERE client_id = %s
        ORDER BY COALESCE(email_date, created_at) DESC, created_at DESC
        LIMIT %s
    """, (client_id, limit))
    rows = cur.fetchall()
    return [{
        'id': r[0], 'direction': r[1], 'subject': r[2], 'body_text': r[3],
        'from_email': r[4], 'to_email': r[5], 'cc_email': r[6],
        'email_date': str(r[7]) if r[7] else '', 'related_round': r[8],
        'email_type': r[9], 'status': r[10], 'source': r[11],
        'created_at': str(r[12]) if r[12] else ''
    } for r in rows]




def fetch_dispute_round_defaults(cur, client_id: str):
    round_number = 1
    try:
        cur.execute("SELECT COALESCE(MAX(round_number), 0) FROM round_runs WHERE client_id = %s", (client_id,))
        row = cur.fetchone()
        if row and row[0]:
            round_number = int(row[0]) + 1
    except Exception:
        conn = getattr(cur, 'connection', None)
        if conn is not None:
            conn.rollback()
        cur.execute("SELECT COALESCE(MAX(round_number), 0) FROM round_run_dispute_meta WHERE client_id = %s", (client_id,))
        row = cur.fetchone()
        if row and row[0]:
            round_number = int(row[0]) + 1
    cur.execute("""
        SELECT COALESCE(letter_instructions, '')
        FROM round_run_dispute_meta
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (client_id,))
    row = cur.fetchone()
    return {
        'current_round_number': round_number,
        'last_letter_instructions': row[0] if row and row[0] else ''
    }


def fetch_dispute_metrics(cur, client_id: str):
    metrics = {}
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) = 1) AS started_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE) AS current_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE) AS removed_count,
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) > 1 AND COALESCE(is_active, TRUE) = TRUE) AS new_count
        FROM client_account_disputes
        WHERE client_id = %s
    """, (client_id,))
    row = cur.fetchone() or (0, 0, 0, 0)
    metrics['accounts'] = {'started': int(row[0] or 0), 'current': int(row[1] or 0), 'removed': int(row[2] or 0), 'new': int(row[3] or 0)}

    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) = 1) AS started_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE) AS current_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE) AS removed_count,
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) > 1 AND COALESCE(is_active, TRUE) = TRUE) AS new_count
        FROM client_personal_info_disputes
        WHERE client_id = %s
    """, (client_id,))
    row = cur.fetchone() or (0, 0, 0, 0)
    metrics['personal_info'] = {'started': int(row[0] or 0), 'current': int(row[1] or 0), 'removed': int(row[2] or 0), 'new': int(row[3] or 0)}

    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) = 1) AS started_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE) AS current_count,
            COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE) AS removed_count,
            COUNT(*) FILTER (WHERE COALESCE(round_added, 1) > 1 AND COALESCE(is_active, TRUE) = TRUE) AS new_count
        FROM client_inquiry_disputes
        WHERE client_id = %s
    """, (client_id,))
    row = cur.fetchone() or (0, 0, 0, 0)
    metrics['inquiries'] = {'started': int(row[0] or 0), 'current': int(row[1] or 0), 'removed': int(row[2] or 0), 'new': int(row[3] or 0)}
    return metrics


def build_dispute_round_summary(cur, client_id: str, round_number: int, include_personal_info: bool, include_inquiries: bool):
    metrics = fetch_dispute_metrics(cur, client_id)
    parts = [
        f"Round {round_number} processed.",
        f"Accounts: started {metrics['accounts']['started']}, current {metrics['accounts']['current']}, removed {metrics['accounts']['removed']}, new since round 1 {metrics['accounts']['new']}."
    ]
    if include_personal_info:
        parts.append(
            f"Personal info: started {metrics['personal_info']['started']}, current {metrics['personal_info']['current']}, removed {metrics['personal_info']['removed']}, new since round 1 {metrics['personal_info']['new']}."
        )
    if include_inquiries:
        parts.append(
            f"Inquiries tracked: started {metrics['inquiries']['started']}, current {metrics['inquiries']['current']}, removed {metrics['inquiries']['removed']}, new since round 1 {metrics['inquiries']['new']}."
        )
        parts.append("Note: inquiry disputes are tracked in the CRM in this build, but the legacy generator does not yet merge them into the generated letters.")
    return ' '.join(parts)


# backward-compatible aliases used elsewhere in the Phase 3 code/template

def fetch_negative_items(cur, client_id: str, limit: int = 200):
    return fetch_account_disputes(cur, client_id, limit)


def fetch_personal_info_errors(cur, client_id: str, limit: int = 200):
    return fetch_personal_info_disputes(cur, client_id, limit)


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
               COALESCE(c.signature_file_name, '') AS signature_file_name,
               COALESCE(c.signature_file_path, '') AS signature_file_path,
               COALESCE(c.use_signature_on_letters, FALSE) AS use_signature_on_letters,
               c.signature_uploaded_at,
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
    dob_raw = profile.get("date_of_birth")
    profile["date_of_birth_display"] = format_short_date(dob_raw)
    profile["date_of_birth_input"] = format_short_date(dob_raw)
    for field in ("start_date", "cancelled_date", "signup_date", "first_payment_date", "signature_uploaded_at"):
        if field in profile and profile.get(field):
            if field == "signature_uploaded_at":
                profile[field + "_display"] = str(profile.get(field))
            else:
                profile[field] = str(profile.get(field))
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
    documents_by_section = group_documents_by_section(documents)
    dispute_source_groups = build_dispute_source_groups(documents)
    last_document_selection = fetch_last_document_selection(cur, client_id)
    upload_requests = fetch_upload_requests(cur, client_id, 50)
    referral_partners = fetch_referral_partners(cur)
    account_disputes = fetch_account_disputes(cur, client_id, 200)
    personal_info_disputes = fetch_personal_info_disputes(cur, client_id, 200)
    inquiry_disputes = fetch_inquiry_disputes(cur, client_id, 200)
    dispute_metrics = fetch_dispute_metrics(cur, client_id)
    dispute_round_defaults = fetch_dispute_round_defaults(cur, client_id)
    saved_letters = fetch_saved_letters(cur, client_id, 100)
    client_emails = fetch_client_emails(cur, client_id, 200)
    account_name_options = fetch_account_name_options(cur, client_id, 300)
    credit_products = fetch_credit_products(cur, client_id, 200)
    outbound_referrals = fetch_outbound_referrals(cur, client_id, 200)

    return (
        dashboard, profile, score_groups, credentials, notes, appointments, followups,
        redispute_events, documents, documents_by_section, dispute_source_groups,
        last_document_selection, upload_requests, referral_partners, account_disputes,
        personal_info_disputes, inquiry_disputes, dispute_metrics, dispute_round_defaults,
        saved_letters, client_emails, account_name_options, credit_products, outbound_referrals
    )


def render_client_workspace(request: Request, client_id: str, message: str = "", error: str = "", reveal_cred_id: Optional[str] = None, active_tab: Optional[str] = None):
    tab_defs = [
        ("overview", "Overview"),
        ("profile", "Profile"),
        ("notes", "Notes"),
        ("emails", "Emails"),
        ("calendar", "Calendar"),
        ("credentials_scores", "Credentials & Scores"),
        ("disputes", "Disputes"),
        ("documents", "Documents"),
        ("credit_products", "Credit Products"),
        ("referrals", "Referrals"),
    ]
    valid_tabs = {k for k, _ in tab_defs}
    chosen_tab = active_tab or request.query_params.get("tab") or "overview"
    if chosen_tab == "personal_info":
        chosen_tab = "disputes"
    if chosen_tab not in valid_tabs:
        chosen_tab = "overview"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            (
                dashboard, profile, score_groups, credentials, notes, appointments, followups,
                redispute_events, documents, documents_by_section, dispute_source_groups,
                last_document_selection, upload_requests, referral_partners, account_disputes,
                personal_info_disputes, inquiry_disputes, dispute_metrics, dispute_round_defaults,
                saved_letters, client_emails, account_name_options, credit_products, outbound_referrals
            ) = load_client_workspace_context(cur, client_id, reveal_cred_id)
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
                "documents_by_section": documents_by_section,
                "dispute_source_groups": dispute_source_groups,
                "last_document_selection": last_document_selection,
                "upload_requests": upload_requests,
                "referral_partners": referral_partners,
                "account_disputes": account_disputes,
                "personal_info_disputes": personal_info_disputes,
                "inquiry_disputes": inquiry_disputes,
                "dispute_metrics": dispute_metrics,
                "dispute_round_defaults": dispute_round_defaults,
                "saved_letters": saved_letters,
                "client_emails": client_emails,
                "account_name_options": account_name_options,
                "credit_products": credit_products,
                "outbound_referrals": outbound_referrals,
                "providers": PROVIDERS,
                "bureaus": BUREAUS,
                "dispute_reasons": DISPUTE_REASONS,
                "negative_item_statuses": NEGATIVE_ITEM_STATUSES,
                "personal_error_types": PERSONAL_ERROR_TYPES,
                "personal_error_statuses": PERSONAL_ERROR_STATUSES,
                "requested_actions": REQUESTED_ACTIONS,
                "inquiry_dispute_reasons": INQUIRY_DISPUTE_REASONS,
                "inquiry_requested_actions": INQUIRY_REQUESTED_ACTIONS,
                "credit_types": CREDIT_TYPES,
                "outbound_referral_statuses": OUTBOUND_REFERRAL_STATUSES,
                "referral_partner_types": REFERRAL_PARTNER_TYPES,
                "followup_types": FOLLOWUP_TYPES,
                "followup_statuses": FOLLOWUP_STATUSES,
                "document_categories": DOCUMENT_CATEGORIES,
                "document_sections": DOCUMENT_SECTIONS,
                "doc_review_statuses": DOC_REVIEW_STATUSES,
                "email_directions": EMAIL_DIRECTIONS,
                "email_statuses": EMAIL_STATUSES,
                "email_types": EMAIL_TYPES,
                "email_sources": EMAIL_SOURCES,
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


@app.get("/ui/letters/{letter_id}/open")
def ui_open_letter(letter_id: str):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            pdf_info = generate_and_attach_pdf(cur, letter_id)
        conn.close()
        file_path = pdf_info.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="PDF file not found.")
        return FileResponse(
            file_path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"'}
        )
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


@app.get("/ui/client/{client_id}/signature")
def ui_open_client_signature(client_id: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(signature_file_path, ''), COALESCE(signature_file_name, '') FROM clients WHERE id = %s", (client_id,))
            row = cur.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="No signature on file.")
        file_path, file_name = row
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Signature file not found on disk.")
        ext = os.path.splitext(file_name or file_path)[1].lower()
        media_type = "image/png" if ext == ".png" else "image/jpeg"
        return FileResponse(file_path, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"'})
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
    use_signature_on_letters: bool = Form(False),
    remove_signature: bool = Form(False),
    signature_file: UploadFile = File(None),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            ssn_last4 = ssn_full[-4:] if ssn_full and len(ssn_full) >= 4 else None
            ssn_full_enc = enc_text(ssn_full)
            dob_iso = parse_date_input(date_of_birth) if date_of_birth else ""

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
                    use_signature_on_letters = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                first_name, middle_name, last_name, suffix,
                primary_phone, primary_phone_type,
                secondary_phone, secondary_phone_type,
                primary_email, primary_email,
                secondary_email, preferred_email_choice, send_to_both_emails,
                dob_iso, ssn_last4, ssn_full_enc,
                is_active, lifecycle_status, pending_payment,
                start_date, cancelled_date,
                consultation_fee, initial_fee, monthly_fee, pricing_plan,
                signup_date, first_payment_date,
                billing_group, custom_billing_day, preferred_payment_method,
                referred_by_partner_id, referral_reason, use_signature_on_letters, client_id
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

            if remove_signature:
                cur.execute("""
                    UPDATE clients
                    SET signature_file_name = NULL,
                        signature_file_path = NULL,
                        signature_uploaded_at = NULL
                    WHERE id = %s
                """, (client_id,))

            if signature_file is not None and getattr(signature_file, "filename", ""):
                ext = os.path.splitext(signature_file.filename)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg"}:
                    raise ValueError("Signature file must be PNG or JPG.")
                target_dir = os.path.join(SIGNATURE_DIR, client_id)
                os.makedirs(target_dir, exist_ok=True)
                safe_name = f"signature_{uuid4().hex}{ext}"
                save_path = os.path.join(target_dir, safe_name)
                content = signature_file.file.read()
                with open(save_path, "wb") as fh:
                    fh.write(content)
                cur.execute("""
                    UPDATE clients
                    SET signature_file_name = %s,
                        signature_file_path = %s,
                        signature_uploaded_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (signature_file.filename, save_path, client_id))

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


@app.post("/ui/add-account-dispute", response_class=HTMLResponse)
def ui_add_account_dispute(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(...),
    creditor_name: str = Form(...),
    account_number_last4: str = Form(""),
    dispute_reason: str = Form(...),
    requested_action: str = Form("investigate"),
    status: str = Form("disputed"),
    first_seen_date: str = Form(""),
    round_added: int = Form(1),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_account_disputes
                  (client_id, creditor_name, account_number_last4, dispute_reason, requested_action, notes,
                   is_active, bureau, status, first_seen_date, removed_date, is_negative, round_added, last_included_round)
                VALUES
                  (%s, %s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), TRUE, %s, %s, NULLIF(%s,'')::date, NULL, TRUE, %s, %s)
            """, (client_id, creditor_name, account_number_last4, dispute_reason, requested_action, notes, bureau, status, first_seen_date, round_added, round_added))
        conn.close()
        return render_client_workspace(request, client_id, message="Account dispute saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/mark-account-dispute-removed", response_class=HTMLResponse)
def ui_mark_account_dispute_removed(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    removed_round: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_account_disputes
                SET is_active = FALSE,
                    status = 'removed',
                    removed_date = CURRENT_DATE,
                    removed_round = %s
                WHERE id = %s AND client_id = %s
            """, (removed_round, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Account dispute moved to removed list.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/reactivate-account-dispute", response_class=HTMLResponse)
def ui_reactivate_account_dispute(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    round_added: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_account_disputes
                SET is_active = TRUE,
                    status = 'disputed',
                    removed_date = NULL,
                    removed_round = NULL,
                    round_added = COALESCE(round_added, %s)
                WHERE id = %s AND client_id = %s
            """, (round_added, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Account dispute reactivated.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-account-dispute", response_class=HTMLResponse)
def ui_delete_account_dispute(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("disputes")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_account_disputes WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Account dispute deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/add-personal-info-dispute", response_class=HTMLResponse)
def ui_add_personal_info_dispute(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(""),
    info_type: str = Form(...),
    reported_value: str = Form(...),
    correct_value: str = Form(""),
    requested_action: str = Form("delete"),
    round_added: int = Form(1),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_personal_info_disputes
                  (client_id, bureau, info_type, reported_value, correct_value, requested_action,
                   notes, is_active, round_added, last_included_round, removed_date, removed_round)
                VALUES
                  (%s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), %s, NULLIF(%s,''), TRUE, %s, %s, NULL, NULL)
            """, (client_id, bureau, info_type, reported_value, correct_value, requested_action, notes, round_added, round_added))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal info dispute saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/mark-personal-info-dispute-removed", response_class=HTMLResponse)
def ui_mark_personal_info_dispute_removed(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    removed_round: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_personal_info_disputes
                SET is_active = FALSE,
                    removed_date = CURRENT_DATE,
                    removed_round = %s
                WHERE id = %s AND client_id = %s
            """, (removed_round, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal info dispute moved to removed list.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/reactivate-personal-info-dispute", response_class=HTMLResponse)
def ui_reactivate_personal_info_dispute(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    round_added: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_personal_info_disputes
                SET is_active = TRUE,
                    removed_date = NULL,
                    removed_round = NULL,
                    round_added = COALESCE(round_added, %s)
                WHERE id = %s AND client_id = %s
            """, (round_added, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal info dispute reactivated.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-personal-info-dispute", response_class=HTMLResponse)
def ui_delete_personal_info_dispute(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("disputes")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_personal_info_disputes WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Personal info dispute deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/add-inquiry-dispute", response_class=HTMLResponse)
def ui_add_inquiry_dispute(
    request: Request,
    client_id: str = Form(...),
    bureau: str = Form(""),
    furnisher_name: str = Form(...),
    inquiry_date: str = Form(""),
    dispute_reason: str = Form("not my inquiry"),
    requested_action: str = Form("delete"),
    round_added: int = Form(1),
    notes: str = Form(""),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_inquiry_disputes
                  (client_id, bureau, furnisher_name, inquiry_date, dispute_reason, requested_action,
                   notes, is_active, round_added, last_included_round, removed_date, removed_round)
                VALUES
                  (%s, NULLIF(%s,''), %s, NULLIF(%s,'')::date, %s, %s, NULLIF(%s,''), TRUE, %s, %s, NULL, NULL)
            """, (client_id, bureau, furnisher_name, inquiry_date, dispute_reason, requested_action, notes, round_added, round_added))
        conn.close()
        return render_client_workspace(request, client_id, message="Inquiry dispute saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/mark-inquiry-dispute-removed", response_class=HTMLResponse)
def ui_mark_inquiry_dispute_removed(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    removed_round: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_inquiry_disputes
                SET is_active = FALSE,
                    removed_date = CURRENT_DATE,
                    removed_round = %s
                WHERE id = %s AND client_id = %s
            """, (removed_round, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Inquiry dispute moved to removed list.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/reactivate-inquiry-dispute", response_class=HTMLResponse)
def ui_reactivate_inquiry_dispute(
    request: Request,
    client_id: str = Form(...),
    item_id: str = Form(...),
    round_added: int = Form(1),
    active_tab: str = Form("disputes"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE client_inquiry_disputes
                SET is_active = TRUE,
                    removed_date = NULL,
                    removed_round = NULL,
                    round_added = COALESCE(round_added, %s)
                WHERE id = %s AND client_id = %s
            """, (round_added, item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Inquiry dispute reactivated.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-inquiry-dispute", response_class=HTMLResponse)
def ui_delete_inquiry_dispute(request: Request, client_id: str = Form(...), item_id: str = Form(...), active_tab: str = Form("disputes")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_inquiry_disputes WHERE id = %s AND client_id = %s", (item_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Inquiry dispute deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


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
        return templates.TemplateResponse("upload.html", {"request": request, "token": token, "upload_request": req, "document_categories": DOCUMENT_CATEGORIES, "document_sections": DOCUMENT_SECTIONS, "bureaus": BUREAUS})
    finally:
        conn.close()


@app.post("/upload/{token}", response_class=HTMLResponse)
def upload_submit(
    request: Request,
    token: str,
    doc_section: str = Form("miscellaneous"),
    doc_category: str = Form("miscellaneous"),
    description: str = Form(""),
    bureau: str = Form(""),
    source_name: str = Form(""),
    document_date: str = Form(""),
    reference_label: str = Form(""),
    reference_value: str = Form(""),
    statement_date: str = Form(""),
    expires_on: str = Form(""),
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

            client_dir = os.path.join(UPLOAD_DIR, client_id, "documents")
            os.makedirs(client_dir, exist_ok=True)
            safe_name = f"{uuid4()}_{os.path.basename(file.filename)}"
            save_path = os.path.join(client_dir, safe_name)
            with open(save_path, 'wb') as f:
                f.write(file.file.read())

            normalized_section = _normalize_document_section(doc_section, doc_category)
            stored_ref_label = (reference_label or _default_reference_label_for_bureau(bureau)).strip() if (reference_value or "").strip() else (reference_label or "").strip()

            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, doc_section, bureau, source_name,
                   document_date, reference_label, reference_value, statement_date, expires_on, refresh_every_days,
                   review_status, upload_request_id, created_at)
                VALUES
                  (%s, 'client_upload', %s, %s, NULLIF(%s,''), NULLIF(%s,''), %s, NULLIF(%s,''), NULLIF(%s,''),
                   NULLIF(%s,'')::date, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date,
                   NULLIF(%s,'')::smallint, 'pending', %s::uuid, CURRENT_TIMESTAMP)
            """, (
                client_id, file.filename, save_path, description, doc_category, normalized_section, bureau, source_name,
                document_date, stored_ref_label, reference_value, statement_date, expires_on, refresh_every_days,
                upload_request_id,
            ))

            cur.execute("UPDATE client_upload_requests SET used_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (upload_request_id,))

        return HTMLResponse("<h3>Thank you. Your document was uploaded successfully.</h3>")
    finally:
        conn.close()


@app.post("/ui/add-document-meta", response_class=HTMLResponse)
def ui_add_document_meta(
    request: Request,
    client_id: str = Form(...),
    doc_section: str = Form("miscellaneous"),
    doc_category: str = Form("miscellaneous"),
    file_name: str = Form(""),
    file_path: str = Form(""),
    description: str = Form(""),
    bureau: str = Form(""),
    source_name: str = Form(""),
    document_date: str = Form(""),
    reference_label: str = Form(""),
    reference_value: str = Form(""),
    statement_date: str = Form(""),
    expires_on: str = Form(""),
    refresh_every_days: str = Form(""),
    review_status: str = Form("pending"),
    review_notes: str = Form(""),
    active_tab: str = Form("documents"),
    file: UploadFile = File(None),
):
    try:
        normalized_section = _normalize_document_section(doc_section, doc_category)
        actual_file_name = (file_name or "").strip()
        stored_path = (file_path or "").strip()
        doc_type = 'manual_log'

        if file is not None and getattr(file, "filename", ""):
            client_dir = os.path.join(UPLOAD_DIR, client_id, "documents")
            os.makedirs(client_dir, exist_ok=True)
            safe_name = f"{uuid4()}_{os.path.basename(file.filename)}"
            stored_path = os.path.join(client_dir, safe_name)
            with open(stored_path, 'wb') as fh:
                fh.write(file.file.read())
            actual_file_name = actual_file_name or file.filename
            doc_type = 'client_upload'

        if not actual_file_name:
            raise ValueError("Attach a file or provide a file name.")

        stored_ref_label = (reference_label or _default_reference_label_for_bureau(bureau)).strip() if (reference_value or "").strip() else (reference_label or "").strip()

        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, doc_section, bureau, source_name,
                   document_date, reference_label, reference_value, statement_date, expires_on, refresh_every_days,
                   review_status, review_notes, created_at)
                VALUES
                  (%s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), %s, NULLIF(%s,''), NULLIF(%s,''),
                   NULLIF(%s,'')::date, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date,
                   NULLIF(%s,'')::smallint, %s, NULLIF(%s,''), CURRENT_TIMESTAMP)
            """, (
                client_id, doc_type, actual_file_name, stored_path, description, doc_category, normalized_section, bureau, source_name,
                document_date, stored_ref_label, reference_value, statement_date, expires_on, refresh_every_days,
                review_status, review_notes,
            ))
        conn.close()
        return render_client_workspace(request, client_id, message="Document saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.get("/ui/documents/{document_id}/open")
def ui_open_document(document_id: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(file_path,''), COALESCE(file_name,'') FROM client_documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="Document file not found.")
        file_path, file_name = row
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Document file missing from disk.")
        media_type = mimetypes.guess_type(file_name or file_path)[0] or "application/octet-stream"
        return FileResponse(file_path, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"'})
    finally:
        conn.close()


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


@app.post("/ui/save-client-email", response_class=HTMLResponse)
def ui_save_client_email(
    request: Request,
    client_id: str = Form(...),
    direction: str = Form("outbound"),
    subject: str = Form(""),
    body_text: str = Form(""),
    from_email: str = Form(""),
    to_email: str = Form(""),
    cc_email: str = Form(""),
    email_date: str = Form(""),
    related_round: Optional[int] = Form(None),
    email_type: str = Form("general"),
    status: str = Form("logged"),
    source: str = Form("manual"),
    active_tab: str = Form("emails"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_emails
                  (client_id, direction, subject, body_text, from_email, to_email, cc_email, email_date, related_round, email_type, status, source, created_by)
                VALUES
                  (%s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::timestamp, %s, %s, %s, %s, 'manual_ui')
            """, (client_id, direction, subject, body_text, from_email, to_email, cc_email, email_date, related_round, email_type, status, source))
        conn.close()
        return render_client_workspace(request, client_id, message="Client email saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-client-email", response_class=HTMLResponse)
def ui_delete_client_email(request: Request, client_id: str = Form(...), email_id: str = Form(...), active_tab: str = Form("emails")):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_emails WHERE id = %s AND client_id = %s", (email_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Client email deleted.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)



@app.post("/ui/process-round-with-pdfs", response_class=HTMLResponse)
def ui_process_round_with_pdfs(
    request: Request,
    client_id: str = Form(...),
    round_number: int = Form(...),
    client_email: str = Form(...),
    include_personal_info: bool = Form(False),
    include_inquiries: bool = Form(False),
    include_signature: bool = Form(False),
    letter_instructions: str = Form(""),
    primary_experian_document_id: str = Form(""),
    primary_transunion_document_id: str = Form(""),
    primary_equifax_document_id: str = Form(""),
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

            cur.execute("""
                INSERT INTO round_run_dispute_meta
                  (round_run_id, client_id, round_number, include_accounts, include_personal_info, include_inquiries,
                   include_signature, letter_instructions, primary_experian_document_id, primary_transunion_document_id,
                   primary_equifax_document_id)
                VALUES
                  (%s, %s, %s, TRUE, %s, %s, %s, NULLIF(%s,''), NULLIF(%s,'')::uuid, NULLIF(%s,'')::uuid, NULLIF(%s,'')::uuid)
                ON CONFLICT (round_run_id) DO UPDATE
                SET round_number = EXCLUDED.round_number,
                    include_accounts = EXCLUDED.include_accounts,
                    include_personal_info = EXCLUDED.include_personal_info,
                    include_inquiries = EXCLUDED.include_inquiries,
                    include_signature = EXCLUDED.include_signature,
                    letter_instructions = EXCLUDED.letter_instructions,
                    primary_experian_document_id = EXCLUDED.primary_experian_document_id,
                    primary_transunion_document_id = EXCLUDED.primary_transunion_document_id,
                    primary_equifax_document_id = EXCLUDED.primary_equifax_document_id
            """, (run_id, client_id, round_number, include_personal_info, include_inquiries, include_signature,
                  letter_instructions, primary_experian_document_id, primary_transunion_document_id, primary_equifax_document_id))

            cur.execute("""
                UPDATE client_account_disputes
                SET last_included_round = %s
                WHERE client_id = %s AND COALESCE(is_active, TRUE) = TRUE
            """, (round_number, client_id))

            if include_personal_info:
                cur.execute("""
                    UPDATE client_personal_info_disputes
                    SET last_included_round = %s
                    WHERE client_id = %s AND COALESCE(is_active, TRUE) = TRUE
                """, (round_number, client_id))

            summary_note = build_dispute_round_summary(cur, client_id, round_number, include_personal_info, include_inquiries)
            if include_signature:
                summary_note += " Client signature included on generated letters."
            selected_refs = []
            for bureau_key, doc_id in (("Experian", primary_experian_document_id), ("TransUnion", primary_transunion_document_id), ("Equifax", primary_equifax_document_id)):
                if doc_id:
                    cur.execute("SELECT COALESCE(file_name,''), COALESCE(reference_label,''), COALESCE(reference_value,''), document_date FROM client_documents WHERE id = %s", (doc_id,))
                    doc_row = cur.fetchone()
                    if doc_row:
                        doc_desc = doc_row[0] or bureau_key
                        if doc_row[2]:
                            doc_desc += f" ({(doc_row[1] or 'Reference')}: {doc_row[2]})"
                        if doc_row[3]:
                            doc_desc += f" dated {format_short_date(doc_row[3])}"
                        selected_refs.append(f"{bureau_key}: {doc_desc}")
            if selected_refs:
                summary_note += " Source documents used: " + "; ".join(selected_refs) + "."
            if letter_instructions and letter_instructions.strip():
                summary_note += f" Letter instructions: {letter_instructions.strip()}"
            cur.execute("""
                INSERT INTO client_notes (client_id, note_text, note_type, created_by)
                VALUES (%s, %s, 'dispute_round_summary', 'system')
            """, (client_id, summary_note))

            cur.execute("SELECT id::text, bureau::text FROM letters WHERE round_run_id = %s ORDER BY bureau::text", (run_id,))
            letter_rows = cur.fetchall()
            pdf_results = []
            for lid, bureau in letter_rows:
                final_subject = f"{bureau.title()} Credit Report Dispute"
                cur.execute("UPDATE letters SET subject = %s, use_client_signature = %s WHERE id = %s", (final_subject, include_signature, lid))
                pdf_results.append(generate_and_attach_pdf(cur, lid))

            cur.execute("""
                INSERT INTO client_emails
                  (client_id, direction, subject, body_text, from_email, to_email, email_date, related_round, email_type, status, source, created_by)
                VALUES
                  (%s, 'outbound', %s, %s, %s, NULLIF(%s,''), NOW(), %s, 'dispute_update', 'logged', 'system', 'system')
            """, (client_id, f"Client Update - Dispute Round {round_number}", summary_note, SENDER_NAME, client_email, round_number))

        conn.close()

        return templates.TemplateResponse(
            "run_result.html",
            {
                "request": request,
                "client_id": client_id,
                "round_number": round_number,
                "round": round_json,
                "pdfs": pdf_results,
                "warning": "Inquiry disputes are tracked and logged in this build, but the legacy letter generator does not yet merge them into generated letters." if include_inquiries else ""
            }
        )

    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab='disputes')

