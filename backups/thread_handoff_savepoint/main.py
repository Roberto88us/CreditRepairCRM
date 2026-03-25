
import os
import json
import base64
import hashlib
import re
import mimetypes
from collections import defaultdict
from itertools import zip_longest
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, List
from uuid import uuid4
from xml.sax.saxutils import escape

import psycopg2
from psycopg2 import sql
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File, Query
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

PdfReader = None
PDF_READER_SOURCE = ""
for _pdf_import in (("pypdf", "PdfReader"), ("PyPDF2", "PdfReader")):
    try:
        _mod = __import__(_pdf_import[0], fromlist=[_pdf_import[1]])
        PdfReader = getattr(_mod, _pdf_import[1])
        PDF_READER_SOURCE = _pdf_import[0]
        break
    except Exception:
        continue

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
INVOICE_DIR = os.path.join(UPLOAD_DIR, "invoices")
COMPANY_PHONE = os.getenv("COMPANY_PHONE", "305-000-0000")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "info@cleanslateconsulting.com")
COMPANY_ADDRESS1 = os.getenv("COMPANY_ADDRESS1", "Miami, FL")
COMPANY_ADDRESS2 = os.getenv("COMPANY_ADDRESS2", "")
COMPANY_LOGO_PATH = os.getenv("COMPANY_LOGO_PATH", "")

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
CREDIT_TYPES = ["car loan", "credit card", "other", "personal loan", "secured credit card", "secured loan", "store card"]
OUTBOUND_REFERRAL_STATUSES = ["completed", "contacted", "declined", "referred"]
REFERRAL_PARTNER_TYPES = ["accountant", "ADP", "banker", "bankruptcy attorney", "client", "estate attorney", "insurance agent", "lender", "mortgage lender", "other", "realtor", "title company"]
FOLLOWUP_TYPES = ["call back", "document reminder", "general", "payment follow-up", "redispute", "review bureau response", "send docs", "waiting on IDs"]
FOLLOWUP_STATUSES = ["done", "open", "waiting"]

DOCUMENT_CATEGORIES = [
    "service_agreement",
    "credit_report",
    "bureau_response",
    "collection_company_mail",
    "driver_license_front",
    "driver_license_back",
    "state_id_front",
    "state_id_back",
    "passport",
    "voter_registration",
    "work_permit",
    "concealed_weapons_permit",
    "green_card",
    "foreign_passport",
    "utility_bill_electricity",
    "utility_bill_water",
    "utility_bill_gas",
    "utility_bill_cable",
    "bank_statement",
    "credit_card_offer",
    "personal_loan_offer",
    "car_loan_offer",
    "miscellaneous",
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
    "equifax": "Confirmation#",
}
DOCUMENT_SECTION_CATEGORY_OPTIONS = {
    "agreements": [
        ("service_agreement", "Service Agreement"),
        ("payment_authorization", "Payment Authorization"),
        ("disclosure", "Disclosure"),
        ("addendum", "Addendum"),
        ("cancellation_form", "Cancellation Form"),
    ],
    "reports": [("credit_report", "Credit Report")],
    "bureau_responses": [("bureau_response", "Bureau Response")],
    "collection_mail": [
        ("collection_company_mail", "Collection Company Mail"),
        ("validation_letter", "Validation Letter"),
        ("settlement_offer", "Settlement Offer"),
        ("attorney_letter", "Attorney Letter"),
    ],
    "ids_proof_of_address": [
        ("driver_license_front", "Driver's License Front"),
        ("driver_license_back", "Driver's License Back"),
        ("state_id_front", "ID Front"),
        ("state_id_back", "ID Back"),
        ("passport", "Passport"),
        ("voter_registration", "Voter's Registration"),
        ("work_permit", "Work Permit"),
        ("concealed_weapons_permit", "Concealed Weapons Permit"),
        ("green_card", "Green Card"),
        ("foreign_passport", "Foreign Passport"),
        ("government_issued_id", "Government Issued ID"),
        ("social_security_card", "Social Security Card"),
        ("utility_bill_electricity", "Utility Bill - Electricity"),
        ("utility_bill_water", "Utility Bill - Water"),
        ("utility_bill_gas", "Utility Bill - Gas"),
        ("utility_bill_cable", "Utility Bill - Cable"),
        ("bank_statement", "Bank Statement"),
    ],
    "credit_offers": [
        ("credit_card_offer", "Credit Card Offer"),
        ("personal_loan_offer", "Personal Loan Offer"),
        ("car_loan_offer", "Car Loan Offer"),
    ],
    "miscellaneous": [("miscellaneous", "Miscellaneous")],
}
DEFAULT_DOCUMENT_CATEGORY_BY_SECTION = {key: options[0][0] for key, options in DOCUMENT_SECTION_CATEGORY_OPTIONS.items()}
DOCUMENT_SOURCE_OPTIONS = [
    "Equifax Website",
    "Experian Website",
    "TransUnion Website",
    "AnnualCreditReport.com",
    "Credit Karma",
    "MyFICO.com",
]
UPLOAD_REQUEST_TYPE_OPTIONS = [
    ("id_proof_intake", "IDs & Proofs Intake"),
    ("general_upload", "General Upload"),
]
ID_DOCUMENT_CATEGORIES = {
    "driver_license_front", "driver_license_back", "state_id_front", "state_id_back", "passport",
    "voter_registration", "work_permit", "concealed_weapons_permit", "green_card", "foreign_passport",
    "government_issued_id", "social_security_card"
}
PROOF_OF_ADDRESS_CATEGORIES = {
    "utility_bill_electricity", "utility_bill_water", "utility_bill_gas", "utility_bill_cable", "bank_statement"
}
CREDIT_OFFER_CATEGORIES = {"credit_card_offer", "personal_loan_offer", "car_loan_offer"}
COLLECTION_MAIL_CATEGORIES = {"collection_company_mail", "validation_letter", "settlement_offer", "attorney_letter"}
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


ANALYSIS_CONFIDENCE_LEVELS = ["high", "medium", "low"]
ANALYSIS_MATCH_STATUSES = ["new_candidate", "matches_active", "possibly_removed", "needs_review", "imported", "duplicate"]
REFERENCE_FORMAT_PATTERNS = {
    "experian": r"^\d{4}-\d{4}-\d{2}$",
    "transunion": r"^\d{8}-\d{3}$",
    "equifax": r"^\d{10}$",
}



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


def only_digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", (value or "").strip())


def normalize_dob_input(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = only_digits(raw)
    if len(digits) == 8:
        raw = f"{digits[0:2]}/{digits[2:4]}/{digits[4:8]}"
    else:
        raw = raw.replace("-", "/")
    try:
        dt = datetime.strptime(raw, "%m/%d/%Y")
        return dt.strftime("%m/%d/%Y")
    except ValueError:
        raise ValueError(f"Invalid date format: {value}. Use MM/DD/YYYY.")


def normalize_phone_input(value: Optional[str]) -> str:
    digits = only_digits(value)
    if not digits:
        return ""
    if len(digits) != 10:
        raise ValueError("Phone must be 10 digits.")
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"


def normalize_ssn_input(value: Optional[str]) -> str:
    digits = only_digits(value)
    if not digits:
        return ""
    if len(digits) != 9:
        raise ValueError("Social Security must be 9 digits.")
    return f"{digits[0:3]}-{digits[3:5]}-{digits[5:9]}"


def parse_date_input(value: Optional[str]) -> str:
    formatted = normalize_dob_input(value)
    if not formatted:
        return ""
    return datetime.strptime(formatted, "%m/%d/%Y").date().isoformat()


def parse_date_string(value: Optional[str]):
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None



# ---------------------------
# Document analysis / staging helpers
# ---------------------------

def normalize_reference_value(bureau: str, value: str) -> str:
    raw = (value or '').strip()
    if not raw:
        return ''
    bureau = (bureau or '').lower().strip()
    compact = raw.replace(' ', '')
    if bureau == 'transunion':
        compact = compact.replace('File#', '').replace('file#', '').replace('FILE#', '').strip()
    elif bureau == 'equifax':
        compact = compact.replace('Confirmation#', '').replace('confirmation#', '').replace('CONFIRMATION#', '').strip()
    elif bureau == 'experian':
        compact = compact.replace('ReportNumber', '').replace('reportnumber', '').replace('REPORTNUMBER', '').strip()
    return compact


def reference_value_is_valid(bureau: str, value: str) -> bool:
    pattern = REFERENCE_FORMAT_PATTERNS.get((bureau or '').lower().strip())
    if not pattern:
        return True
    raw = normalize_reference_value(bureau, value)
    return bool(re.fullmatch(pattern, raw))


def _normalize_key_token(value: str) -> str:
    value = (value or '').lower().strip()
    value = re.sub(r'[^a-z0-9]+', '', value)
    return value


def _extract_last4(value: str) -> str:
    digits = re.sub(r'\D+', '', value or '')
    return digits[-4:] if len(digits) >= 4 else digits


def _parse_flexible_date(value: str) -> str:
    s = (value or '').strip()
    if not s:
        return ''
    for fmt in (
        '%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%b %d, %Y', '%B %d, %Y',
        '%m/%d/%y', '%m-%d-%y', '%Y/%m/%d'
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def _document_text_preview(text: str, max_chars: int = 1600) -> str:
    s = re.sub(r'\s+', ' ', (text or '').strip())
    return s[:max_chars]


def extract_text_from_document(file_path: str, file_name: str = '') -> tuple[str, str]:
    path = (file_path or '').strip()
    if not path or not os.path.exists(path):
        return '', 'Document file is missing from disk.'
    suffix = Path(file_name or path).suffix.lower()
    try:
        if suffix in {'.txt', '.md', '.csv', '.log'}:
            return Path(path).read_text(encoding='utf-8', errors='ignore'), ''
        if suffix in {'.html', '.htm'}:
            raw = Path(path).read_text(encoding='utf-8', errors='ignore')
            return re.sub(r'<[^>]+>', ' ', raw), ''
        if suffix == '.pdf':
            if PdfReader is None:
                return '', "PDF text extraction library is not available. Install one with: pip install pypdf"
            reader = PdfReader(path)
            pages = []
            for page in getattr(reader, 'pages', []) or []:
                try:
                    extracted = page.extract_text() or ''
                except Exception:
                    extracted = ''
                if extracted:
                    pages.append(extracted)
            combined = '\n\n'.join(pages).strip()
            if not combined:
                return '', 'No extractable text found. This file may be a scanned image PDF and may need OCR or manual review.'
            return combined, ''
        return '', 'This file type is not currently supported for automatic text extraction.'
    except Exception as exc:
        return '', f'Could not read document text: {exc}'


def _clean_for_match(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def _document_client_match(profile: dict, file_name: str = '', extracted_text: str = '') -> tuple[str, int, str]:
    haystack = ' '.join([file_name or '', extracted_text or ''])
    hay_clean = _clean_for_match(haystack)
    if not hay_clean:
        return 'unknown', 0, 'Could not verify client identity automatically from this file. Review manually before using it.'
    score = 0
    notes = []
    first = _clean_for_match(profile.get('first_name', ''))
    last = _clean_for_match(profile.get('last_name', ''))
    full = _clean_for_match(' '.join([profile.get('first_name',''), profile.get('middle_name',''), profile.get('last_name','')]))
    if full and full in hay_clean:
        score += 3
        notes.append('full name matched')
    elif first and last and first in hay_clean and last in hay_clean:
        score += 3
        notes.append('name matched')
    elif last and last in hay_clean:
        score += 1
        notes.append('last name matched only')
    hay_digits = re.sub(r'\D+', '', haystack)
    dob_compact = re.sub(r'\D+', '', profile.get('date_of_birth_display', '') or '')
    if dob_compact and dob_compact in hay_digits:
        score += 2
        notes.append('DOB matched')
    ssn_last4 = re.sub(r'\D+', '', profile.get('ssn_last4', '') or '')
    if ssn_last4 and ssn_last4 in hay_digits:
        score += 2
        notes.append('SSN last4 matched')
    addr_line1 = _clean_for_match(profile.get('address_line1', ''))
    city = _clean_for_match(profile.get('city', ''))
    zip_code = re.sub(r'\D+', '', profile.get('zip', '') or '')
    if addr_line1 and addr_line1 in hay_clean:
        score += 1
        notes.append('address matched')
    elif city and city in hay_clean:
        score += 1
        notes.append('city matched')
    if zip_code and zip_code in hay_digits:
        score += 1
        notes.append('ZIP matched')
    if score >= 6:
        status = 'matched'
    elif score >= 3:
        status = 'partial_match'
    else:
        status = 'possible_mismatch'
    summary = '; '.join(notes) if notes else 'No clear client identifiers were detected in the uploaded file.'
    if status == 'matched':
        summary = 'Client identifiers look consistent. ' + summary
    elif status == 'partial_match':
        summary = 'Review suggested before use. ' + summary
    else:
        summary = 'Warning: report/response may not belong to this client. ' + summary
    return status, score, summary


def _assess_uploaded_document_for_client(cur, client_id: str, file_name: str = '', file_path: str = '') -> tuple[str, int, str]:
    profile = fetch_client_profile(cur, client_id, include_sensitive=False)
    extracted_text = ''
    if file_path:
        extracted_text, _ = extract_text_from_document(file_path, file_name)
    return _document_client_match(profile, file_name=file_name, extracted_text=extracted_text)


def _split_text_blocks(text: str) -> list[str]:
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    blocks = [b.strip() for b in re.split(r'\n\s*\n', raw) if b.strip()]
    return blocks


def _first_date_in_text(text: str) -> str:
    candidates = re.findall(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\b', text or '')
    for c in candidates:
        parsed = _parse_flexible_date(c)
        if parsed:
            return parsed
    return ''


def _detect_account_number_fragment(text: str) -> str:
    match = re.search(r'(?:acct(?:ount)?(?:\s*(?:number|no|#))?\s*[:#-]?\s*)([A-Z0-9*Xx-]{4,})', text or '', flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    masked = re.search(r'([Xx*#]{0,8}\d{2,8}|\d{2,8}[Xx*#]{0,8}|\d{4,})', text or '')
    return masked.group(1).strip() if masked else ''


def _detect_creditor_name(block: str) -> str:
    for pattern in [r'creditor(?:\s*name)?\s*[:#-]\s*([^\n]+)', r'furnisher\s*[:#-]\s*([^\n]+)', r'account\s*name\s*[:#-]\s*([^\n]+)']:
        match = re.search(pattern, block or '', flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(' .:-')
    lines = [ln.strip() for ln in (block or '').splitlines() if ln.strip()]
    for line in lines[:3]:
        if len(line) < 3:
            continue
        if re.search(r'(status|balance|payment|date|history|account|remarks|bureau)', line, flags=re.IGNORECASE):
            continue
        return line.strip(' .:-')
    return 'Unclear tradeline'


def _suggest_account_strategy(account: dict) -> dict:
    status_text = (account.get('status_text') or '').lower()
    notes = []
    reason = 'other'
    action = 'investigate'
    strategy = 'Review reporting for accuracy, completeness, and verifiability before mailing the dispute.'
    if account.get('late_count', 0) > 0 and 'charge' not in status_text and 'collect' not in status_text:
        reason = 'never late'
        action = 'correct'
        strategy = 'Payment-history challenge candidate if the client confirms the reported late month(s) are inaccurate.'
    if any(k in status_text for k in ['charge off', 'chargeoff', 'collection', 'repossession', 'bankruptcy']):
        reason = 'other'
        action = 'investigate'
        strategy = 'Derogatory status challenge candidate. Verify ownership, status coding, dates, and completeness before asserting any stronger basis.'
    if 'repo' in status_text or 'repossession' in status_text:
        notes.append('Check whether the file shows a redeemed or cured repossession status before phrasing the dispute.')
    if account.get('missing_required_data'):
        notes.append('Possible incomplete reporting issue detected.')
    if account.get('account_age_years', 0) >= 8 and account.get('is_open'):
        notes.append('Preserve-history caution: older open tradeline may be helping average age.')
    if not notes:
        notes.append('Use the selected report/response and the client’s attested facts to refine the final letter language.')
    return {
        'suggested_reason': reason,
        'suggested_action': action,
        'strategy_note': ' '.join(notes) + ' ' + strategy,
    }


def _extract_personal_info_candidates(text: str, bureau: str) -> list[dict]:
    items = []
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    patterns = [
        ('name', r'^(?:name|consumer\s+name|also\s+known\s+as|aka)\s*[:#-]?\s*(.+)$'),
        ('address', r'^(?:address|current\s+address|previous\s+address|former\s+address)\s*[:#-]?\s*(.+)$'),
        ('employer', r'^(?:employer|employers)\s*[:#-]?\s*(.+)$'),
        ('phone', r'^(?:phone|telephone)\s*[:#-]?\s*(.+)$'),
        ('ssn variation', r'^(?:ssn|social\s+security)\s*[:#-]?\s*(.+)$'),
    ]
    seen = set()
    for line in lines:
        for info_type, pattern in patterns:
            m = re.search(pattern, line, flags=re.IGNORECASE)
            if not m:
                continue
            value = m.group(1).strip()
            key = (_normalize_key_token(info_type), _normalize_key_token(value))
            if not value or key in seen:
                break
            seen.add(key)
            items.append({
                'bureau': bureau,
                'info_type': info_type,
                'reported_value': value,
                'correct_value': '',
                'suggested_action': 'delete' if info_type in {'address', 'employer', 'phone'} else 'correct',
                'confidence': 'high',
                'raw_excerpt': line,
                'comparison_status': 'new_candidate',
                'strategy_note': 'Review whether this identity/profile element is inaccurate, outdated, or should be removed from the bureau file.',
            })
            break
    return items


def _extract_inquiry_candidates(text: str, bureau: str) -> list[dict]:
    items = []
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    inquiry_section = False
    seen = set()
    for line in lines:
        low = line.lower()
        if 'inquir' in low:
            inquiry_section = True
            if len(low) < 20:
                continue
        if inquiry_section or ('inquiry' in low and len(line) > 10):
            date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})', line)
            inquiry_date = _parse_flexible_date(date_match.group(1)) if date_match else ''
            cleaned = re.sub(r'(hard\s+inquir(?:y|ies)|soft\s+inquir(?:y|ies)|inquiries|inquiry)', '', line, flags=re.IGNORECASE)
            cleaned = re.sub(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})', '', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:|,')
            furnisher = cleaned[:120] if cleaned else ''
            if not furnisher:
                continue
            key = (_normalize_key_token(furnisher), inquiry_date)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                'bureau': bureau,
                'furnisher_name': furnisher,
                'inquiry_date': inquiry_date,
                'suggested_reason': 'not my inquiry',
                'suggested_action': 'delete',
                'confidence': 'medium' if inquiry_date else 'low',
                'raw_excerpt': line,
                'comparison_status': 'new_candidate',
                'strategy_note': 'Confirm whether the client recognizes this inquiry and whether there was a permissible purpose before mailing the dispute.',
            })
    return items


