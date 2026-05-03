# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python/FastAPI CRM application. The main web app lives in `app/main.py`, with theme logic in `app/theme_system.py` and `app/theme_agent.py`. HTML views are in `app/templates/`, branding assets are in `app/static/brand/`, and bureau letter templates are in `app/custom_letter_templates/`.

Top-level scripts generate or process dispute PDFs: `generate_letter_pdf.py`, `generate_pdfs_for_ids.py`, `process_round_and_generate_pdfs.py`, and `process_round_run_and_generate_pdfs.py`. Runtime files are stored under `uploads/`, `letters/`, and `backups/`; avoid committing new client documents unless explicitly required.

## Build, Test, and Development Commands

- `cd app && python -m uvicorn main:app --reload`: run the FastAPI app locally at `http://127.0.0.1:8000`.
- `Start_CreditSapientia.bat`: Windows shortcut that starts the server and opens the browser.
- `python generate_letter_pdf.py`: run the standalone PDF generator script from the repository root.
- `python process_round_and_generate_pdfs.py`: process a dispute round and generate related PDFs.

No package manifest is currently present. When setting up a new environment, install dependencies implied by imports, including `fastapi`, `uvicorn`, `psycopg2`, `python-dotenv`, `reportlab`, `cryptography`, `pydantic`, `python-pptx`, and either `pypdf` or `PyPDF2`.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, `snake_case` for functions and variables, and `PascalCase` for classes such as `ThemeAgent`. Name route handlers and helpers by behavior, for example `create_client`, `generate_invoice_pdf`, or `load_theme_settings`. Prefer `pathlib.Path` for filesystem work where practical, and name templates for their view, such as `client_edit.html`.

## Testing Guidelines

There is no committed test suite yet. For new tests, add a `tests/` directory and use `pytest` with files named `test_*.py`. Focus coverage on PDF generation, database-backed route behavior, upload handling, and theme settings. Run tests with `python -m pytest` once the suite exists, and include manual verification notes for UI-heavy changes.

## Commit & Pull Request Guidelines

Git history currently uses short, informal messages such as `Initial commit` and `latest updated main`. Prefer clearer imperative commits going forward, for example `Add client merge validation` or `Fix invoice PDF totals`.

Pull requests should include a short summary, affected screens or scripts, database/configuration impacts, and verification steps. Include screenshots for template or branding changes, and note any generated files intentionally added under `letters/` or `uploads/`.

## Security & Configuration Tips

Keep secrets in `app/.env` and document required keys in `app/.env.example`. Do not commit real client credit reports, signatures, invoices, database credentials, encryption keys, or uploaded identity documents. Review changes under `uploads/` and `letters/` carefully before committing.
