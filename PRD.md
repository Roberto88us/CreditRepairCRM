# Product Requirements Document

## Product Summary

CreditSapientia CRM is a local-first FastAPI application for credit repair operations, client intake, dispute workflows, document management, accounting, referrals, presentations, and branded workspace management. The product currently behaves like an all-in-one operator console for a credit repair business, with most business logic implemented in `app/main.py`, UI in Jinja templates under `app/templates/`, static brand assets under `app/static/brand/`, generated letters under `letters/`, and client documents/invoices/presentations under `uploads/`.

## Current Feature Inventory

- Authentication and setup: first-user setup, login/logout, password reset, role labels, license activation, and theme cookies.
- Workspace management: corporate and personal profiles, current workspace cookies, profile logos, lobby/dashboard navigation, and module shells.
- Client CRM: client creation, profile editing, lifecycle status, notes, appointments, follow-ups, redispute reminders, address history, related people, referrals, and header summaries.
- Credit repair workflow: bureau disputes, inquiry disputes, personal-info disputes, removed-history handling, round overrides, dispute round generation, letter editing, custom bureau templates, and PDF output.
- Document intake: upload request links, public token upload page, document metadata, document text extraction, staged analysis, import into disputes, and document opening.
- Accounting: billing programs, startup/monthly/manual invoices, invoice items, recurring schedules, payment methods, payments, invoice PDFs, and accounting settings.
- Presentations and marketing: PowerPoint upload/indexing, generated draft decks, PDF conversion when Office/LibreOffice is available, presentation viewer, and email draft records.
- Appearance: theme pack library, theme settings, AI-style visual theme generation, and brand asset switching.
- Backup: local backup ZIP creation, database dump via `pg_dump` when available, and backup file management.

## Users and Jobs To Be Done

- Business owner/admin: configure company identity, users, license, branding, billing defaults, backups, and business profiles.
- Credit repair specialist: manage client onboarding, documents, dispute items, letters, bureau rounds, follow-ups, and results.
- Accounting operator: create invoices, track recurring charges, record payments, and review customer balances.
- Client: use a tokenized upload link to submit requested documents without accessing the full CRM.

## Core Requirements

1. Securely protect all non-public CRM routes and all client files.
2. Support end-to-end client lifecycle from prospect to active client to cancelled/completed.
3. Keep dispute data structured by bureau, item type, round, status, and source document.
4. Generate editable dispute letters and attach reproducible PDFs.
5. Track invoices, payment methods, payment history, and recurring billing schedules.
6. Preserve a clear audit trail for client-sensitive actions.
7. Keep sensitive client data encrypted at rest where practical and never expose it through unauthenticated routes.
8. Provide maintainable development setup, tests, migrations, and deployment documentation.

## Major Bottlenecks

- Monolithic application file: `app/main.py` is over 18,000 lines and mixes routing, persistence, migrations, rendering, PDF generation, security, accounting, and credit analysis. This slows review, raises regression risk, and makes testing difficult.
- Oversized template: `app/templates/client.html` is over 8,500 lines and contains many unrelated tabs, forms, styles, and scripts. Any UI change has high conflict and breakage risk.
- No dependency manifest: there is no `requirements.txt` or `pyproject.toml`, so new setup depends on manually inferring imports.
- No automated tests: there is no committed test suite, despite high-risk workflows around credentials, documents, invoices, and dispute letters.
- Inline schema evolution: many `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN IF NOT EXISTS` calls run inside app code. This hides database versioning, makes rollbacks hard, and can fail only at runtime.
- Repeated PDF/script logic: top-level scripts duplicate PDF rendering and database logic that also exists in `app/main.py`.
- Large binary assets and generated data in the repository/worktree: brand images, PDFs, uploads, invoices, signatures, and `__pycache__` files increase repository size and risk leaking client data.
- Synchronous heavy work: PDF generation, PPTX conversion, backups, document analysis, and large context loads run in request handlers and can block the web process.

## Possible Issues Found