def _extract_account_candidates(text: str, bureau: str) -> list[dict]:
    blocks = _split_text_blocks(text)
    items = []
    seen = set()
    for block in blocks:
        low = block.lower()
        if not any(k in low for k in ['late', 'charge', 'collect', 'repo', 'bankrupt', 'past due', 'derog', 'account', 'tradeline']):
            continue
        if 'inquiry' in low and 'account' not in low:
            continue
        creditor_name = _detect_creditor_name(block)
        acct_fragment = _detect_account_number_fragment(block)
        late_count = len(re.findall(r'\b30\b|\b60\b|\b90\b|\b120\b|late', low))
        status_bits = []
        for keyword in ['open', 'closed', 'charge off', 'chargeoff', 'collection', 'collections', 'repossession', 'repo', 'redeemed', 'bankruptcy', 'past due', 'late']:
            if keyword in low:
                status_bits.append(keyword)
        status_text = ', '.join(dict.fromkeys(status_bits)) or 'review account status'
        missing_required_data = bool(re.search(r'date of first delinquency|first delinquency', low) and 'missing' in low)
        acct_key = (_normalize_key_token(creditor_name), _extract_last4(acct_fragment))
        if acct_key in seen:
            continue
        seen.add(acct_key)
        open_state = 'open' in low and 'closed' not in low
        age_years = 0
        age_match = re.search(r'(\d+)\s+year', low)
        if age_match:
            try:
                age_years = int(age_match.group(1))
            except ValueError:
                age_years = 0
        item = {
            'bureau': bureau,
            'creditor_name': creditor_name,
            'account_number_fragment': acct_fragment,
            'account_status': 'open' if open_state else ('closed' if 'closed' in low else ''),
            'status_text': status_text,
            'late_count': late_count,
            'is_open': open_state,
            'account_age_years': age_years,
            'missing_required_data': missing_required_data,
            'confidence': 'medium',
            'raw_excerpt': block[:1500],
            'comparison_status': 'new_candidate',
        }
        item.update(_suggest_account_strategy(item))
        items.append(item)
    return items


def _build_live_dispute_maps(cur, client_id: str, bureau: str) -> dict:
    bureau = (bureau or '').lower()
    account_rows = fetch_account_disputes(cur, client_id, 500)
    personal_rows = fetch_personal_info_disputes(cur, client_id, 500)
    inquiry_rows = fetch_inquiry_disputes(cur, client_id, 500)

    accounts = {}
    for item in account_rows:
        if not item.get('is_active'):
            continue
        if item.get('bureau') not in ('', bureau):
            continue
        key = (_normalize_key_token(item.get('creditor_name', '')), _extract_last4(item.get('account_number_last4', '')))
        accounts[key] = item

    personal = {}
    for item in personal_rows:
        if not item.get('is_active'):
            continue
        if item.get('bureau') not in ('', bureau):
            continue
        key = (_normalize_key_token(item.get('info_type', '')), _normalize_key_token(item.get('reported_value', '')))
        personal[key] = item

    inquiries = {}
    for item in inquiry_rows:
        if not item.get('is_active'):
            continue
        if item.get('bureau') not in ('', bureau):
            continue
        key = (_normalize_key_token(item.get('furnisher_name', '')), item.get('inquiry_date', '') or '')
        inquiries[key] = item

    return {'accounts': accounts, 'personal_info': personal, 'inquiries': inquiries}


def analyze_document_text(cur, client_id: str, bureau: str, text: str) -> dict:
    bureau = (bureau or '').lower().strip()
    account_candidates = _extract_account_candidates(text, bureau)
    personal_candidates = _extract_personal_info_candidates(text, bureau)
    inquiry_candidates = _extract_inquiry_candidates(text, bureau)
    live_maps = _build_live_dispute_maps(cur, client_id, bureau)

    matched_account_keys = set()
    for item in account_candidates:
        key = (_normalize_key_token(item.get('creditor_name', '')), _extract_last4(item.get('account_number_fragment', '')))
        if key in live_maps['accounts']:
            item['comparison_status'] = 'matches_active'
            item['matched_dispute_id'] = live_maps['accounts'][key]['id']
            matched_account_keys.add(key)
    matched_personal_keys = set()
    for item in personal_candidates:
        key = (_normalize_key_token(item.get('info_type', '')), _normalize_key_token(item.get('reported_value', '')))
        if key in live_maps['personal_info']:
            item['comparison_status'] = 'matches_active'
            item['matched_dispute_id'] = live_maps['personal_info'][key]['id']
            matched_personal_keys.add(key)
    matched_inquiry_keys = set()
    for item in inquiry_candidates:
        key = (_normalize_key_token(item.get('furnisher_name', '')), item.get('inquiry_date', '') or '')
        if key in live_maps['inquiries']:
            item['comparison_status'] = 'matches_active'
            item['matched_dispute_id'] = live_maps['inquiries'][key]['id']
            matched_inquiry_keys.add(key)

    missing_accounts = []
    for key, item in live_maps['accounts'].items():
        if key not in matched_account_keys:
            missing_accounts.append(f"{item.get('creditor_name')} {item.get('account_number_last4') or ''}".strip())
    missing_personal = []
    for key, item in live_maps['personal_info'].items():
        if key not in matched_personal_keys:
            missing_personal.append(f"{item.get('info_type')}: {item.get('reported_value')}")
    missing_inquiries = []
    for key, item in live_maps['inquiries'].items():
        if key not in matched_inquiry_keys:
            missing_inquiries.append(f"{item.get('furnisher_name')} {item.get('inquiry_date') or ''}".strip())

    needs_review = []
    if not account_candidates and not personal_candidates and not inquiry_candidates:
        needs_review.append('No structured items were extracted automatically. Review the document manually or upload a text-based PDF/report.')
    if len(account_candidates) < 1:
        needs_review.append('Account extraction was limited. Some report formats or scanned PDFs may require manual review.')

    comparison_summary = {
        'accounts': {
            'found': len(account_candidates),
            'matched_active': sum(1 for i in account_candidates if i.get('comparison_status') == 'matches_active'),
            'new_candidates': sum(1 for i in account_candidates if i.get('comparison_status') == 'new_candidate'),
            'possibly_removed_from_current_report': missing_accounts,
        },
        'personal_info': {
            'found': len(personal_candidates),
            'matched_active': sum(1 for i in personal_candidates if i.get('comparison_status') == 'matches_active'),
            'new_candidates': sum(1 for i in personal_candidates if i.get('comparison_status') == 'new_candidate'),
            'possibly_removed_from_current_report': missing_personal,
        },
        'inquiries': {
            'found': len(inquiry_candidates),
            'matched_active': sum(1 for i in inquiry_candidates if i.get('comparison_status') == 'matches_active'),
            'new_candidates': sum(1 for i in inquiry_candidates if i.get('comparison_status') == 'new_candidate'),
            'possibly_removed_from_current_report': missing_inquiries,
        },
        'needs_review': needs_review,
    }
    return {
        'accounts': account_candidates,
        'personal_info': personal_candidates,
        'inquiries': inquiry_candidates,
        'comparison_summary': comparison_summary,
    }


def create_analysis_run(cur, client_id: str, bureau: str, source_document_id: str, source_kind: str, extracted_text: str, comparison_summary: dict) -> str:
    cur.execute("""
        INSERT INTO dispute_analysis_runs
          (client_id, bureau, source_document_id, source_kind, extracted_text_preview, comparison_summary, status, created_by)
        VALUES
          (%s, %s, %s::uuid, %s, NULLIF(%s,''), %s::jsonb, 'completed', 'system')
        RETURNING id::text
    """, (client_id, bureau, source_document_id, source_kind, _document_text_preview(extracted_text), json.dumps(comparison_summary)))
    return cur.fetchone()[0]


def save_analysis_items(cur, run_id: str, client_id: str, bureau: str, result: dict):
    for item in result.get('accounts', []):
        cur.execute("""
            INSERT INTO dispute_analysis_accounts
              (run_id, client_id, bureau, creditor_name, account_number_fragment, account_status, status_text, late_count,
               suggested_reason, suggested_action, strategy_note, confidence, comparison_status, matched_dispute_id,
               raw_excerpt, selected_by_default)
            VALUES
              (%s::uuid, %s::uuid, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), %s,
               NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), %s, %s, NULLIF(%s,'')::uuid, NULLIF(%s,''), TRUE)
        """, (
            run_id, client_id, bureau, item.get('creditor_name'), item.get('account_number_fragment'), item.get('account_status'),
            item.get('status_text'), item.get('late_count') or None, item.get('suggested_reason'), item.get('suggested_action'),
            item.get('strategy_note'), item.get('confidence', 'medium'), item.get('comparison_status', 'new_candidate'),
            item.get('matched_dispute_id'), item.get('raw_excerpt')
        ))
    for item in result.get('personal_info', []):
        cur.execute("""
            INSERT INTO dispute_analysis_personal_info
              (run_id, client_id, bureau, info_type, reported_value, correct_value, suggested_action, strategy_note,
               confidence, comparison_status, matched_dispute_id, raw_excerpt, selected_by_default)
            VALUES
              (%s::uuid, %s::uuid, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
               %s, %s, NULLIF(%s,'')::uuid, NULLIF(%s,''), TRUE)
        """, (
            run_id, client_id, bureau, item.get('info_type'), item.get('reported_value'), item.get('correct_value'),
            item.get('suggested_action'), item.get('strategy_note'), item.get('confidence', 'medium'),
            item.get('comparison_status', 'new_candidate'), item.get('matched_dispute_id'), item.get('raw_excerpt')
        ))
    for item in result.get('inquiries', []):
        cur.execute("""
            INSERT INTO dispute_analysis_inquiries
              (run_id, client_id, bureau, furnisher_name, inquiry_date, suggested_reason, suggested_action, strategy_note,
               confidence, comparison_status, matched_dispute_id, raw_excerpt, selected_by_default)
            VALUES
              (%s::uuid, %s::uuid, %s, NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''),
               %s, %s, NULLIF(%s,'')::uuid, NULLIF(%s,''), TRUE)
        """, (
            run_id, client_id, bureau, item.get('furnisher_name'), item.get('inquiry_date'), item.get('suggested_reason'),
            item.get('suggested_action'), item.get('strategy_note'), item.get('confidence', 'medium'),
            item.get('comparison_status', 'new_candidate'), item.get('matched_dispute_id'), item.get('raw_excerpt')
        ))


def analyze_selected_source_document(cur, client_id: str, document_id: str) -> dict:
    cur.execute("""
        SELECT id::text, COALESCE(doc_section,''), COALESCE(doc_category,''), COALESCE(file_name,''), COALESCE(file_path,''),
               COALESCE(bureau,''), COALESCE(source_name,''), document_date, COALESCE(reference_label,''), COALESCE(reference_value,'')
        FROM client_documents
        WHERE client_id = %s::uuid AND id = %s::uuid
        LIMIT 1
    """, (client_id, document_id))
    row = cur.fetchone()
    if not row:
        raise ValueError('Selected document was not found.')
    doc = {
        'id': row[0], 'doc_section': row[1], 'doc_category': row[2], 'file_name': row[3], 'file_path': row[4],
        'bureau': row[5], 'source_name': row[6], 'document_date': str(row[7]) if row[7] else '',
        'reference_label': row[8], 'reference_value': row[9],
    }
    bureau = (doc.get('bureau') or '').lower().strip()
    if bureau not in BUREAUS:
        raise ValueError('The selected document must have a bureau set before it can be analyzed.')
    extracted_text, extraction_error = extract_text_from_document(doc['file_path'], doc['file_name'])
    comparison_summary = {'needs_review': [extraction_error] if extraction_error else []}
    if extraction_error:
        run_id = create_analysis_run(cur, client_id, bureau, document_id, _document_kind_label(doc.get('doc_section', '')), '', comparison_summary)
        return {'run_id': run_id, 'bureau': bureau, 'counts': {'accounts': 0, 'personal_info': 0, 'inquiries': 0}, 'error': extraction_error}
    result = analyze_document_text(cur, client_id, bureau, extracted_text)
    comparison_summary = result.get('comparison_summary', {})
    run_id = create_analysis_run(cur, client_id, bureau, document_id, _document_kind_label(doc.get('doc_section', '')), extracted_text, comparison_summary)
    save_analysis_items(cur, run_id, client_id, bureau, result)
    return {
        'run_id': run_id,
        'bureau': bureau,
        'counts': {
            'accounts': len(result.get('accounts', [])),
            'personal_info': len(result.get('personal_info', [])),
            'inquiries': len(result.get('inquiries', [])),
        },
        'error': '',
    }


