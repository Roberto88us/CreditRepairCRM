from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

import main


def _best_effort_cleanup_client(cur, client_id: str) -> None:
    statements = [
        "DELETE FROM client_documents WHERE client_id = %s::uuid",
        "DELETE FROM client_upload_requests WHERE client_id = %s::uuid",
        "DELETE FROM letters WHERE client_id = %s::uuid",
        "DELETE FROM round_run_dispute_meta WHERE client_id = %s::uuid",
        "DELETE FROM round_runs WHERE client_id = %s::uuid",
        "DELETE FROM client_emails WHERE client_id = %s::uuid",
        "DELETE FROM client_notes WHERE client_id = %s::uuid",
        "DELETE FROM client_account_disputes WHERE client_id = %s::uuid",
        "DELETE FROM client_personal_info_disputes WHERE client_id = %s::uuid",
        "DELETE FROM client_inquiry_disputes WHERE client_id = %s::uuid",
        "DELETE FROM client_addresses WHERE client_id = %s::uuid",
        "DELETE FROM clients WHERE id = %s::uuid",
    ]
    for statement in statements:
        try:
            cur.execute(statement, (client_id,))
        except Exception:
            try:
                cur.connection.rollback()
            except Exception:
                pass


def _create_client(cur, *, first_name: str, last_name: str, email: str) -> str:
    cur.execute(
        """
        INSERT INTO clients (first_name, last_name, primary_email, email)
        VALUES (%s, %s, %s, %s)
        RETURNING id::text
        """,
        (first_name, last_name, email, email),
    )
    return cur.fetchone()[0]