- Auth/session weakness: authentication uses cookies like `crm_auth=1` plus `crm_user_id`; there is no signed session token or server-side session. Several route groups do explicit `is_authenticated` checks, but many client and API routes do not show direct guards.
- Missing CSRF protection: many destructive POST forms delete records, files, invoices, users, documents, templates, and presentations without CSRF tokens.
- Public file access risk: `/ui/documents/{document_id}/open` returns stored client files by document ID and does not check authentication in the route body.
- Public API risk: `/clients/{client_id}/dashboard`, `/process-round`, `/letters/{letter_id}/pdf`, and related API endpoints do not appear protected.
- Upload controls are limited: token upload supports expiration, but file size/content scanning and strict MIME validation are not centralized.
- Error handling leaks internals: many handlers return `str(e)` to the UI, which can expose database or filesystem details.
- Sensitive data exposure in repo: existing `uploads/`, `letters/`, invoices, signatures, and `app/.env` are present locally; these should not be committed or distributed.
- Password reset flow appears incomplete for real email delivery: reset tokens are prepared, but there is no SMTP/API sender implementation.
- Cookie flags are incomplete for production: cookies use `httponly` in some cases and `samesite=lax`, but no `secure=True`; display name/current-context cookies are readable by client scripts.
- Backups and local path opening are Windows/local-machine oriented and should be permission-gated consistently.
- Query and route ownership is inconsistent: some delete/edit paths validate client ownership strongly, while others rely on provided IDs or global IDs.

## Improvement Plan

### Phase 1: Security and Repository Hygiene

- Add `.gitignore` for `.env`, `__pycache__/`, generated PDFs, uploads, backups, local databases, and temporary Office conversion files.
- Remove tracked/generated sensitive artifacts from version control after confirming with the owner.
- Add `requirements.txt` or `pyproject.toml` with pinned dependencies and a setup section in `README.md`.
- Replace bare auth cookies with signed server-side sessions or signed JWT/session cookies.
- Add a global auth dependency/middleware for all `/ui/*` routes except login, setup, password reset, license, and `/upload/{token}`.
- Protect API routes with authentication or explicit API keys.
- Add CSRF protection to all state-changing form posts.
- Add authorization checks to file routes, especially documents, invoices, signatures, letter PDFs, and presentation files.
- Stop returning raw exception strings to end users; log details server-side and show safe messages.

### Phase 2: Test and Migration Foundation

- Add `pytest`, `httpx`, and database test fixtures.
- Create smoke tests for app import, health route, login requirements, document access, invoice creation, dispute letter generation, and upload token expiration.
- Move schema changes into migrations using Alembic or versioned SQL files.
- Add seed scripts for default billing programs, invoice items, themes, and bureau templates.
- Add CI checks for `python -m py_compile`, unit tests, and basic linting.

### Phase 3: Modular Refactor

- Split `app/main.py` into packages:
  - `app/core/`: config, database, auth, crypto, errors.
  - `app/routes/`: auth, clients, documents, disputes, accounting, profiles, presentations, settings.
  - `app/services/`: PDF, letters, document analysis, billing, backup, presentation generation.
  - `app/repositories/`: database access by domain.
  - `app/schemas/`: Pydantic request/response models.
- Split `client.html` into tab partials such as `client_overview.html`, `client_billing.html`, `client_documents.html`, `client_disputes.html`, and `client_calendar.html`.
- Move inline JavaScript and CSS into static files with route-specific bundles.
- Replace duplicated PDF functions in scripts with one shared service.

### Phase 4: Performance and Reliability

- Add pagination and targeted lazy loading for client workspace sections instead of loading every tab dataset on every client page request.
- Move PDF generation, PPTX conversion, backups, and document analysis to background jobs.
- Add database indexes for common filters: client IDs, business profile IDs, invoice status/due dates, document client IDs, dispute status/bureau, upload tokens, and recurring invoice schedules.
- Add file-size limits, MIME validation, antivirus/scanning hooks, and storage abstraction for uploads.
- Add structured logging with request IDs and an audit table for sensitive actions.

### Phase 5: Product Enhancements

- Add real outbound email integration for password resets, client reminders, invoice notices, and dispute updates.
- Add dashboard widgets for overdue invoices, expiring upload requests, pending document review, upcoming appointments, and redispute deadlines.
- Add role-based permission enforcement at route and UI levels.
- Add import/export tools for clients, disputes, invoices, and presentation libraries.
- Add compliance reporting for dispute round history, sent letters, client consent, and uploaded identity documents.

## Acceptance Criteria

- Every protected route rejects unauthenticated users and every file route verifies ownership/permission.
- New environments can be installed from one documented command set.
- Tests cover at least the critical happy paths and security denial paths.
- Database schema changes are versioned and repeatable.
- Client workspace loads only the data needed for the active section.
- Generated artifacts are excluded from version control by default.
- PDF generation, document analysis, and billing calculations live in shared services with tests.

## Validation Performed

- Repository structure, route decorators, templates, helper scripts, environment template, and schema-creation code were reviewed.
- `python -m py_compile` completed successfully for `app/main.py`, `app/theme_system.py`, `app/theme_agent.py`, and the top-level PDF/round scripts.
- No automated test suite, dependency manifest, `.gitignore`, Dockerfile, SQL migration files, or README were found in the repository root during this review.