def fetch_latest_analysis_by_bureau(cur, client_id: str) -> dict:
    results = {bureau: None for bureau in BUREAUS}
    for bureau in BUREAUS:
        cur.execute("""
            SELECT ar.id::text, ar.bureau, ar.source_document_id::text, COALESCE(cd.file_name,''), COALESCE(cd.reference_label,''),
                   COALESCE(cd.reference_value,''), cd.document_date, COALESCE(ar.source_kind,''), COALESCE(ar.comparison_summary::text, '{}'),
                   COALESCE(ar.extracted_text_preview,''), ar.created_at
            FROM dispute_analysis_runs ar
            LEFT JOIN client_documents cd ON cd.id = ar.source_document_id
            WHERE ar.client_id = %s::uuid AND ar.bureau = %s
            ORDER BY ar.created_at DESC
            LIMIT 1
        """, (client_id, bureau))
        row = cur.fetchone()
        if not row:
            continue
        run_id = row[0]
        cur.execute("""
            SELECT id::text, COALESCE(creditor_name,''), COALESCE(account_number_fragment,''), COALESCE(account_status,''),
                   COALESCE(status_text,''), COALESCE(suggested_reason,''), COALESCE(suggested_action,''), COALESCE(strategy_note,''),
                   COALESCE(confidence,'medium'), COALESCE(comparison_status,'new_candidate'), COALESCE(imported_dispute_id::text,''),
                   COALESCE(raw_excerpt,'')
            FROM dispute_analysis_accounts
            WHERE run_id = %s::uuid
            ORDER BY comparison_status, creditor_name
        """, (run_id,))
        accounts = [
            {
                'id': r[0], 'creditor_name': r[1], 'account_number_fragment': r[2], 'account_status': r[3], 'status_text': r[4],
                'suggested_reason': r[5], 'suggested_action': r[6], 'strategy_note': r[7], 'confidence': r[8],
                'comparison_status': r[9], 'imported_dispute_id': r[10], 'raw_excerpt': r[11]
            }
            for r in cur.fetchall()
        ]
        cur.execute("""
            SELECT id::text, COALESCE(info_type,''), COALESCE(reported_value,''), COALESCE(correct_value,''), COALESCE(suggested_action,''),
                   COALESCE(strategy_note,''), COALESCE(confidence,'medium'), COALESCE(comparison_status,'new_candidate'),
                   COALESCE(imported_dispute_id::text,''), COALESCE(raw_excerpt,'')
            FROM dispute_analysis_personal_info
            WHERE run_id = %s::uuid
            ORDER BY comparison_status, info_type, reported_value
        """, (run_id,))
        personal = [
            {
                'id': r[0], 'info_type': r[1], 'reported_value': r[2], 'correct_value': r[3], 'suggested_action': r[4],
                'strategy_note': r[5], 'confidence': r[6], 'comparison_status': r[7], 'imported_dispute_id': r[8], 'raw_excerpt': r[9]
            }
            for r in cur.fetchall()
        ]
        cur.execute("""
            SELECT id::text, COALESCE(furnisher_name,''), inquiry_date, COALESCE(suggested_reason,''), COALESCE(suggested_action,''),
                   COALESCE(strategy_note,''), COALESCE(confidence,'medium'), COALESCE(comparison_status,'new_candidate'),
                   COALESCE(imported_dispute_id::text,''), COALESCE(raw_excerpt,'')
            FROM dispute_analysis_inquiries
            WHERE run_id = %s::uuid
            ORDER BY comparison_status, inquiry_date DESC NULLS LAST, furnisher_name
        """, (run_id,))
        inquiries = [
            {
                'id': r[0], 'furnisher_name': r[1], 'inquiry_date': str(r[2]) if r[2] else '', 'suggested_reason': r[3],
                'suggested_action': r[4], 'strategy_note': r[5], 'confidence': r[6], 'comparison_status': r[7],
                'imported_dispute_id': r[8], 'raw_excerpt': r[9]
            }
            for r in cur.fetchall()
        ]
        try:
            comparison_summary = json.loads(row[8] or '{}')
        except Exception:
            comparison_summary = {}
        results[bureau] = {
            'run_id': run_id,
            'bureau': row[1],
            'source_document_id': row[2],
            'source_file_name': row[3],
            'reference_label': row[4],
            'reference_value': row[5],
            'document_date': str(row[6]) if row[6] else '',
            'source_kind': row[7],
            'comparison_summary': comparison_summary,
            'extracted_text_preview': row[9],
            'created_at': str(row[10]) if row[10] else '',
            'accounts': accounts,
            'personal_info': personal,
            'inquiries': inquiries,
        }
    return results


def _match_existing_account_dispute(cur, client_id: str, bureau: str, creditor_name: str, last4: str) -> Optional[str]:
    cur.execute("""
        SELECT id::text
        FROM client_account_disputes
        WHERE client_id = %s::uuid
          AND lower(COALESCE(bureau,'')) IN ('', lower(%s))
          AND lower(COALESCE(creditor_name,'')) = lower(%s)
          AND COALESCE(account_number_last4,'') = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (client_id, bureau, creditor_name, last4))
    row = cur.fetchone()
    return row[0] if row else None


def build_smart_letter_text(cur, client_id: str, bureau: str, round_number: int, include_personal_info: bool, include_inquiries: bool, letter_instructions: str = '', round_run_id: Optional[str] = None) -> str:
    profile = fetch_client_profile(cur, client_id, include_sensitive=False)
    primary_doc = _fetch_selected_primary_document(cur, round_run_id, client_id, bureau)
    bureau_label = (bureau or '').title()
    doc_kind = _document_kind_label(primary_doc.get('doc_section', '')) if primary_doc else 'Report'
    doc_date = format_long_date(primary_doc.get('document_date') or primary_doc.get('statement_date') or date.today()) if primary_doc else format_long_date(date.today())
    reference_label = (primary_doc.get('reference_label') or _default_reference_label_for_bureau(bureau)).strip() if primary_doc else _default_reference_label_for_bureau(bureau)
    reference_value = (primary_doc.get('reference_value') or '').strip() if primary_doc else ''

    account_items = [i for i in fetch_account_disputes(cur, client_id, 500) if i.get('is_active') and (not i.get('bureau') or i.get('bureau') == bureau)]
    personal_items = [i for i in fetch_personal_info_disputes(cur, client_id, 500) if i.get('is_active') and (not i.get('bureau') or i.get('bureau') == bureau)] if include_personal_info else []
    inquiry_items = [i for i in fetch_inquiry_disputes(cur, client_id, 500) if i.get('is_active') and (not i.get('bureau') or i.get('bureau') == bureau)] if include_inquiries else []

    paragraphs = []
    if round_number <= 1:
        paragraphs.append(f'I am writing after reviewing my {bureau_label} {doc_kind.lower()} dated {doc_date}. I dispute the accuracy and completeness of the items listed below and request a reasonable reinvestigation.')
    elif round_number == 2:
        paragraphs.append(f'I am following up after reviewing my {bureau_label} {doc_kind.lower()} dated {doc_date}. I previously disputed the items below and they continue to report inaccurately or incompletely. Please conduct a new reasonable reinvestigation.')
    else:
        paragraphs.append(f'I am again writing after reviewing my {bureau_label} {doc_kind.lower()} dated {doc_date}. The items listed below continue to be reported inaccurately, incompletely, or without adequate correction after prior disputes, and I request a fresh reinvestigation and correction or deletion as appropriate.')
    if reference_value:
        paragraphs.append(f'For reference, the {bureau_label} {doc_kind.lower()} I am disputing is identified as {reference_label} {reference_value}.')

    if account_items:
        details = []
        for idx, item in enumerate(account_items, start=1):
            acct_ref = item.get('account_number_last4') or 'reported as shown on my file'
            reason = (item.get('dispute_reason') or 'other').strip().lower()
            if reason == 'never late':
                reason_sentence = 'the reported late-payment history is inaccurate and should be corrected or deleted if it cannot be verified as complete and accurate'
            elif reason == 'not mine':
                reason_sentence = 'I do not recognize this account as mine and request reinvestigation of ownership and reporting accuracy'
            elif reason == 'not included in bankruptcy':
                reason_sentence = 'the bankruptcy-related reporting on this account is inaccurate or incomplete and should be corrected'
            else:
                note = (item.get('notes') or '').strip()
                reason_sentence = note if note else 'the reporting on this account is inaccurate, incomplete, or unverifiable and should be corrected or deleted as appropriate'
            details.append(f'{idx}. {item.get("creditor_name") or "Account"} ({acct_ref}): {reason_sentence}.')
        paragraphs.append('Please review the following account items: ' + ' '.join(details))

    if personal_items:
        details = []
        for idx, item in enumerate(personal_items, start=1):
            details.append(f'{idx}. {item.get("info_type")}: {item.get("reported_value")}. Please {item.get("requested_action") or "correct"} this personal-information entry if it is inaccurate, outdated, or should not be reporting.')
        paragraphs.append('Please also review the following personal-information items: ' + ' '.join(details))

    if inquiry_items:
        details = []
        for idx, item in enumerate(inquiry_items, start=1):
            date_part = f' dated {format_short_date(item.get("inquiry_date"))}' if item.get('inquiry_date') else ''
            reason = (item.get('dispute_reason') or 'not my inquiry').strip()
            details.append(f'{idx}. {item.get("furnisher_name")}{date_part}: please investigate and delete this inquiry if it was not authorized, is inaccurate, or cannot be verified.')
        paragraphs.append('Please review the following inquiries: ' + ' '.join(details))

    instructions = (letter_instructions or '').strip().lower()
    if round_number >= 2 or 'redispute' in instructions:
        paragraphs.append('If you previously verified any of these items without making the requested corrections, please provide the method of verification and the source of the information you relied on during your reinvestigation.')
    paragraphs.append('Please correct or delete any item that is inaccurate, incomplete, or cannot be verified after a reasonable reinvestigation, and send me an updated copy of my credit file once your review is complete.')
    return '\n\n'.join(paragraphs)

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
        "validation_letter": "collection_mail",
        "settlement_offer": "collection_mail",
        "attorney_letter": "collection_mail",
        "driver_license_front": "ids_proof_of_address",
        "driver_license_back": "ids_proof_of_address",
        "state_id_front": "ids_proof_of_address",
        "state_id_back": "ids_proof_of_address",
        "passport": "ids_proof_of_address",
        "voter_registration": "ids_proof_of_address",
        "work_permit": "ids_proof_of_address",
        "concealed_weapons_permit": "ids_proof_of_address",
        "green_card": "ids_proof_of_address",
        "foreign_passport": "ids_proof_of_address",
        "utility_bill_electricity": "ids_proof_of_address",
        "utility_bill_water": "ids_proof_of_address",
        "utility_bill_gas": "ids_proof_of_address",
        "utility_bill_cable": "ids_proof_of_address",
        "bank_statement": "ids_proof_of_address",
        "credit_card_offer": "credit_offers",
        "personal_loan_offer": "credit_offers",
        "car_loan_offer": "credit_offers",
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


def _merge_document_source_options(custom_sources: list[str]) -> list[str]:
    ordered = []
    seen = set()
    for value in DOCUMENT_SOURCE_OPTIONS + list(custom_sources or []):
        clean = (value or "").strip()
        if not clean or clean.lower() in seen:
            continue
        ordered.append(clean)
        seen.add(clean.lower())
    return ordered


def _validate_document_dates(doc_category: str, statement_date: str = "", expires_on: str = "") -> None:
    category = (doc_category or "").strip()
    today = date.today()
    if category in ID_DOCUMENT_CATEGORIES:
        if not expires_on:
            raise ValueError("Expiration date is required for identification documents.")
        exp_date = _coerce_iso_date(expires_on)
        if not exp_date:
            raise ValueError("Expiration date is invalid.")
        if exp_date < today:
            raise ValueError("Identification must be unexpired.")
    if category in PROOF_OF_ADDRESS_CATEGORIES:
        if not statement_date:
            raise ValueError("Statement / utility bill date is required for proof of address.")
        stmt_date = _coerce_iso_date(statement_date)
        if not stmt_date:
            raise ValueError("Statement / utility bill date is invalid.")
        age_days = (today - stmt_date).days
        if age_days > 60:
            raise ValueError("Proof of address must be no more than 60 days old.")


def _doc_category_counts_as_proof(doc_category: str) -> bool:
    return (doc_category or "") in PROOF_OF_ADDRESS_CATEGORIES or (doc_category or "") in {"driver_license_front", "driver_license_back", "state_id_front", "state_id_back"}


def _document_kind_label(doc_section: str) -> str:
    return "Response" if (doc_section or "") == "bureau_responses" else "Report"



def _safe_add_years(dt_obj: date, years: int) -> date:
    try:
        return dt_obj.replace(year=dt_obj.year + years)
    except ValueError:
        return dt_obj.replace(month=2, day=28, year=dt_obj.year + years)


def _compute_proof_address_metrics(statement_date_value) -> dict:
    if not statement_date_value:
        return {}
    if isinstance(statement_date_value, datetime):
        base_date = statement_date_value.date()
    else:
        base_date = statement_date_value
    today = date.today()
    days_old = max((today - base_date).days, 0)
    days_remaining = 90 - days_old
    remind_on = base_date + timedelta(days=75)
    if days_remaining >= 0:
        if days_old >= 75:
            status = f"{days_old} days old · request updated proof now"
        else:
            status = f"{days_old} days old · good for another {days_remaining} days"
    else:
        status = f"{days_old} days old · expired {-days_remaining} days ago"
    return {
        "days_old": days_old,
        "days_remaining": days_remaining,
        "remind_on": str(remind_on),
        "status_text": status,
    }


def _compute_expiration_metrics(expires_on_value) -> dict:
    if not expires_on_value:
        return {}
    if isinstance(expires_on_value, datetime):
        exp_date = expires_on_value.date()
    else:
        exp_date = expires_on_value
    today = date.today()
    days_until = (exp_date - today).days
    if days_until >= 0:
        text = f"Valid for another {days_until} days"
    else:
        text = f"Expired {-days_until} days ago"
    return {"days_until": days_until, "status_text": text}


def _coerce_iso_date(value: str):
    value = (value or '').strip()
    if not value:
        return None
    return datetime.strptime(value, '%Y-%m-%d').date()


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
               COALESCE(client_match_status, '') AS client_match_status,
               COALESCE(client_match_score, 0) AS client_match_score,
               COALESCE(client_match_summary, '') AS client_match_summary,
               COALESCE(collection_company_name, '') AS collection_company_name,
               COALESCE(collecting_for, '') AS collecting_for,
               COALESCE(amount_claimed, '') AS amount_claimed,
               original_negative_date,
               collector_acquired_date,
               COALESCE(lender_name, '') AS lender_name,
               COALESCE(offer_code, '') AS offer_code,
               COALESCE(offer_website, '') AS offer_website,
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
        doc_category = r[2] or ''
        ref_label = (r[10] or _default_reference_label_for_bureau(r[7])).strip() if r[11] else (r[10] or '').strip()
        proof_metrics = _compute_proof_address_metrics(r[12]) if doc_category in PROOF_OF_ADDRESS_CATEGORIES else {}
        exp_metrics = _compute_expiration_metrics(r[15]) if doc_category in ID_DOCUMENT_CATEGORIES or section_key == 'credit_offers' else {}
        original_negative_date = str(r[24]) if r[24] else ''
        collector_acquired_date = str(r[25]) if r[25] else ''
        four_year_tracker = ''
        if r[24]:
            four_year_tracker = str(_safe_add_years(r[24], 4))
        offer_lines = []
        if r[26]:
            offer_lines.append(f"Lender: {r[26]}")
        if r[15] and section_key == 'credit_offers':
            offer_lines.append(f"Expiration: {r[15]}")
        if r[27]:
            offer_lines.append(f"Offer code: {r[27]}")
        if r[28]:
            offer_lines.append(f"Website: {r[28]}")
        collection_lines = []
        if r[21]:
            collection_lines.append(f"Collection company: {r[21]}")
        if r[22]:
            collection_lines.append(f"Collecting for: {r[22]}")
        if r[23]:
            collection_lines.append(f"Amount owed: {r[23]}")
        if original_negative_date:
            collection_lines.append(f"Original account went negative: {original_negative_date}")
        if collector_acquired_date:
            collection_lines.append(f"Bought by collector: {collector_acquired_date}")
        if four_year_tracker:
            collection_lines.append(f"4-year tracker (reference): {four_year_tracker}")
        out.append({
            "id": r[0],
            "doc_type": r[1],
            "doc_category": doc_category,
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
            "client_match_status": r[18] or '',
            "client_match_score": r[19] or 0,
            "client_match_summary": r[20] or '',
            "collection_company_name": r[21] or '',
            "collecting_for": r[22] or '',
            "amount_claimed": r[23] or '',
            "original_negative_date": original_negative_date,
            "collector_acquired_date": collector_acquired_date,
            "lender_name": r[26] or '',
            "offer_code": r[27] or '',
            "offer_website": r[28] or '',
            "created_at": str(r[29]) if r[29] else '',
            "can_open": bool(r[5]),
            "document_kind": _document_kind_label(section_key) if section_key in {"reports", "bureau_responses"} else "",
            "proof_status_text": proof_metrics.get('status_text', ''),
            "proof_remind_on": proof_metrics.get('remind_on', ''),
            "expiration_status_text": exp_metrics.get('status_text', ''),
            "collection_summary_lines": collection_lines,
            "offer_summary_lines": offer_lines,
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


def fetch_document_source_options(cur):
    try:
        cur.execute("SELECT source_name FROM custom_document_sources ORDER BY lower(source_name)")
        rows = [r[0] for r in cur.fetchall() if r and r[0]]
    except Exception:
        try:
            cur.connection.rollback()
        except Exception:
            pass
        rows = []
    return _merge_document_source_options(rows)


def fetch_associated_clients(cur, client_id: str):
    try:
        cur.execute("""
            SELECT ca.id::text, ca.associated_client_id::text, COALESCE(ca.relationship_label,''),
                   COALESCE(c.first_name,''), COALESCE(c.last_name,''), COALESCE(c.phone,''), COALESCE(c.primary_email,''), COALESCE(c.is_active, TRUE)
            FROM client_associations ca
            JOIN clients c ON c.id = ca.associated_client_id
            WHERE ca.client_id = %s::uuid
            ORDER BY lower(COALESCE(c.last_name,'')), lower(COALESCE(c.first_name,''))
        """, (client_id,))
        rows = cur.fetchall()
        return [{
            "id": r[0], "associated_client_id": r[1], "relationship_label": r[2],
            "name": " ".join([p for p in [r[3], r[4]] if p]).strip() or r[1],
            "phone": r[5] or '', "email": r[6] or '', "is_active": bool(r[7]),
        } for r in rows]
    except Exception:
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return []


def fetch_client_switch_choices(cur, client_id: str, limit: int = 300):
    try:
        cur.execute("""
            SELECT id::text, COALESCE(first_name,''), COALESCE(last_name,''), COALESCE(phone,''), COALESCE(primary_email,'')
            FROM clients
            WHERE id <> %s::uuid
            ORDER BY lower(COALESCE(last_name,'')), lower(COALESCE(first_name,''))
            LIMIT %s
        """, (client_id, limit))
        rows = cur.fetchall()
        return [{
            "id": r[0],
            "name": " ".join([p for p in [r[1], r[2]] if p]).strip() or r[0],
            "phone": r[3] or '',
            "email": r[4] or '',
        } for r in rows]
    except Exception:
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return []


def fetch_referral_partners(cur):
    try:
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
    except Exception:
        try:
            cur.connection.rollback()
        except Exception:
            pass
        return []


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


def get_current_round_number(cur, client_id: str) -> int:
    return int(fetch_dispute_round_defaults(cur, client_id).get('current_round_number', 1) or 1)


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




def _safe_parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for parser in (
        lambda t: datetime.fromisoformat(t.replace('Z', '+00:00')).date(),
        lambda t: datetime.strptime(t[:10], '%Y-%m-%d').date(),
        lambda t: datetime.strptime(t[:10], '%m/%d/%Y').date(),
    ):
        try:
            return parser(text)
        except Exception:
            continue
    return None


def get_current_round_number_for_bureau(cur, client_id: str, bureau: str) -> int:
    bureau = (bureau or '').strip().lower()
    max_round = 0
    cur.execute("""
        SELECT COALESCE(MAX(COALESCE(meta.round_number, 0)), 0)
        FROM letters l
        LEFT JOIN round_run_dispute_meta meta ON meta.round_run_id = l.round_run_id
        WHERE l.client_id = %s AND lower(COALESCE(l.bureau::text, '')) = %s
    """, (client_id, bureau))
    row = cur.fetchone()
    if row and row[0]:
        max_round = max(max_round, int(row[0] or 0))

    for table in ('client_account_disputes', 'client_personal_info_disputes', 'client_inquiry_disputes'):
        cur.execute(f"""
            SELECT COALESCE(MAX(COALESCE(round_added, 0)), 0)
            FROM {table}
            WHERE client_id = %s
              AND lower(COALESCE(bureau, '')) = %s
        """, (client_id, bureau))
        row = cur.fetchone()
        if row and row[0]:
            max_round = max(max_round, int(row[0] or 0))

    return max_round + 1 if max_round >= 1 else 1


def build_bureau_dispute_summaries(profile: dict, account_disputes: list, personal_info_disputes: list,
                                   inquiry_disputes: list, saved_letters: list, redispute_events: list,
                                   current_round_default: int) -> dict:
    summaries = {}
    today = date.today()
    full_name = ' '.join([p for p in [profile.get('first_name'), profile.get('middle_name'), profile.get('last_name')] if p]).strip()
    for bureau in BUREAUS:
        items = []
        for source, kind in ((account_disputes, 'accounts'), (personal_info_disputes, 'personal_info'), (inquiry_disputes, 'inquiries')):
            for item in source:
                if (item.get('bureau') or '').strip().lower() == bureau:
                    copy = dict(item)
                    copy['_kind'] = kind
                    items.append(copy)

        started_negative = sum(1 for item in items if int(item.get('round_added') or 1) <= 1)
        current_active = sum(1 for item in items if bool(item.get('is_active')))
        removed = sum(1 for item in items if not bool(item.get('is_active')))
        new_since_started = sum(1 for item in items if int(item.get('round_added') or 1) > 1)

        letters = [l for l in saved_letters if (l.get('bureau') or '').strip().lower() == bureau]
        letters.sort(key=lambda x: ((_safe_parse_date(x.get('generated_at')) or date.min), int(x.get('round_number') or 0)), reverse=True)
        latest_letter = letters[0] if letters else None
        latest_round_sent = int(latest_letter.get('round_number') or 0) if latest_letter else 0
        latest_date = _safe_parse_date(latest_letter.get('generated_at')) if latest_letter else None
        current_round = latest_round_sent + 1 if latest_round_sent else max(int(current_round_default or 1), 1)

        next_tickler_event = None
        future_events = []
        for event in redispute_events:
            if (event.get('bureau') or '').strip().lower() != bureau:
                continue
            event_date = _safe_parse_date(event.get('event_date'))
            if event_date and event_date >= today:
                future_events.append((event_date, event))
        future_events.sort(key=lambda t: t[0])
        if future_events:
            next_tickler_event = future_events[0][1]

        summaries[bureau] = {
            'bureau': bureau,
            'full_name': full_name,
            'started_negative': started_negative,
            'current_active': current_active,
            'removed': removed,
            'new_since_started': new_since_started,
            'latest_round_sent': latest_round_sent,
            'date_sent': format_short_date(latest_date) if latest_date else '',
            'next_round_tickler': format_short_date(next_tickler_event.get('event_date')) if next_tickler_event else '',
            'current_round': current_round,
        }
    return summaries


def build_bureau_dispute_round_summary(cur, client_id: str, bureau: str, round_number: int, include_personal_info: bool, include_inquiries: bool):
    bureau = (bureau or '').strip().lower()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE COALESCE(round_added,1) <= 1),
               COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE),
               COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE),
               COUNT(*) FILTER (WHERE COALESCE(round_added,1) > 1)
        FROM client_account_disputes
        WHERE client_id = %s AND lower(COALESCE(bureau, '')) = %s
    """, (client_id, bureau))
    acct = cur.fetchone() or (0, 0, 0, 0)
    parts = [
        f"{bureau.title()} dispute letter generated for round {round_number}.",
        f"Accounts: started {int(acct[0] or 0)}, current {int(acct[1] or 0)}, removed {int(acct[2] or 0)}, new since we started {int(acct[3] or 0)}."
    ]
    if include_personal_info:
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE COALESCE(round_added,1) <= 1),
                   COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE),
                   COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE),
                   COUNT(*) FILTER (WHERE COALESCE(round_added,1) > 1)
            FROM client_personal_info_disputes
            WHERE client_id = %s AND lower(COALESCE(bureau, '')) = %s
        """, (client_id, bureau))
        row = cur.fetchone() or (0, 0, 0, 0)
        parts.append(f"Personal info: started {int(row[0] or 0)}, current {int(row[1] or 0)}, removed {int(row[2] or 0)}, new since we started {int(row[3] or 0)}.")
    if include_inquiries:
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE COALESCE(round_added,1) <= 1),
                   COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE),
                   COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE),
                   COUNT(*) FILTER (WHERE COALESCE(round_added,1) > 1)
            FROM client_inquiry_disputes
            WHERE client_id = %s AND lower(COALESCE(bureau, '')) = %s
        """, (client_id, bureau))
        row = cur.fetchone() or (0, 0, 0, 0)
        parts.append(f"Inquiries: started {int(row[0] or 0)}, current {int(row[1] or 0)}, removed {int(row[2] or 0)}, new since we started {int(row[3] or 0)}.")
    return ' '.join(parts)