@pytest.fixture(scope="session")
def db_conn():
    try:
        conn = main.get_conn()
    except Exception as exc:
        pytest.skip(f"DB-backed integration tests skipped: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def app_template_paths(monkeypatch):
    monkeypatch.setattr(main, "templates", Jinja2Templates(directory=str(main.PROJECT_ROOT / "app" / "templates")))


@pytest.fixture(scope="session")
def auth_cookie_user_id(db_conn) -> str:
    with db_conn.cursor() as cur:
        main.ensure_app_users_table(cur)
        cur.execute(
            "SELECT id FROM app_users WHERE COALESCE(is_active, TRUE) = TRUE ORDER BY updated_at DESC NULLS LAST LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0])
        user_id = uuid4().hex
        cur.execute(
            """
            INSERT INTO app_users (id, display_name, username, email, role, password_hash, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'administrator', %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (user_id, "Integration Admin", f"integration_{user_id[:8]}", f"integration_{user_id[:8]}@example.com", "integration-test-hash"),
        )
        return user_id


@pytest.fixture()
def authed_client(auth_cookie_user_id: str):
    with TestClient(main.app) as client:
        client.cookies.set("crm_auth", "1")
        client.cookies.set("crm_user_id", auth_cookie_user_id)
        client.cookies.set("crm_user_role", "administrator")
        client.cookies.set("crm_user_name", "Integration Admin")
        yield client


@pytest.mark.integration
def test_ui_client_create_persists_record(authed_client: TestClient, db_conn) -> None:
    email = f"integration_create_{uuid4().hex[:10]}@example.com"
    created_client_id = ""
    try:
        response = authed_client.post(
            "/ui/client/create",
            data={
                "first_name": "Integration",
                "last_name": "Create",
                "primary_email": email,
                "lifecycle_status": "prospect",
            },
        )
        assert response.status_code == 200

        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, first_name, last_name
                FROM clients
                WHERE lower(COALESCE(primary_email, '')) = lower(%s)
                ORDER BY created_at DESC NULLS LAST
                LIMIT 1
                """,
                (email,),
            )
            row = cur.fetchone()
            assert row is not None
            created_client_id = row[0]
            assert row[1] == "Integration"
            assert row[2] == "Create"
    finally:
        if created_client_id:
            with db_conn.cursor() as cur:
                _best_effort_cleanup_client(cur, created_client_id)


@pytest.mark.integration
def test_ui_client_get_loads_workspace(authed_client: TestClient, db_conn) -> None:
    client_id = ""
    try:
        with db_conn.cursor() as cur:
            client_id = _create_client(
                cur,
                first_name="Integration",
                last_name="Viewer",
                email=f"integration_view_{uuid4().hex[:10]}@example.com",
            )
        response = authed_client.get(f"/ui/client/{client_id}")
        assert response.status_code == 200
        assert "Integration" in response.text
        assert "Viewer" in response.text
    finally:
        if client_id:
            with db_conn.cursor() as cur:
                _best_effort_cleanup_client(cur, client_id)


@pytest.mark.integration
def test_upload_token_get_and_post_roundtrip(authed_client: TestClient, db_conn, monkeypatch, tmp_path) -> None:
    client_id = ""
    upload_request_id = uuid4()
    upload_token = uuid4()
    try:
        monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
        with db_conn.cursor() as cur:
            client_id = _create_client(
                cur,
                first_name="Integration",
                last_name="Uploader",
                email=f"integration_upload_{uuid4().hex[:10]}@example.com",
            )
            main.ensure_client_upload_requests_table(cur)
            cur.execute(
                """
                INSERT INTO client_upload_requests (id, client_id, token, request_type, status)
                VALUES (%s::uuid, %s::uuid, %s::uuid, 'general_upload', 'open')
                """,
                (str(upload_request_id), client_id, str(upload_token)),
            )

        get_response = authed_client.get(f"/upload/{upload_token}")
        assert get_response.status_code == 200

        post_response = authed_client.post(
            f"/upload/{upload_token}",
            data={"doc_category": "miscellaneous", "doc_section": "miscellaneous"},
            files={"file": ("integration-proof.txt", b"integration-upload", "text/plain")},
        )
        assert post_response.status_code == 200
        assert "uploaded successfully" in post_response.text.lower()

        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_name, file_path
                FROM client_documents
                WHERE upload_request_id = %s::uuid
                ORDER BY created_at DESC NULLS LAST
                LIMIT 1
                """,
                (str(upload_request_id),),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "integration-proof.txt"
            assert str(tmp_path) in row[1]
    finally:
        if client_id:
            with db_conn.cursor() as cur:
                _best_effort_cleanup_client(cur, client_id)


@pytest.mark.integration
def test_process_round_with_pdfs_persists_meta(authed_client: TestClient, db_conn, monkeypatch) -> None:
    client_id = ""
    round_run_id = str(uuid4())
    letter_id = str(uuid4())
    original_secret = main.SECRET_KEY
    try:
        with db_conn.cursor() as cur:
            client_id = _create_client(
                cur,
                first_name="Integration",
                last_name="Dispute",
                email=f"integration_dispute_{uuid4().hex[:10]}@example.com",
            )
            cur.execute(
                """
                INSERT INTO round_runs (id, client_id, round_number, include_personal_info)
                VALUES (%s::uuid, %s::uuid, %s, %s)
                """,
                (round_run_id, client_id, 1, False),
            )
            cur.execute(
                """
                INSERT INTO letters (id, client_id, bureau, subject, letter_text, round_run_id, use_client_signature)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::uuid, FALSE)
                """,
                (letter_id, client_id, "experian", "Initial Subject", "Initial Body", round_run_id),
            )

        monkeypatch.setattr(main, "SECRET_KEY", original_secret or "integration-secret")

        def fake_round_json_with_fallback(cur, _client_id, requested_round_number, _include_personal_info, _client_email):
            return ({"round_run_id": round_run_id, "requested_round_number": requested_round_number}, requested_round_number)

        def fake_generate_and_attach_pdf(_cur, target_letter_id):
            return {"letter_id": target_letter_id, "file_path": "integration-fake.pdf"}

        monkeypatch.setattr(main, "run_dispute_round_json_with_template_fallback", fake_round_json_with_fallback)
        monkeypatch.setattr(main, "generate_and_attach_pdf", fake_generate_and_attach_pdf)

        response = authed_client.post(
            "/ui/process-round-with-pdfs",
            data={
                "client_id": client_id,
                "round_number": "1",
                "include_personal_info": "",
                "include_inquiries": "",
                "include_signature": "",
                "letter_instructions": "integration test",
            },
        )
        assert response.status_code == 200

        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT round_number, include_accounts, include_personal_info, include_inquiries
                FROM round_run_dispute_meta
                WHERE round_run_id = %s::uuid
                """,
                (round_run_id,),
            )
            meta_row = cur.fetchone()
            assert meta_row is not None
            assert meta_row[0] == 1
            assert meta_row[1] is True

            cur.execute("SELECT subject FROM letters WHERE id = %s::uuid", (letter_id,))
            letter_row = cur.fetchone()
            assert letter_row is not None
            assert letter_row[0] == "Experian Credit Report Dispute"
    finally:
        main.SECRET_KEY = original_secret
        if client_id:
            with db_conn.cursor() as cur:
                _best_effort_cleanup_client(cur, client_id)