def ensure_bureau_redispute_tickler(cur, client_id: str, bureau: str, round_number: int, client_name: str, due_date: date):
    bureau = (bureau or '').strip().lower()
    title = f"{bureau.title()} Redispute - {client_name}".strip()
    cur.execute("""
        SELECT id::text FROM client_redispute_events
        WHERE client_id = %s AND lower(COALESCE(bureau, '')) = %s AND round_number = %s AND event_date = %s::date
        LIMIT 1
    """, (client_id, bureau, round_number, due_date))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO client_redispute_events (client_id, bureau, event_date, round_number, status, notes)
            VALUES (%s, %s, %s::date, %s, 'scheduled', %s)
        """, (client_id, bureau, due_date, round_number, title))

    cur.execute("""
        SELECT id::text FROM client_followups
        WHERE client_id = %s AND followup_type = 'redispute' AND due_date = %s::date AND COALESCE(note_text,'') = %s
        LIMIT 1
    """, (client_id, due_date, title))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO client_followups (client_id, followup_type, due_date, status, note_text)
            VALUES (%s, 'redispute', %s::date, 'open', %s)
        """, (client_id, due_date, title))

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
        "cutoff_date": r[5], "statement_date": r[5], "secured_deposit_amount": r[6], "origination_date": str(r[7]) if r[7] else '',
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




def format_currency(value) -> str:
    try:
        return f"${Decimal(str(value or 0)).quantize(Decimal('0.01')):,.2f}"
    except Exception:
        return "$0.00"


def _safe_decimal(value, default='0.00') -> Decimal:
    try:
        return Decimal(str(value or default)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _to_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _billing_day(profile: dict) -> int:
    group = (profile.get('billing_group') or '15th').strip().lower()
    if group == '1st':
        return 1
    if group == 'custom':
        try:
            return max(1, min(28, int(profile.get('custom_billing_day') or 15)))
        except Exception:
            return 15
    return 15


def _month_increment(year: int, month: int):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _last_day_of_month(year: int, month: int) -> int:
    ny, nm = _month_increment(year, month)
    return (date(ny, nm, 1) - timedelta(days=1)).day


def _invoice_number(cur, client_id: str) -> str:
    cur.execute("SELECT COUNT(*) FROM client_invoices WHERE client_id = %s::uuid", (client_id,))
    count = int((cur.fetchone() or [0])[0] or 0) + 1
    return f"INV-{date.today().strftime('%Y%m%d')}-{str(count).zfill(4)}"


def _billing_due_dates(profile: dict, horizon: date):
    monthly_fee = _safe_decimal(profile.get('monthly_fee'))
    if monthly_fee <= 0:
        return []
    start = _to_date(profile.get('first_payment_date')) or _to_date(profile.get('signup_date'))
    if not start:
        return []
    day = _billing_day(profile)
    dates = []
    y, m = start.year, start.month
    guard = 0
    while guard < 240:
        due_day = min(day, _last_day_of_month(y, m))
        due = date(y, m, due_day)
        if due >= start and due <= horizon:
            dates.append(due)
        if due > horizon:
            break
        y, m = _month_increment(y, m)
        guard += 1
    return dates


def _service_period_for_due_date(due_date: date):
    period_end = due_date - timedelta(days=1)
    period_start = period_end.replace(day=1)
    return period_start, period_end


def _create_invoice_row(cur, client_id: str, invoice_type: str, amount: Decimal, issue_date: date, due_date: date,
                        item_description: str, notes: str = '', is_automated: bool = False,
                        service_period_start: Optional[date] = None, service_period_end: Optional[date] = None):
    invoice_number = _invoice_number(cur, client_id)
    cur.execute(
        """
        INSERT INTO client_invoices
          (client_id, invoice_number, invoice_type, issue_date, due_date, item_description, amount,
           status, is_automated, service_period_start, service_period_end, notes)
        VALUES
          (%s::uuid, %s, %s, %s, %s, %s, %s, 'unpaid', %s, %s, %s, NULLIF(%s,''))
        RETURNING id::text
        """,
        (client_id, invoice_number, invoice_type, issue_date, due_date, item_description, amount, is_automated,
         service_period_start, service_period_end, notes)
    )
    return (cur.fetchone() or [None])[0]


def _queue_invoice_email_log(cur, client_id: str, profile: dict, invoice_id: str, subject: str, body: str, source: str):
    cur.execute(
        "SELECT 1 FROM client_emails WHERE client_id = %s::uuid AND source = %s AND subject = %s LIMIT 1",
        (client_id, source, subject)
    )
    if cur.fetchone():
        return
    cur.execute(
        """
        INSERT INTO client_emails
          (client_id, provider, direction, subject, body_text, from_email, to_email, email_date, email_type, status, source, created_by)
        VALUES
          (%s::uuid, 'system', 'outbound', %s, %s, %s, %s, CURRENT_TIMESTAMP, 'invoice', 'queued', %s, 'system')
        """,
        (client_id, subject, body, COMPANY_EMAIL, profile.get('primary_email') or '', source)
    )


def ensure_client_invoices(cur, client_id: str, profile: dict):
    monthly_fee = _safe_decimal(profile.get('monthly_fee'))
    if monthly_fee <= 0:
        return
    horizon = date.today() + timedelta(days=5)
    monthly_item = _find_invoice_item(cur, 'Monthly Service Fee', monthly_fee, 'monthly')
    monthly_desc = monthly_item.get('default_description') or 'Monthly service fee. Payment is for work completed the prior month.'
    for due_date in _billing_due_dates(profile, horizon):
        cur.execute("SELECT 1 FROM client_invoices WHERE client_id = %s::uuid AND invoice_type = 'monthly' AND due_date = %s AND status <> 'void' LIMIT 1", (client_id, due_date))
        if cur.fetchone():
            continue
        issue_date = due_date - timedelta(days=5)
        service_end = due_date - timedelta(days=1)
        service_start = date(service_end.year, service_end.month, 1)
        iid = _create_invoice_row(cur, client_id, 'monthly', monthly_fee, issue_date, due_date, monthly_desc,
                                  service_period_start=service_start, service_period_end=service_end,
                                  notes='Payment is for work completed the prior month.', is_automated=True)
        try:
            generate_invoice_pdf(cur, iid)
        except Exception:
            pass
    # reminder logs
    for inv in fetch_client_invoices(cur, client_id, 500):
        if inv.get('status') == 'paid':
            continue
        due_date = parse_date_string(inv.get('due_date'))
        if not due_date:
            continue
        inv_id = inv['id']; inv_no = inv['invoice_number']; amt = inv['amount_display']
        if (due_date - date.today()).days <= 5 and not inv.get('reminder_due_logged_at'):
            subj = f"Invoice {inv_no} due {format_short_date(due_date)}"
            body = f"Invoice {inv_no} for {amt} is due on {format_short_date(due_date)}. This payment is for work completed the prior month."
            _queue_invoice_email_log(cur, client_id, profile, inv_id, subj, body, f'invoice_due:{inv_id}')
            cur.execute("UPDATE client_invoices SET reminder_due_logged_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (inv_id,))
        if due_date < date.today() and not inv.get('reminder_past_due_logged_at'):
            subj = f"Past Due Invoice {inv_no}"
            body = f"Invoice {inv_no} for {amt} is now past due. Work will not be processed until the invoice is paid. This payment is for work completed the prior month."
            _queue_invoice_email_log(cur, client_id, profile, inv_id, subj, body, f'invoice_past_due:{inv_id}')
            cur.execute("UPDATE client_invoices SET reminder_past_due_logged_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (inv_id,))

def fetch_client_invoices(cur, client_id: str, limit: int = 200):
    cur.execute(
        """
        SELECT id::text, invoice_number, invoice_type, issue_date::text, due_date::text, item_description,
               amount::text, status, created_at::text, paid_at::text, notes, pdf_file_path,
               service_period_start::text, service_period_end::text, is_automated
        FROM client_invoices
        WHERE client_id = %s::uuid
        ORDER BY due_date DESC NULLS LAST, created_at DESC
        LIMIT %s
        """,
        (client_id, limit)
    )
    rows = []
    for r in cur.fetchall():
        rows.append({
            'id': r[0], 'invoice_number': r[1], 'invoice_type': r[2], 'issue_date': r[3], 'due_date': r[4],
            'item_description': r[5], 'amount': r[6], 'amount_display': format_currency(r[6]), 'status': r[7],
            'created_at': r[8], 'paid_at': r[9], 'notes': r[10], 'pdf_file_path': r[11],
            'service_period_start': r[12], 'service_period_end': r[13], 'is_automated': bool(r[14]),
        })
    return rows


def build_billing_snapshot(profile: dict, invoices: list[dict]):
    today = date.today()
    unpaid = [i for i in invoices if (i.get('status') or 'unpaid') != 'paid']
    next_due = None
    oldest_past_due = None
    for inv in unpaid:
        dd = _to_date(inv.get('due_date'))
        if not dd:
            continue
        if dd < today:
            if oldest_past_due is None or dd < oldest_past_due:
                oldest_past_due = dd
        else:
            if next_due is None or dd < next_due:
                next_due = dd
    invoice_status = 'Past Due' if oldest_past_due else 'Current'
    status_color = 'past-due' if oldest_past_due else 'current'
    return {
        'pricing_plan': profile.get('pricing_plan') or '—',
        'startup_fee': format_currency(profile.get('initial_fee') or 0),
        'monthly_fee': format_currency(profile.get('monthly_fee') or 0),
        'billing_group': profile.get('billing_group') or '—',
        'first_payment_date': format_short_date(profile.get('first_payment_date')) or '—',
        'invoice_status': invoice_status,
        'invoice_status_color': status_color,
        'next_due_date': format_short_date(next_due) if next_due else '—',
        'oldest_past_due': format_short_date(oldest_past_due) if oldest_past_due else '—',
    }


def _invoice_pdf_path(client_id: str, invoice_number: str):
    client_dir = os.path.join(INVOICE_DIR, client_id)
    os.makedirs(client_dir, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', invoice_number)
    return os.path.join(client_dir, f"{safe}.pdf")


def generate_invoice_pdf(cur, invoice_id: str):
    cur.execute(
        """
        SELECT ci.id::text, ci.client_id::text, ci.invoice_number, ci.invoice_type, ci.issue_date, ci.due_date,
               ci.item_description, ci.amount::text, ci.notes, ci.pdf_file_path
        FROM client_invoices ci
        WHERE ci.id = %s::uuid
        """,
        (invoice_id,)
    )
    row = cur.fetchone()
    if not row:
        raise ValueError('Invoice not found.')
    (iid, client_id, inv_no, inv_type, issue_date, due_date, item_desc, amount, notes, pdf_path) = row
    profile = fetch_client_profile(cur, client_id, include_sensitive=True) or {}
    out_path = pdf_path or _invoice_pdf_path(client_id, inv_no)
    story = []
    doc = SimpleDocTemplate(out_path, pagesize=LETTER, leftMargin=0.75*inch, rightMargin=0.75*inch, topMargin=0.6*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    normal = ParagraphStyle('invnormal', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=13)
    heading = ParagraphStyle('invhead', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=14, leading=17, spaceAfter=10)
    if COMPANY_LOGO_PATH and os.path.exists(COMPANY_LOGO_PATH):
        try:
            story.append(RLImage(COMPANY_LOGO_PATH, width=1.4*inch, height=0.7*inch))
            story.append(Spacer(1, 6))
        except Exception:
            pass
    story.append(Paragraph(f"<b>{escape(SENDER_NAME)}</b>", heading))
    company_lines = [escape(COMPANY_ADDRESS1)]
    if COMPANY_ADDRESS2:
        company_lines.append(escape(COMPANY_ADDRESS2))
    company_lines.append(f"Phone: {escape(COMPANY_PHONE)}")
    company_lines.append(f"Email: {escape(COMPANY_EMAIL)}")
    story.append(Paragraph('<br/>'.join(company_lines), normal))
    story.append(Spacer(1, 12))
    client_name = profile.get('client_name') or ' '.join([x for x in [profile.get('first_name'), profile.get('middle_name'), profile.get('last_name')] if x]).strip()
    client_lines = [escape(client_name)]
    if profile.get('address_line1'):
        client_lines.append(escape(profile.get('address_line1')))
    if profile.get('address_line2'):
        client_lines.append(escape(profile.get('address_line2')))
    loc = ', '.join([x for x in [profile.get('city'), profile.get('state')] if x]).strip(', ')
    if profile.get('zip'):
        loc = f"{loc} {profile.get('zip')}".strip()
    if loc:
        client_lines.append(escape(loc))
    if profile.get('phone'):
        client_lines.append(f"Phone: {escape(profile.get('phone'))}")
    if profile.get('email'):
        client_lines.append(f"Email: {escape(profile.get('email'))}")
    story.append(Paragraph('<br/>'.join(client_lines), normal))
    story.append(Spacer(1, 12))
    meta = [
        ['Invoice #', inv_no],
        ['Invoice Date', format_short_date(issue_date)],
        ['Due Date', format_short_date(due_date)],
        ['Terms', 'Due on receipt'],
    ]
    table = Table(meta, colWidths=[1.3*inch, 4.9*inch])
    table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),10),('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('BACKGROUND',(0,0),(0,-1),colors.whitesmoke)]))
    story.append(table)
    story.append(Spacer(1, 14))
    items = [['Item', 'Amount'], [item_desc or inv_type.title(), format_currency(amount)]]
    items_tbl = Table(items, colWidths=[5.5*inch, 1.0*inch])
    items_tbl.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),10),('BACKGROUND',(0,0),(-1,0),colors.whitesmoke),('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ALIGN',(1,1),(1,-1),'RIGHT')]))
    story.append(items_tbl)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>Total:</b> {escape(format_currency(amount))}", normal))
    story.append(Spacer(1, 10))
    note_line = 'Payment is for work completed the prior month.' if inv_type == 'monthly' else 'Due on receipt.'
    story.append(Paragraph(note_line, normal))
    if notes:
        story.append(Spacer(1, 8))
        story.append(Paragraph(escape(notes), normal))
    doc.build(story)
    cur.execute("UPDATE client_invoices SET pdf_file_path = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (out_path, invoice_id))
    return out_path


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
    if profile:
        ensure_client_invoices(cur, client_id, profile)
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
    latest_analysis_by_bureau = fetch_latest_analysis_by_bureau(cur, client_id)
    upload_requests = fetch_upload_requests(cur, client_id, 50)
    document_source_options = fetch_document_source_options(cur)
    associated_clients = fetch_associated_clients(cur, client_id)
    client_switch_choices = fetch_client_switch_choices(cur, client_id)
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
    invoices = fetch_client_invoices(cur, client_id, 200)
    billing_snapshot = build_billing_snapshot(profile, invoices)
    invoice_items = fetch_invoice_items(cur)

    return (
        dashboard, profile, score_groups, credentials, notes, appointments, followups,
        redispute_events, documents, documents_by_section, dispute_source_groups,
        last_document_selection, latest_analysis_by_bureau, upload_requests, document_source_options,
        associated_clients, client_switch_choices, referral_partners, account_disputes,
        personal_info_disputes, inquiry_disputes, dispute_metrics, dispute_round_defaults,
        saved_letters, client_emails, account_name_options, credit_products, outbound_referrals, invoices, billing_snapshot, invoice_items
    )


def render_client_workspace(request: Request, client_id: str, message: str = "", error: str = "", reveal_cred_id: Optional[str] = None, active_tab: Optional[str] = None, dispute_bureau: Optional[str] = None, open_letter_url: str = "", edit_credit_product_id: Optional[str] = None):
    tab_defs = [
        ("billing", "Billing"),
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
    chosen_tab = request.query_params.get("tab") or active_tab or "disputes"
    if chosen_tab in {"personal_info", "overview", "profile"}:
        chosen_tab = "disputes"
    if chosen_tab not in valid_tabs:
        chosen_tab = "disputes"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            (
                dashboard, profile, score_groups, credentials, notes, appointments, followups,
                redispute_events, documents, documents_by_section, dispute_source_groups,
                last_document_selection, latest_analysis_by_bureau, upload_requests, document_source_options,
                associated_clients, client_switch_choices, referral_partners, account_disputes,
                personal_info_disputes, inquiry_disputes, dispute_metrics, dispute_round_defaults,
                saved_letters, client_emails, account_name_options, credit_products, outbound_referrals,
                invoices, billing_snapshot, invoice_items
            ) = load_client_workspace_context(cur, client_id, reveal_cred_id)
        if not profile:
            return templates.TemplateResponse("index.html", {"request": request, "error": "Client not found."})
        selected_dispute_bureau = (dispute_bureau or request.query_params.get("dispute_bureau") or "equifax").strip().lower()
        selected_edit_credit_product_id = edit_credit_product_id or request.query_params.get("edit_credit_product_id") or ""
        edit_credit_product = fetch_credit_product(cur, client_id, selected_edit_credit_product_id) if selected_edit_credit_product_id else None
        if selected_dispute_bureau not in BUREAUS:
            selected_dispute_bureau = "equifax"
        bureau_dispute_summaries = build_bureau_dispute_summaries(
            profile, account_disputes, personal_info_disputes, inquiry_disputes,
            saved_letters, redispute_events, int(dispute_round_defaults.get('current_round_number', 1) or 1)
        )
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
                "latest_analysis_by_bureau": latest_analysis_by_bureau,
                "upload_requests": upload_requests,
                "document_source_options": document_source_options,
                "associated_clients": associated_clients,
                "client_switch_choices": client_switch_choices,
                "referral_partners": referral_partners,
                "account_disputes": account_disputes,
                "personal_info_disputes": personal_info_disputes,
                "inquiry_disputes": inquiry_disputes,
                "dispute_metrics": dispute_metrics,
                "dispute_round_defaults": dispute_round_defaults,
                "selected_dispute_bureau": selected_dispute_bureau,
                "bureau_dispute_summaries": bureau_dispute_summaries,
                "saved_letters": saved_letters,
                "client_emails": client_emails,
                "account_name_options": account_name_options,
                "credit_products": credit_products,
                "outbound_referrals": outbound_referrals,
                "invoices": invoices,
                "billing_snapshot": billing_snapshot,
                "invoice_items": invoice_items,
                "edit_credit_product": edit_credit_product,
                "company_name": SENDER_NAME,
                "company_phone": COMPANY_PHONE,
                "company_email": COMPANY_EMAIL,
                "company_address1": COMPANY_ADDRESS1,
                "company_address2": COMPANY_ADDRESS2,
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
                "document_section_category_options": DOCUMENT_SECTION_CATEGORY_OPTIONS,
                "default_document_category_by_section": DEFAULT_DOCUMENT_CATEGORY_BY_SECTION,
                "document_source_options": document_source_options,
                "upload_request_type_options": UPLOAD_REQUEST_TYPE_OPTIONS,
                "doc_review_statuses": DOC_REVIEW_STATUSES,
                "email_directions": EMAIL_DIRECTIONS,
                "email_statuses": EMAIL_STATUSES,
                "email_types": EMAIL_TYPES,
                "email_sources": EMAIL_SOURCES,
                "tabs": tab_defs,
                "active_tab": chosen_tab,
                "message": message,
                "error": error,
                "open_letter_url": open_letter_url,
            }
        )
    finally:
        conn.close()



@app.post('/ui/generate-startup-invoice', response_class=HTMLResponse)
def ui_generate_startup_invoice(request: Request, client_id: str = Form(...), active_tab: str = Form('billing')):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            profile = fetch_client_profile(cur, client_id, include_sensitive=False)
            amount = _safe_decimal(profile.get('initial_fee'))
            if amount <= 0:
                return render_client_workspace(request, client_id, error='Startup fee is not set.', active_tab=active_tab)
            iid = _create_invoice_row(cur, client_id, 'startup', amount, date.today(), date.today(), 'Startup fee', notes='Due on receipt.', is_automated=False)
            generate_invoice_pdf(cur, iid)
        return render_client_workspace(request, client_id, message='Startup invoice generated.', active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)
    finally:
        conn.close()


@app.post('/ui/generate-manual-invoice', response_class=HTMLResponse)
def ui_generate_manual_invoice(
    request: Request,
    client_id: str = Form(...),
    invoice_type: str = Form('other'),
    item_description: str = Form(...),
    amount: str = Form(...),
    due_date: str = Form(...),
    notes: str = Form(''),
    active_tab: str = Form('billing'),
):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            due = _to_date(due_date)
            if not due:
                raise ValueError('Due date is required.')
            iid = _create_invoice_row(cur, client_id, invoice_type, _safe_decimal(amount), date.today(), due, item_description, notes=notes, is_automated=False)
            generate_invoice_pdf(cur, iid)
        return render_client_workspace(request, client_id, message='Invoice generated.', active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)
    finally:
        conn.close()


@app.post('/ui/toggle-invoice-paid', response_class=HTMLResponse)
def ui_toggle_invoice_paid(request: Request, client_id: str = Form(...), invoice_id: str = Form(...), mark_paid: str = Form('0'), active_tab: str = Form('billing')):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            if mark_paid == '1':
                cur.execute("UPDATE client_invoices SET status = 'paid', paid_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = %s::uuid AND client_id = %s::uuid", (invoice_id, client_id))
            else:
                cur.execute("UPDATE client_invoices SET status = 'unpaid', paid_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = %s::uuid AND client_id = %s::uuid", (invoice_id, client_id))
        return render_client_workspace(request, client_id, message='Invoice updated.', active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)
    finally:
        conn.close()


@app.get('/ui/invoice/{invoice_id}/pdf')
def ui_invoice_pdf(invoice_id: str):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            file_path = generate_invoice_pdf(cur, invoice_id)
        return FileResponse(file_path, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="{os.path.basename(file_path)}"'})
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



def _prefill_name_from_query(q: str) -> dict:
    q = (q or "").strip()
    if not q:
        return {"first_name": "", "last_name": ""}
    parts = [p for p in re.split(r"\s+", q) if p]
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": ""}
    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


def _get_public_tables_with_client_id(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name = 'client_id'
          AND table_name <> 'clients'
        ORDER BY table_name
    """)
    return [r[0] for r in cur.fetchall()]


def _safe_merge_client_records(cur, source_client_id: str, target_client_id: str):
    if source_client_id == target_client_id:
        raise ValueError("Source and target client cannot be the same.")

    cur.execute("""
        SELECT first_name, middle_name, last_name, suffix, phone, primary_email, email, secondary_email,
               date_of_birth, ssn_last4, ssn_full_enc, is_active, lifecycle_status
        FROM clients WHERE id = %s
    """, (source_client_id,))
    src = cur.fetchone()
    cur.execute("""
        SELECT first_name, middle_name, last_name, suffix, phone, primary_email, email, secondary_email,
               date_of_birth, ssn_last4, ssn_full_enc, is_active, lifecycle_status
        FROM clients WHERE id = %s
    """, (target_client_id,))
    tgt = cur.fetchone()
    if not src or not tgt:
        raise ValueError("Source or target client was not found.")

    updates = {
        "middle_name": tgt[1] or src[1],
        "suffix": tgt[3] or src[3],
        "phone": tgt[4] or src[4],
        "primary_email": tgt[5] or src[5],
        "email": tgt[6] or src[6] or tgt[5] or src[5],
        "secondary_email": tgt[7] or src[7],
        "date_of_birth": tgt[8] or src[8],
        "ssn_last4": tgt[9] or src[9],
        "ssn_full_enc": tgt[10] or src[10],
        "is_active": bool(tgt[11] or src[11]),
        "lifecycle_status": tgt[12] or src[12] or "candidate",
    }
    cur.execute("""
        UPDATE clients
        SET middle_name = %s,
            suffix = %s,
            phone = %s,
            primary_email = %s,
            email = %s,
            secondary_email = %s,
            date_of_birth = %s,
            ssn_last4 = %s,
            ssn_full_enc = %s,
            is_active = %s,
            lifecycle_status = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (
        updates["middle_name"], updates["suffix"], updates["phone"], updates["primary_email"],
        updates["email"], updates["secondary_email"], updates["date_of_birth"], updates["ssn_last4"],
        updates["ssn_full_enc"], updates["is_active"], updates["lifecycle_status"], target_client_id
    ))

    for table_name in _get_public_tables_with_client_id(cur):
        q = sql.SQL("UPDATE {} SET client_id = %s WHERE client_id = %s").format(sql.Identifier(table_name))
        cur.execute(q, (target_client_id, source_client_id))

    cur.execute("UPDATE client_associations SET primary_client_id = %s WHERE primary_client_id = %s", (target_client_id, source_client_id))
    cur.execute("UPDATE client_associations SET associated_client_id = %s WHERE associated_client_id = %s", (target_client_id, source_client_id))
    cur.execute("DELETE FROM client_associations WHERE primary_client_id = associated_client_id")
    cur.execute("""
        DELETE FROM client_associations a
        USING client_associations b
        WHERE a.ctid < b.ctid
          AND a.primary_client_id = b.primary_client_id
          AND a.associated_client_id = b.associated_client_id
          AND COALESCE(a.relationship_label,'') = COALESCE(b.relationship_label,'')
    """)

    cur.execute("""
        SELECT 1 FROM client_addresses
        WHERE client_id = %s AND is_current = TRUE
        LIMIT 1
    """, (target_client_id,))
    tgt_addr = cur.fetchone()
    if not tgt_addr:
        cur.execute("""
            INSERT INTO client_addresses (client_id, is_current, line1, apt_unit, line2, city, state, zip)
            SELECT %s, TRUE, line1, apt_unit, line2, city, state, zip
            FROM client_addresses
            WHERE client_id = %s AND is_current = TRUE
            ORDER BY created_at DESC
            LIMIT 1
        """, (target_client_id, source_client_id))

    cur.execute("DELETE FROM client_associations WHERE primary_client_id = %s OR associated_client_id = %s", (source_client_id, source_client_id))
    cur.execute("DELETE FROM clients WHERE id = %s", (source_client_id,))


def _safe_delete_client_record(cur, client_id: str):
    for table_name in _get_public_tables_with_client_id(cur):
        q = sql.SQL("DELETE FROM {} WHERE client_id = %s").format(sql.Identifier(table_name))
        cur.execute(q, (client_id,))
    cur.execute("DELETE FROM client_associations WHERE primary_client_id = %s OR associated_client_id = %s", (client_id, client_id))
    cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))


@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/ui/search", response_class=HTMLResponse)
def ui_search(request: Request, q: str = ""):
    q = (q or "").strip()
    if not q:
        return templates.TemplateResponse("index.html", {"request": request, "error": "Enter a search term."})

    like = f"%{q}%"
    digits = re.sub(r"\D+", "", q)
    results = []

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            try:
                if digits and len(digits) >= 4:
                    cur.execute("""
                        SELECT id::text, first_name, last_name, phone, email, status::text
                        FROM clients
                        WHERE COALESCE(first_name,'') ILIKE %s
                           OR COALESCE(last_name,'') ILIKE %s
                           OR COALESCE(phone,'') ILIKE %s
                           OR COALESCE(email,'') ILIKE %s
                           OR COALESCE(status::text,'') ILIKE %s
                           OR COALESCE(ssn_last4,'') ILIKE %s
                        ORDER BY last_name NULLS LAST, first_name NULLS LAST
                        LIMIT 50
                    """, (like, like, like, like, like, f"%{digits[-4:]}%"))
                else:
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
                        "id": r[0], "first_name": r[1], "last_name": r[2],
                        "phone": r[3], "email": r[4], "status": r[5]
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
                        "id": r[0], "first_name": r[1], "last_name": r[2],
                        "phone": r[3], "email": None, "status": r[4]
                    })
        conn.close()
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "q": q,
                "results": results,
                "results_count": len(results),
                "no_results": len(results) == 0,
                "prefill": _prefill_name_from_query(q),
            }
        )
    except Exception as e:
        return templates.TemplateResponse("index.html", {"request": request, "error": str(e), "q": q, "prefill": _prefill_name_from_query(q)})





@app.get("/ui/client/new", response_class=HTMLResponse)
def ui_new_client_form(request: Request, q: str = ""):
    prefill = _prefill_name_from_query(q)
    return templates.TemplateResponse("new_client.html", {"request": request, "q": q, "prefill": prefill})


@app.post("/ui/client/create", response_class=HTMLResponse)
def ui_create_client(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(""),
    primary_phone: str = Form(""),
    primary_email: str = Form(""),
    date_of_birth: str = Form(""),
    ssn_full: str = Form(""),
    address_line1: str = Form(""),
    apt_unit: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    is_active: bool = Form(False),
    lifecycle_status: str = Form("candidate"),
):
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            dob_formatted = normalize_dob_input(date_of_birth) if date_of_birth else ""
            dob_iso = parse_date_input(dob_formatted) if dob_formatted else ""
            phone_normalized = normalize_phone_input(primary_phone) if primary_phone else ""
            ssn_normalized = normalize_ssn_input(ssn_full) if ssn_full else ""
            ssn_digits = only_digits(ssn_normalized)
            ssn_last4 = ssn_digits[-4:] if len(ssn_digits) >= 4 else None
            ssn_full_enc = enc_text(ssn_normalized)
            cur.execute("""
                INSERT INTO clients (
                    first_name, last_name, phone, primary_email, email, date_of_birth,
                    ssn_last4, ssn_full_enc, is_active, lifecycle_status,
                    consultation_fee, initial_fee, monthly_fee, pricing_plan,
                    billing_group, preferred_payment_method, preferred_email_choice
                )
                VALUES (
                    %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                RETURNING id::text
            """, (
                first_name, last_name, phone_normalized, primary_email, primary_email, dob_iso,
                ssn_last4, ssn_full_enc, is_active, lifecycle_status,
                100.0, 199.0, 125.0, "standard program",
                "15th", "zelle", "primary"
            ))
            client_id = cur.fetchone()[0]
            if any([address_line1, apt_unit, city, state, zip_code]):
                cur.execute("""
                    INSERT INTO client_addresses
                      (client_id, is_current, line1, apt_unit, city, state, zip)
                    VALUES
                      (%s, TRUE, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''))
                """, (client_id, address_line1, apt_unit, city, state, zip_code))
        conn.close()
        return render_client_workspace(request, client_id, message="New client profile created.")
    except Exception as e:
        return templates.TemplateResponse("new_client.html", {"request": request, "error": str(e), "prefill": {"first_name": first_name, "last_name": last_name}})


@app.get("/ui/client/{client_id}/merge", response_class=HTMLResponse)
def ui_client_merge_form(request: Request, client_id: str, q: str = ""):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            source = fetch_client_profile(cur, client_id, include_sensitive=True)
            results = []
            if q:
                like = f"%{q.strip()}%"
                cur.execute("""
                    SELECT id::text, first_name, last_name, phone, primary_email
                    FROM clients
                    WHERE id <> %s
                      AND (
                        COALESCE(first_name,'') ILIKE %s OR
                        COALESCE(last_name,'') ILIKE %s OR
                        COALESCE(phone,'') ILIKE %s OR
                        COALESCE(primary_email,'') ILIKE %s OR
                        COALESCE(email,'') ILIKE %s
                      )
                    ORDER BY last_name NULLS LAST, first_name NULLS LAST
                    LIMIT 25
                """, (client_id, like, like, like, like, like))
                results = [{"id":r[0], "first_name":r[1], "last_name":r[2], "phone":r[3], "email":r[4]} for r in cur.fetchall()]
        if not source:
            return templates.TemplateResponse("index.html", {"request": request, "error": "Client not found."})
        return templates.TemplateResponse("client_merge.html", {"request": request, "client_id": client_id, "source": source, "q": q, "results": results})
    finally:
        conn.close()


@app.post("/ui/client/{client_id}/merge", response_class=HTMLResponse)
def ui_client_merge_execute(request: Request, client_id: str, target_client_id: str = Form(...)):
    conn = get_conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            _safe_merge_client_records(cur, client_id, target_client_id)
        conn.commit()
        return render_client_workspace(request, target_client_id, message="Duplicate profile merged successfully.")
    except Exception as e:
        conn.rollback()
        return templates.TemplateResponse("index.html", {"request": request, "error": f"Merge failed: {e}"})
    finally:
        conn.close()


@app.post("/ui/client/{client_id}/delete", response_class=HTMLResponse)
def ui_client_delete(request: Request, client_id: str):
    conn = get_conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            _safe_delete_client_record(cur, client_id)
        conn.commit()
        return templates.TemplateResponse("index.html", {"request": request, "message": "Client profile deleted."})
    except Exception as e:
        conn.rollback()
        return render_client_workspace(request, client_id, error=f"Delete failed: {e}")
    finally:
        conn.close()


@app.post("/ui/client", response_class=HTMLResponse)
def ui_client_post(request: Request, client_id: str = Form(...)):
    return render_client_workspace(request, client_id, active_tab="disputes")


@app.get("/ui/client/{client_id}", response_class=HTMLResponse)
def ui_client_get(request: Request, client_id: str, active_tab: str = Query(''), edit_credit_product_id: str = Query('')):
    return render_client_workspace(request, client_id, active_tab=active_tab or None, edit_credit_product_id=edit_credit_product_id or None)


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
            dob_formatted = normalize_dob_input(date_of_birth) if date_of_birth else ""
            dob_iso = parse_date_input(dob_formatted) if dob_formatted else ""
            primary_phone_normalized = normalize_phone_input(primary_phone) if primary_phone else ""
            secondary_phone_normalized = normalize_phone_input(secondary_phone) if secondary_phone else ""
            ssn_normalized = normalize_ssn_input(ssn_full) if ssn_full else ""
            ssn_last4 = only_digits(ssn_normalized)[-4:] if ssn_normalized else None
            ssn_full_enc = enc_text(ssn_normalized) if ssn_normalized else None

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
                primary_phone_normalized, primary_phone_type,
                secondary_phone_normalized, secondary_phone_type,
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
async def ui_add_account_dispute(request: Request):
    form = await request.form()
    client_id = str(form.get("client_id", ""))
    active_tab = str(form.get("active_tab", "disputes"))
    bureau = str(form.get("bureau", "")).strip().lower()
    creditor_names = form.getlist("creditor_name") or ([form.get("creditor_name")] if form.get("creditor_name") is not None else [])
    account_numbers = form.getlist("account_number") or form.getlist("account_number_last4")
    if not account_numbers and form.get("account_number_last4") is not None:
        account_numbers = [form.get("account_number_last4")]
    dispute_reasons = form.getlist("dispute_reason") or ([form.get("dispute_reason")] if form.get("dispute_reason") is not None else [])
    requested_actions = form.getlist("requested_action") or ([form.get("requested_action")] if form.get("requested_action") is not None else [])
    notes_list = form.getlist("notes") or ([form.get("notes")] if form.get("notes") is not None else [])

    try:
        conn = get_conn(); conn.autocommit = True
        inserted = 0
        with conn.cursor() as cur:
            round_added = get_current_round_number_for_bureau(cur, client_id, bureau)
            for creditor_name, account_number, dispute_reason, requested_action, notes in zip_longest(
                creditor_names, account_numbers, dispute_reasons, requested_actions, notes_list, fillvalue=""
            ):
                creditor_name = (creditor_name or "").strip()
                account_number = (account_number or "").strip()
                dispute_reason = (dispute_reason or "other").strip() or "other"
                requested_action = (requested_action or "investigate").strip() or "investigate"
                notes = (notes or "").strip()
                if not creditor_name and not account_number:
                    continue
                if not creditor_name:
                    raise ValueError("Each account row needs a creditor name.")
                cur.execute("""
                    INSERT INTO client_account_disputes
                      (client_id, creditor_name, account_number_last4, dispute_reason, requested_action, notes,
                       is_active, bureau, status, first_seen_date, removed_date, is_negative, round_added, last_included_round)
                    VALUES
                      (%s, %s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), TRUE, %s, 'open', NULL, NULL, TRUE, %s, %s)
                """, (client_id, creditor_name, account_number, dispute_reason, requested_action, notes, bureau, round_added, round_added))
                inserted += 1
        conn.close()
        if inserted == 0:
            return render_client_workspace(request, client_id, error="Add at least one account row before saving.", active_tab=active_tab)
        return render_client_workspace(request, client_id, message=f"{inserted} account disputed item(s) saved for {bureau.title()} round {round_added}.", active_tab=active_tab)
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
                    status = 'open',
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
async def ui_add_personal_info_dispute(request: Request):
    form = await request.form()
    client_id = str(form.get("client_id", ""))
    active_tab = str(form.get("active_tab", "disputes"))
    bureau = str(form.get("bureau", "")).strip().lower()
    info_type = str(form.get("info_type", "other")).strip().lower() or "other"
    if info_type == "telephone":
        info_type = "phone"
    reported_values = form.getlist("reported_value") or ([form.get("reported_value")] if form.get("reported_value") is not None else [])
    correct_values = form.getlist("correct_value") or ([form.get("correct_value")] if form.get("correct_value") is not None else [])
    requested_actions = form.getlist("requested_action") or ([form.get("requested_action")] if form.get("requested_action") is not None else [])
    notes_list = form.getlist("notes") or ([form.get("notes")] if form.get("notes") is not None else [])

    try:
        conn = get_conn(); conn.autocommit = True
        inserted = 0
        with conn.cursor() as cur:
            round_added = get_current_round_number_for_bureau(cur, client_id, bureau)
            for reported_value, correct_value, requested_action, notes in zip_longest(
                reported_values, correct_values, requested_actions, notes_list, fillvalue=""
            ):
                reported_value = (reported_value or "").strip()
                correct_value = (correct_value or "").strip()
                requested_action = (requested_action or "delete").strip() or "delete"
                notes = (notes or "").strip()
                if not reported_value:
                    continue
                cur.execute("""
                    INSERT INTO client_personal_info_disputes
                      (client_id, bureau, info_type, reported_value, correct_value, requested_action,
                       notes, is_active, round_added, last_included_round, removed_date, removed_round)
                    VALUES
                      (%s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), %s, NULLIF(%s,''), TRUE, %s, %s, NULL, NULL)
                """, (client_id, bureau, info_type, reported_value, correct_value, requested_action, notes, round_added, round_added))
                inserted += 1
        conn.close()
        if inserted == 0:
            return render_client_workspace(request, client_id, error="Add at least one personal-info row before saving.", active_tab=active_tab)
        return render_client_workspace(request, client_id, message=f"{inserted} personal-info disputed item(s) saved for {bureau.title()} round {round_added}.", active_tab=active_tab)
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
async def ui_add_inquiry_dispute(request: Request):
    form = await request.form()
    client_id = str(form.get("client_id", ""))
    active_tab = str(form.get("active_tab", "disputes"))
    bureau = str(form.get("bureau", "")).strip().lower()
    furnisher_names = form.getlist("furnisher_name") or ([form.get("furnisher_name")] if form.get("furnisher_name") is not None else [])
    inquiry_dates = form.getlist("inquiry_date") or ([form.get("inquiry_date")] if form.get("inquiry_date") is not None else [])
    dispute_reasons = form.getlist("dispute_reason") or ([form.get("dispute_reason")] if form.get("dispute_reason") is not None else [])
    requested_actions = form.getlist("requested_action") or ([form.get("requested_action")] if form.get("requested_action") is not None else [])
    notes_list = form.getlist("notes") or ([form.get("notes")] if form.get("notes") is not None else [])

    try:
        conn = get_conn(); conn.autocommit = True
        inserted = 0
        with conn.cursor() as cur:
            round_added = get_current_round_number_for_bureau(cur, client_id, bureau)
            for furnisher_name, inquiry_date, dispute_reason, requested_action, notes in zip_longest(
                furnisher_names, inquiry_dates, dispute_reasons, requested_actions, notes_list, fillvalue=""
            ):
                furnisher_name = (furnisher_name or "").strip()
                inquiry_date = (inquiry_date or "").strip()
                dispute_reason = (dispute_reason or "not my inquiry").strip() or "not my inquiry"
                requested_action = (requested_action or "delete").strip() or "delete"
                notes = (notes or "").strip()
                if not furnisher_name:
                    continue
                cur.execute("""
                    INSERT INTO client_inquiry_disputes
                      (client_id, bureau, furnisher_name, inquiry_date, dispute_reason, requested_action,
                       notes, is_active, round_added, last_included_round, removed_date, removed_round)
                    VALUES
                      (%s, NULLIF(%s,''), %s, NULLIF(%s,'')::date, %s, %s, NULLIF(%s,''), TRUE, %s, %s, NULL, NULL)
                """, (client_id, bureau, furnisher_name, inquiry_date, dispute_reason, requested_action, notes, round_added, round_added))
                inserted += 1
        conn.close()
        if inserted == 0:
            return render_client_workspace(request, client_id, error="Add at least one inquiry row before saving.", active_tab=active_tab)
        return render_client_workspace(request, client_id, message=f"{inserted} inquiry disputed item(s) saved for {bureau.title()} round {round_added}.", active_tab=active_tab)
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
    credit_limit: str = Form(''),
    due_date: str = Form(''),
    statement_date: str = Form(''),
    secured_deposit_amount: str = Form(''),
    origination_date: str = Form(''),
    notes: str = Form(''),
    edit_item_id: str = Form(''),
    active_tab: str = Form('credit_products'),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            if edit_item_id:
                cur.execute("""
                    UPDATE client_credit_products
                    SET lender_name = %s, credit_type = %s, credit_limit = NULLIF(%s,'')::numeric,
                        due_date = NULLIF(%s,'')::smallint, cutoff_date = NULLIF(%s,'')::smallint,
                        secured_deposit_amount = NULLIF(%s,'')::numeric, origination_date = NULLIF(%s,'')::date,
                        notes = NULLIF(%s,''), updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s::uuid AND client_id = %s::uuid
                """, (lender_name, credit_type, credit_limit, due_date, statement_date, secured_deposit_amount, origination_date, notes, edit_item_id, client_id))
                msg = 'Credit product updated.'
            else:
                cur.execute("""
                    INSERT INTO client_credit_products
                      (client_id, lender_name, credit_type, credit_limit, due_date, cutoff_date, secured_deposit_amount, origination_date, notes)
                    VALUES
                      (%s, %s, %s, NULLIF(%s,'')::numeric, NULLIF(%s,'')::smallint, NULLIF(%s,'')::smallint,
                       NULLIF(%s,'')::numeric, NULLIF(%s,'')::date, NULLIF(%s,''))
                """, (client_id, lender_name, credit_type, credit_limit, due_date, statement_date, secured_deposit_amount, origination_date, notes))
                msg = 'Credit product saved.'
        conn.close()
        return render_client_workspace(request, client_id, message=msg, active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post('/ui/delete-credit-product', response_class=HTMLResponse)
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
    request_type: str = Form("id_proof_intake"),
    expires_days: int = Form(30),
    active_tab: str = Form("documents"),
):
    try:
        token = str(uuid4())
        allowed_doc_types = ""
        if request_type == "id_proof_intake":
            allowed_doc_types = ",".join([opt[0] for opt in DOCUMENT_SECTION_CATEGORY_OPTIONS.get("ids_proof_of_address", [])])
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


@app.post("/ui/save-client-association", response_class=HTMLResponse)
def ui_save_client_association(
    request: Request,
    client_id: str = Form(...),
    associated_client_id: str = Form(...),
    relationship_label: str = Form("associated"),
    active_tab: str = Form("profile"),
):
    try:
        if client_id == associated_client_id:
            raise ValueError("A client cannot be associated to the same record.")
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO client_associations (client_id, associated_client_id, relationship_label)
                VALUES (%s::uuid, %s::uuid, NULLIF(%s,''))
                ON CONFLICT (client_id, associated_client_id)
                DO UPDATE SET relationship_label = EXCLUDED.relationship_label
            """, (client_id, associated_client_id, relationship_label))
            cur.execute("""
                INSERT INTO client_associations (client_id, associated_client_id, relationship_label)
                VALUES (%s::uuid, %s::uuid, NULLIF(%s,''))
                ON CONFLICT (client_id, associated_client_id)
                DO NOTHING
            """, (associated_client_id, client_id, relationship_label))
        conn.close()
        return render_client_workspace(request, client_id, message="Associated client saved.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/delete-client-association", response_class=HTMLResponse)
def ui_delete_client_association(
    request: Request,
    client_id: str = Form(...),
    associated_client_id: str = Form(...),
    active_tab: str = Form("profile"),
):
    try:
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DELETE FROM client_associations WHERE client_id = %s::uuid AND associated_client_id = %s::uuid", (client_id, associated_client_id))
            cur.execute("DELETE FROM client_associations WHERE client_id = %s::uuid AND associated_client_id = %s::uuid", (associated_client_id, client_id))
        conn.close()
        return render_client_workspace(request, client_id, message="Associated client removed.", active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.get("/upload/{token}", response_class=HTMLResponse)
def upload_page(request: Request, token: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ur.id::text, ur.client_id::text, COALESCE(ur.request_type,'general_upload'), COALESCE(ur.allowed_doc_types,''),
                       ur.expires_at, COALESCE(ur.status,'open')
                FROM client_upload_requests ur
                WHERE ur.token = %s::uuid
                LIMIT 1
            """, (token,))
            row = cur.fetchone()
            if not row:
                return HTMLResponse("<h3>Upload link not found.</h3>", status_code=404)
            profile = fetch_client_profile(cur, row[1], include_sensitive=False)
            source_options = fetch_document_source_options(cur)
        req = {
            "id": row[0], "client_id": row[1], "request_type": row[2], "allowed_doc_types": row[3],
            "expires_at": str(row[4]) if row[4] else '', "status": row[5],
            "client_name": " ".join([p for p in [profile.get("first_name", ""), profile.get("last_name", "")] if p]).strip(),
            "phone": profile.get("phone") or "",
            "email": profile.get("primary_email") or "",
            "address": ", ".join([p for p in [profile.get("address_line1") or "", profile.get("city") or "", profile.get("state") or "", profile.get("zip") or ""] if p]),
            "is_active": bool(profile.get("is_active", True)),
        }
        if req["request_type"] == "id_proof_intake":
            section_options = {"ids_proof_of_address": DOCUMENT_SECTION_CATEGORY_OPTIONS.get("ids_proof_of_address", [])}
            sections = [("ids_proof_of_address", "IDs & Proof of Address")]
            defaults = {"ids_proof_of_address": "driver_license_front"}
        else:
            section_options = DOCUMENT_SECTION_CATEGORY_OPTIONS
            sections = DOCUMENT_SECTIONS
            defaults = DEFAULT_DOCUMENT_CATEGORY_BY_SECTION
        return templates.TemplateResponse("upload.html", {"request": request, "token": token, "upload_request": req, "document_categories": DOCUMENT_CATEGORIES, "document_sections": sections, "document_section_category_options": section_options, "default_document_category_by_section": defaults, "document_source_options": source_options, "bureaus": BUREAUS})
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
    source_name_custom: str = Form(""),
    document_date: str = Form(""),
    reference_label: str = Form(""),
    reference_value: str = Form(""),
    statement_date: str = Form(""),
    expires_on: str = Form(""),
    collection_company_name: str = Form(""),
    collecting_for: str = Form(""),
    amount_claimed: str = Form(""),
    original_negative_date: str = Form(""),
    collector_acquired_date: str = Form(""),
    lender_name: str = Form(""),
    offer_code: str = Form(""),
    offer_website: str = Form(""),
    file: UploadFile = File(...),
):
    conn = get_conn(); conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, client_id::text, COALESCE(status,'open'), COALESCE(request_type,'general_upload')
                FROM client_upload_requests
                WHERE token = %s::uuid
                LIMIT 1
            """, (token,))
            row = cur.fetchone()
            if not row:
                return HTMLResponse("<h3>Upload link not found.</h3>", status_code=404)
            upload_request_id, client_id, status, request_type = row
            if status != 'open':
                return HTMLResponse("<h3>This upload link is no longer open.</h3>", status_code=400)
            if request_type == 'id_proof_intake':
                doc_section = 'ids_proof_of_address'
            _validate_document_dates(doc_category, statement_date=statement_date, expires_on=expires_on)
            client_dir = os.path.join(UPLOAD_DIR, client_id, "documents")
            os.makedirs(client_dir, exist_ok=True)
            safe_name = f"{uuid4()}_{os.path.basename(file.filename)}"
            save_path = os.path.join(client_dir, safe_name)
            with open(save_path, 'wb') as f:
                f.write(file.file.read())

            normalized_section = _normalize_document_section(doc_section, doc_category)
            final_source_name = (source_name_custom or source_name or "").strip()
            if final_source_name == "__custom__":
                final_source_name = ""
            if final_source_name:
                try:
                    cur.execute("INSERT INTO custom_document_sources (source_name) VALUES (%s) ON CONFLICT (source_name) DO NOTHING", (final_source_name,))
                except Exception:
                    pass
            stored_ref_label = (reference_label or _default_reference_label_for_bureau(bureau)).strip() if (reference_value or "").strip() else (reference_label or "").strip()
            match_status, match_score, match_summary = _assess_uploaded_document_for_client(cur, client_id, file.filename, save_path)
            review_status = 'needs_update' if match_status in {'partial_match', 'possible_mismatch'} else 'pending'
            remind_on = None
            if doc_category in PROOF_OF_ADDRESS_CATEGORIES and statement_date:
                remind_on = _coerce_iso_date(statement_date) + timedelta(days=75)

            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, doc_section, bureau, source_name,
                   document_date, reference_label, reference_value, statement_date, expires_on, remind_on,
                   review_status, review_notes, client_match_status, client_match_score, client_match_summary,
                   collection_company_name, collecting_for, amount_claimed, original_negative_date, collector_acquired_date,
                   lender_name, offer_code, offer_website, upload_request_id, created_at)
                VALUES
                  (%s, 'client_upload', %s, %s, NULLIF(%s,''), NULLIF(%s,''), %s, NULLIF(%s,''), NULLIF(%s,''),
                   NULLIF(%s,'')::date, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date, %s,
                   %s, %s, %s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date,
                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), %s::uuid, CURRENT_TIMESTAMP)
            """, (
                client_id, file.filename, save_path, description, doc_category, normalized_section, bureau, final_source_name,
                document_date, stored_ref_label, reference_value, statement_date, expires_on, remind_on,
                review_status, match_summary, match_status, match_score, match_summary,
                collection_company_name, collecting_for, amount_claimed, original_negative_date, collector_acquired_date,
                lender_name, offer_code, offer_website, upload_request_id,
            ))

            cur.execute("UPDATE client_upload_requests SET used_at = CURRENT_TIMESTAMP WHERE id = %s::uuid", (upload_request_id,))

        return HTMLResponse("<h3>Thank you. Your document was uploaded successfully.</h3>")
    except Exception as e:
        return HTMLResponse(f"<h3>Upload failed.</h3><p>{str(e)}</p>", status_code=400)
    finally:
        conn.close()



@app.post("/ui/add-document-meta", response_class=HTMLResponse)
def ui_add_document_meta(
    request: Request,
    client_id: str = Form(...),
    doc_section: str = Form("miscellaneous"),
    doc_category: str = Form("miscellaneous"),
    description: str = Form(""),
    bureau: str = Form(""),
    source_name: str = Form(""),
    source_name_custom: str = Form(""),
    document_date: str = Form(""),
    reference_label: str = Form(""),
    reference_value: str = Form(""),
    statement_date: str = Form(""),
    expires_on: str = Form(""),
    collection_company_name: str = Form(""),
    collecting_for: str = Form(""),
    amount_claimed: str = Form(""),
    original_negative_date: str = Form(""),
    collector_acquired_date: str = Form(""),
    lender_name: str = Form(""),
    offer_code: str = Form(""),
    offer_website: str = Form(""),
    active_tab: str = Form("documents"),
    file: UploadFile = File(None),
):
    try:
        normalized_section = _normalize_document_section(doc_section, doc_category)
        _validate_document_dates(doc_category, statement_date=statement_date, expires_on=expires_on)
        final_source_name = (source_name_custom or source_name or "").strip()
        if final_source_name == "__custom__":
            final_source_name = ""
        actual_file_name = ''
        stored_path = ''
        doc_type = 'manual_log'

        if file is not None and getattr(file, "filename", ""):
            client_dir = os.path.join(UPLOAD_DIR, client_id, "documents")
            os.makedirs(client_dir, exist_ok=True)
            safe_name = f"{uuid4()}_{os.path.basename(file.filename)}"
            stored_path = os.path.join(client_dir, safe_name)
            with open(stored_path, 'wb') as fh:
                fh.write(file.file.read())
            actual_file_name = file.filename
            doc_type = 'client_upload'

        if not actual_file_name:
            parts = [DOCUMENT_SECTION_LABELS.get(normalized_section, normalized_section).replace(" & ", " ").replace("/", " "), (bureau or "").title(), (document_date or statement_date or "").strip()]
            actual_file_name = " - ".join([p for p in parts if p]).strip() or "Document"

        stored_ref_label = (reference_label or _default_reference_label_for_bureau(bureau)).strip() if (reference_value or "").strip() else (reference_label or "").strip()
        remind_on = None
        if doc_category in PROOF_OF_ADDRESS_CATEGORIES and statement_date:
            remind_on = _coerce_iso_date(statement_date) + timedelta(days=75)

        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            if final_source_name:
                try:
                    cur.execute("INSERT INTO custom_document_sources (source_name) VALUES (%s) ON CONFLICT (source_name) DO NOTHING", (final_source_name,))
                except Exception:
                    pass
            match_status, match_score, match_summary = ('unknown', 0, '')
            if stored_path:
                match_status, match_score, match_summary = _assess_uploaded_document_for_client(cur, client_id, actual_file_name, stored_path)
            review_status = 'needs_update' if match_status in {'partial_match', 'possible_mismatch'} else 'pending'
            cur.execute("""
                INSERT INTO client_documents
                  (client_id, doc_type, file_name, file_path, description, doc_category, doc_section, bureau, source_name,
                   document_date, reference_label, reference_value, statement_date, expires_on, remind_on,
                   review_status, review_notes, client_match_status, client_match_score, client_match_summary,
                   collection_company_name, collecting_for, amount_claimed, original_negative_date, collector_acquired_date,
                   lender_name, offer_code, offer_website, created_at)
                VALUES
                  (%s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), %s, NULLIF(%s,''), NULLIF(%s,''),
                   NULLIF(%s,'')::date, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date, %s,
                   %s, NULLIF(%s,''), %s, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::date, NULLIF(%s,'')::date,
                   NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), CURRENT_TIMESTAMP)
            """, (
                client_id, doc_type, actual_file_name, stored_path, description, doc_category, normalized_section, bureau, final_source_name,
                document_date, stored_ref_label, reference_value, statement_date, expires_on, remind_on,
                review_status, match_summary, match_status, match_score, match_summary,
                collection_company_name, collecting_for, amount_claimed, original_negative_date, collector_acquired_date,
                lender_name, offer_code, offer_website,
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
                  (client_id, provider, direction, subject, body_text, from_email, to_email, cc_email, email_date, related_round, email_type, status, source, created_by)
                VALUES
                  (%s, 'manual_ui', %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,'')::timestamp, %s, %s, %s, %s, 'manual_ui')
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






@app.post("/ui/analyze-selected-documents", response_class=HTMLResponse)
async def ui_analyze_selected_documents(request: Request):
    form = await request.form()
    client_id = str(form.get('client_id', ''))
    active_tab = str(form.get('active_tab', 'disputes'))
    bureau = str(form.get('bureau', '')).strip().lower()
    selected_document_ids = [str(v).strip() for v in form.getlist('selected_document_ids') if str(v).strip()]
    try:
        if not selected_document_ids:
            raise ValueError('Choose at least one report or bureau response before running analysis.')
        messages = []
        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            for doc_id in selected_document_ids:
                result = analyze_selected_source_document(cur, client_id, doc_id)
                if result.get('error'):
                    messages.append(result['error'])
                else:
                    counts = result.get('counts', {})
                    msg = f"{bureau.title()}: staged {counts.get('accounts',0)} account item(s), {counts.get('personal_info',0)} personal-info item(s), and {counts.get('inquiries',0)} inquiry item(s)."
                    if result.get('client_match_status') in {'partial_match', 'possible_mismatch'} and result.get('client_match_summary'):
                        msg += ' ' + result.get('client_match_summary')
                    messages.append(msg)
        conn.close()
        return render_client_workspace(request, client_id, message=' '.join(messages), active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)



@app.post("/ui/import-analysis-items", response_class=HTMLResponse)
def ui_import_analysis_items(
    request: Request,
    client_id: str = Form(...),
    analysis_run_id: str = Form(...),
    bureau: str = Form(...),
    active_tab: str = Form("disputes"),
    selected_account_ids: Optional[List[str]] = Form(None),
    selected_personal_ids: Optional[List[str]] = Form(None),
    selected_inquiry_ids: Optional[List[str]] = Form(None),
):
    try:
        account_ids = selected_account_ids or []
        personal_ids = selected_personal_ids or []
        inquiry_ids = selected_inquiry_ids or []
        if not account_ids and not personal_ids and not inquiry_ids:
            raise ValueError("Choose at least one staged item to import.")

        conn = get_conn(); conn.autocommit = True
        imported_counts = {'accounts': 0, 'personal_info': 0, 'inquiries': 0, 'duplicates': 0}
        with conn.cursor() as cur:
            round_added = get_current_round_number_for_bureau(cur, client_id, bureau)

            for item_id in account_ids:
                cur.execute("""
                    SELECT COALESCE(creditor_name,''), COALESCE(account_number_fragment,''), COALESCE(suggested_reason,'other'), COALESCE(suggested_action,'investigate'),
                           COALESCE(strategy_note,''), COALESCE(imported_dispute_id::text,''), COALESCE(matched_dispute_id::text,'')
                    FROM dispute_analysis_accounts WHERE id = %s::uuid AND run_id = %s::uuid
                """, (item_id, analysis_run_id))
                row = cur.fetchone()
                if not row:
                    continue
                if row[5]:
                    imported_counts['duplicates'] += 1
                    continue
                existing_id = row[6]
                if existing_id:
                    cur.execute("""
                        UPDATE client_account_disputes
                        SET is_active = TRUE,
                            status = 'open',
                            removed_date = NULL,
                            removed_round = NULL,
                            dispute_reason = COALESCE(NULLIF(%s,''), dispute_reason),
                            requested_action = COALESCE(NULLIF(%s,''), requested_action),
                            notes = CASE WHEN COALESCE(notes,'') = '' THEN NULLIF(%s,'') ELSE notes || E'
' || NULLIF(%s,'') END,
                            round_added = COALESCE(round_added, %s)
                        WHERE id = %s::uuid
                    """, (row[2], row[3], row[4], row[4], round_added, existing_id))
                    cur.execute("UPDATE dispute_analysis_accounts SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (existing_id, item_id))
                    imported_counts['duplicates'] += 1
                    continue
                cur.execute("""
                    INSERT INTO client_account_disputes
                      (client_id, bureau, creditor_name, account_number_last4, dispute_reason, requested_action, notes, is_active, status, round_added, last_included_round)
                    VALUES
                      (%s::uuid, %s, NULLIF(%s,''), NULLIF(%s,''), COALESCE(NULLIF(%s,''), 'other'), COALESCE(NULLIF(%s,''), 'investigate'), NULLIF(%s,''), TRUE, 'open', %s, %s)
                    RETURNING id::text
                """, (client_id, bureau, row[0], row[1], row[2], row[4], round_added, round_added))
                new_id = cur.fetchone()[0]
                cur.execute("UPDATE dispute_analysis_accounts SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (new_id, item_id))
                imported_counts['accounts'] += 1

            for item_id in personal_ids:
                cur.execute("""
                    SELECT COALESCE(info_type,''), COALESCE(reported_value,''), COALESCE(correct_value,''), COALESCE(suggested_action,'correct'),
                           COALESCE(strategy_note,''), COALESCE(imported_dispute_id::text,''), COALESCE(matched_dispute_id::text,'')
                    FROM dispute_analysis_personal_info WHERE id = %s::uuid AND run_id = %s::uuid
                """, (item_id, analysis_run_id))
                row = cur.fetchone()
                if not row:
                    continue
                if row[5]:
                    imported_counts['duplicates'] += 1
                    continue
                existing_id = row[6]
                if existing_id:
                    cur.execute("""
                        UPDATE client_personal_info_disputes
                        SET is_active = TRUE,
                            removed_date = NULL,
                            removed_round = NULL,
                            requested_action = COALESCE(NULLIF(%s,''), requested_action),
                            notes = CASE WHEN COALESCE(notes,'') = '' THEN NULLIF(%s,'') ELSE notes || E'
' || NULLIF(%s,'') END,
                            round_added = COALESCE(round_added, %s)
                        WHERE id = %s::uuid
                    """, (row[3], row[4], row[4], round_added, existing_id))
                    cur.execute("UPDATE dispute_analysis_personal_info SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (existing_id, item_id))
                    imported_counts['duplicates'] += 1
                    continue
                cur.execute("""
                    INSERT INTO client_personal_info_disputes
                      (client_id, bureau, info_type, reported_value, correct_value, requested_action, notes, is_active, round_added, last_included_round)
                    VALUES
                      (%s::uuid, %s, NULLIF(%s,''), NULLIF(%s,''), NULLIF(%s,''), COALESCE(NULLIF(%s,''), 'correct'), NULLIF(%s,''), TRUE, %s, %s)
                    RETURNING id::text
                """, (client_id, bureau, row[0], row[1], row[2], row[3], row[4], round_added, round_added))
                new_id = cur.fetchone()[0]
                cur.execute("UPDATE dispute_analysis_personal_info SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (new_id, item_id))
                imported_counts['personal_info'] += 1

            for item_id in inquiry_ids:
                cur.execute("""
                    SELECT COALESCE(furnisher_name,''), inquiry_date, COALESCE(suggested_reason,'not my inquiry'), COALESCE(suggested_action,'delete'),
                           COALESCE(strategy_note,''), COALESCE(imported_dispute_id::text,''), COALESCE(matched_dispute_id::text,'')
                    FROM dispute_analysis_inquiries WHERE id = %s::uuid AND run_id = %s::uuid
                """, (item_id, analysis_run_id))
                row = cur.fetchone()
                if not row:
                    continue
                if row[5]:
                    imported_counts['duplicates'] += 1
                    continue
                existing_id = row[6]
                if existing_id:
                    cur.execute("""
                        UPDATE client_inquiry_disputes
                        SET is_active = TRUE,
                            removed_date = NULL,
                            removed_round = NULL,
                            dispute_reason = COALESCE(NULLIF(%s,''), dispute_reason),
                            requested_action = COALESCE(NULLIF(%s,''), requested_action),
                            notes = CASE WHEN COALESCE(notes,'') = '' THEN NULLIF(%s,'') ELSE notes || E'
' || NULLIF(%s,'') END,
                            round_added = COALESCE(round_added, %s)
                        WHERE id = %s::uuid
                    """, (row[2], row[3], row[4], row[4], round_added, existing_id))
                    cur.execute("UPDATE dispute_analysis_inquiries SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (existing_id, item_id))
                    imported_counts['duplicates'] += 1
                    continue
                cur.execute("""
                    INSERT INTO client_inquiry_disputes
                      (client_id, bureau, furnisher_name, inquiry_date, dispute_reason, requested_action, notes, is_active, round_added, last_included_round)
                    VALUES
                      (%s::uuid, %s, NULLIF(%s,''), %s, COALESCE(NULLIF(%s,''), 'not my inquiry'), COALESCE(NULLIF(%s,''), 'delete'), NULLIF(%s,''), TRUE, %s, %s)
                    RETURNING id::text
                """, (client_id, bureau, row[0], row[1], row[2], row[4], round_added, round_added))
                new_id = cur.fetchone()[0]
                cur.execute("UPDATE dispute_analysis_inquiries SET imported_dispute_id = %s::uuid, comparison_status = 'imported' WHERE id = %s::uuid", (new_id, item_id))
                imported_counts['inquiries'] += 1

            summary = []
            if imported_counts['accounts']:
                summary.append(f"{imported_counts['accounts']} account item(s)")
            if imported_counts['personal_info']:
                summary.append(f"{imported_counts['personal_info']} personal-info item(s)")
            if imported_counts['inquiries']:
                summary.append(f"{imported_counts['inquiries']} inquiry item(s)")
            if imported_counts['duplicates']:
                summary.append(f"{imported_counts['duplicates']} existing item(s) re-linked or reactivated")
            message = 'Imported staged findings: ' + ', '.join(summary)
        conn.close()
        return render_client_workspace(request, client_id, message=message, active_tab=active_tab)
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab=active_tab)


@app.post("/ui/generate-dispute-letter", response_class=HTMLResponse)
async def ui_generate_dispute_letter(request: Request):
    form = await request.form()
    client_id = str(form.get('client_id', '')).strip()
    bureau = str(form.get('bureau', '')).strip().lower()
    active_tab = str(form.get('active_tab', 'disputes'))
    include_personal_info = bool(form.get('include_personal_info'))
    include_inquiries = bool(form.get('include_inquiries'))
    include_signature = bool(form.get('include_signature'))
    letter_instructions = str(form.get('letter_instructions', '')).strip()
    selected_document_ids = [str(v).strip() for v in form.getlist('selected_document_ids') if str(v).strip()]
    try:
        if bureau not in BUREAUS:
            raise ValueError('Choose a bureau before generating the dispute letter.')
        if not SECRET_KEY:
            raise RuntimeError('SECRET_KEY missing. Check .env')

        conn = get_conn(); conn.autocommit = True
        with conn.cursor() as cur:
            round_number = get_current_round_number_for_bureau(cur, client_id, bureau)
            account_items = [i for i in fetch_account_disputes(cur, client_id, 500) if i.get('is_active') and (i.get('bureau') or '').lower() == bureau]
            personal_items = [i for i in fetch_personal_info_disputes(cur, client_id, 500) if i.get('is_active') and (i.get('bureau') or '').lower() == bureau]
            inquiry_items = [i for i in fetch_inquiry_disputes(cur, client_id, 500) if i.get('is_active') and (i.get('bureau') or '').lower() == bureau]
            if not include_personal_info:
                personal_items = []
            if not include_inquiries:
                inquiry_items = []
            if not account_items and not personal_items and not inquiry_items:
                raise ValueError(f'No active disputed items are loaded for {bureau.title()}. Add or import items first.')

            cur.execute("SELECT process_dispute_round_run_json(%s,%s,%s,%s,%s,%s)::text", (client_id, round_number, bool(personal_items), SECRET_KEY, SENDER_NAME, ''))
            round_json = json.loads(cur.fetchone()[0])
            run_id = round_json.get('round_run_id')
            if not run_id:
                raise RuntimeError('No round_run_id returned')

            primary_doc_id = selected_document_ids[0] if selected_document_ids else ''
            primary_experian_document_id = primary_doc_id if bureau == 'experian' else ''
            primary_transunion_document_id = primary_doc_id if bureau == 'transunion' else ''
            primary_equifax_document_id = primary_doc_id if bureau == 'equifax' else ''
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
            """, (run_id, client_id, round_number, bool(personal_items), bool(inquiry_items), include_signature, letter_instructions,
                  primary_experian_document_id, primary_transunion_document_id, primary_equifax_document_id))

            cur.execute("SELECT id::text, bureau::text FROM letters WHERE round_run_id = %s ORDER BY bureau::text", (run_id,))
            letter_rows = cur.fetchall()
            target_letter_id = ''
            for lid, letter_bureau in letter_rows:
                if (letter_bureau or '').strip().lower() == bureau:
                    target_letter_id = lid
                else:
                    cur.execute('DELETE FROM letters WHERE id = %s', (lid,))
            if not target_letter_id:
                raise RuntimeError(f'No {bureau.title()} letter record was created for this round.')

            cur.execute("""
                UPDATE client_account_disputes
                SET last_included_round = %s
                WHERE client_id = %s AND COALESCE(is_active, TRUE) = TRUE AND lower(COALESCE(bureau, '')) = %s
            """, (round_number, client_id, bureau))
            if personal_items:
                cur.execute("""
                    UPDATE client_personal_info_disputes
                    SET last_included_round = %s
                    WHERE client_id = %s AND COALESCE(is_active, TRUE) = TRUE AND lower(COALESCE(bureau, '')) = %s
                """, (round_number, client_id, bureau))
            if inquiry_items:
                cur.execute("""
                    UPDATE client_inquiry_disputes
                    SET last_included_round = %s
                    WHERE client_id = %s AND COALESCE(is_active, TRUE) = TRUE AND lower(COALESCE(bureau, '')) = %s
                """, (round_number, client_id, bureau))

            smart_letter_text = build_smart_letter_text(cur, client_id, bureau, round_number, bool(personal_items), bool(inquiry_items), letter_instructions=letter_instructions, round_run_id=run_id)
            final_subject = f"{bureau.title()} Credit Report Dispute"
            cur.execute("UPDATE letters SET bureau = %s, subject = %s, letter_text = %s, use_client_signature = %s WHERE id = %s", (bureau, final_subject, smart_letter_text, include_signature, target_letter_id))
            generate_and_attach_pdf(cur, target_letter_id)

            client_profile = fetch_client_profile(cur, client_id, include_sensitive=False)
            client_name = ' '.join([p for p in [client_profile.get('first_name'), client_profile.get('middle_name'), client_profile.get('last_name')] if p]).strip() or 'Client'
            client_email = (client_profile.get('primary_email') or '').strip()
            summary_note = build_bureau_dispute_round_summary(cur, client_id, bureau, round_number, bool(personal_items), bool(inquiry_items))
            if letter_instructions:
                summary_note += f" Letter instructions: {letter_instructions}"
            cur.execute("INSERT INTO client_notes (client_id, note_text, note_type, created_by) VALUES (%s, %s, 'dispute_round_summary', 'system')", (client_id, summary_note))
            cur.execute("""
                INSERT INTO client_emails
                  (client_id, provider, direction, subject, body_text, from_email, to_email, email_date, related_round, email_type, status, source, created_by)
                VALUES
                  (%s, 'system', 'outbound', %s, %s, %s, NULLIF(%s,''), NOW(), %s, 'dispute_update', 'logged', 'system', 'system')
            """, (client_id, f"{bureau.title()} dispute update - round {round_number}", summary_note, SENDER_NAME, client_email, round_number))
            due_date = date.today() + timedelta(days=30)
            ensure_bureau_redispute_tickler(cur, client_id, bureau, round_number + 1, client_name, due_date)
        conn.close()
        message = f"{bureau.title()} dispute letter generated for round {round_number}. PDF saved and next redispute tickler added for {format_short_date(due_date)}."
        return render_client_workspace(request, client_id, message=message, active_tab=active_tab, open_letter_url=f"/ui/letters/{target_letter_id}/open")
    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab='disputes')


@app.post("/ui/process-round-with-pdfs", response_class=HTMLResponse)
def ui_process_round_with_pdfs(
    request: Request,
    client_id: str = Form(...),
    round_number: int = Form(...),
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
                (client_id, round_number, include_personal_info, SECRET_KEY, SENDER_NAME, '')
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

            client_profile = fetch_client_profile(cur, client_id, include_sensitive=False)
            client_email = (client_profile.get('primary_email') or '').strip()
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
                smart_letter_text = build_smart_letter_text(
                    cur,
                    client_id,
                    bureau,
                    round_number,
                    include_personal_info,
                    include_inquiries,
                    letter_instructions=letter_instructions,
                    round_run_id=run_id,
                )
                final_subject = f"{bureau.title()} Credit Report Dispute"
                cur.execute(
                    "UPDATE letters SET subject = %s, letter_text = %s, use_client_signature = %s WHERE id = %s",
                    (final_subject, smart_letter_text, include_signature, lid)
                )
                pdf_results.append(generate_and_attach_pdf(cur, lid))

            cur.execute("""
                INSERT INTO client_emails
                  (client_id, provider, direction, subject, body_text, from_email, to_email, email_date, related_round, email_type, status, source, created_by)
                VALUES
                  (%s, 'system', 'outbound', %s, %s, %s, NULLIF(%s,''), NOW(), %s, 'dispute_update', 'logged', 'system', 'system')
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
                "warning": ""
            }
        )

    except Exception as e:
        return render_client_workspace(request, client_id, error=str(e), active_tab='disputes')


def fetch_invoice_items(cur, include_inactive: bool = False):
    sql = """
        SELECT id::text, item_name, COALESCE(default_description, ''), COALESCE(default_amount, 0)::text,
               COALESCE(invoice_type, 'other'), is_active
        FROM invoice_service_items
        {where_clause}
        ORDER BY item_name
    """.format(where_clause='' if include_inactive else 'WHERE is_active = TRUE')
    cur.execute(sql)
    return [
        {
            'id': r[0],
            'item_name': r[1],
            'default_description': r[2],
            'default_amount': r[3],
            'invoice_type': r[4],
            'is_active': bool(r[5]),
        }
        for r in cur.fetchall()
    ]


def _find_invoice_item(cur, preferred_name: str, fallback_amount: Decimal | None = None, fallback_type: str = 'other'):
    cur.execute("""
        SELECT id::text, item_name, COALESCE(default_description, ''), COALESCE(default_amount, 0)::text, COALESCE(invoice_type, 'other')
        FROM invoice_service_items
        WHERE lower(item_name) = lower(%s)
        LIMIT 1
    """, (preferred_name,))
    row = cur.fetchone()
    if row:
        return {'id': row[0], 'item_name': row[1], 'default_description': row[2], 'default_amount': row[3], 'invoice_type': row[4]}
    return {'id': '', 'item_name': preferred_name, 'default_description': preferred_name, 'default_amount': str(fallback_amount or Decimal('0.00')), 'invoice_type': fallback_type}


def fetch_credit_product(cur, client_id: str, item_id: str):
    cur.execute("""
        SELECT id::text, lender_name, credit_type, credit_limit, due_date, cutoff_date,
               secured_deposit_amount, origination_date, COALESCE(notes, '')
        FROM client_credit_products
        WHERE id = %s::uuid AND client_id = %s::uuid
        LIMIT 1
    """, (item_id, client_id))
    r = cur.fetchone()
    if not r:
        return None
    return {
        'id': r[0], 'lender_name': r[1] or '', 'credit_type': r[2] or '',
        'credit_limit': '' if r[3] is None else str(r[3]), 'due_date': '' if r[4] is None else str(r[4]),
        'statement_date': '' if r[5] is None else str(r[5]), 'secured_deposit_amount': '' if r[6] is None else str(r[6]),
        'origination_date': '' if r[7] is None else str(r[7]), 'notes': r[8] or ''
    }


