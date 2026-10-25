# Contributing

Thanks for considering a contribution. This project aims to stay small enough to read
in an afternoon and rigorous enough to deploy.

---

## Quick start for contributors

```bash
git clone https://github.com/your-org/enterprise-api-backend.git
cd enterprise-api-backend

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,postgres]"

cp .env.example .env
pytest -q
ruff check .
ruff format --check .
```

The test suite uses file-based SQLite databases and needs no external services. If you
touch migrations, also run them against PostgreSQL (see [Migrations](#migrations)).

---

## Before you open a pull request

- [ ] `ruff check .` passes.
- [ ] `ruff format --check .` passes (or run `ruff format .`).
- [ ] `pytest -q` passes.
- [ ] New behaviour has tests.
- [ ] Public API changes update `docs/API.md` and `docs/openapi.json`
      (`make openapi`).
- [ ] The change is described in the pull request with the *why*, not only the *what*.

---

## Development workflow

```bash
make install     # create .venv and install dev + postgres extras
make run         # uvicorn on 127.0.0.1:8000 with autoreload
make test        # pytest
make lint        # ruff check
make format      # ruff format + ruff check --fix
make migrate     # alembic upgrade head
make seed        # idempotent demo data
make openapi     # regenerate docs/openapi.json
make check       # lint + test
```

### A typical loop

1. Branch from `main`: `git switch -c feat/task-estimates`.
2. Write a failing test in `tests/`.
3. Implement the smallest change that makes it pass.
4. Refactor, then run `make check`.
5. Commit and open a pull request.

---

## Project conventions

### Layering

Keep the boundaries described in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):

- **Routers** declare HTTP surface and call one service function. No business logic,
  no `db.commit()`.
- **Services** own rules, authorization, and transactions. They raise typed
  `AppError` subclasses and return domain objects or Pydantic models.
- **Models** are persistence only. No HTTP awareness.
- **Schemas** validate input and shape output. No database access.

If a change makes you want to import `Request` into a service, the boundary is wrong.

### Errors

Raise the specific exception, not a bare `HTTPException`:

```python
raise NotFoundError("Task not found", code="task_not_found")
```

Codes are stable, lower_snake_case, and documented in the
[error catalog](docs/API.md#error-catalog). Adding a new code means updating that
table.

### Async

- Use `async def` for anything that touches I/O.
- Never call blocking libraries in the request path without `run_in_threadpool`
  (see `app/services/pdf.py` for the pattern).
- Do not use `time.sleep`; use `asyncio.sleep`.

### Typing and style

- Full type hints on public functions.
- `from __future__ import annotations` at the top of modules.
- Line length 100, enforced by ruff.
- Prefer explicit imports over wildcards.
- Keep comments for *why*, not *what*.

### Tests

- One behaviour per test, named for the behaviour:
  `test_viewer_cannot_update_project`.
- Use the shared fixtures in `tests/conftest.py`; do not hand-roll an app per test.
- Assert on status codes **and** error codes, not just "not 200".
- Cover the negative path: who is forbidden, what is rejected, what is not leaked.

---

## Migrations

When you change a model:

```bash
alembic revision --autogenerate -m "add task estimate"
```

Then:

1. **Read the generated file.** Autogenerate misses data migrations and sometimes
   misreads defaults.
2. Make `downgrade()` genuinely reversible.
3. Apply it: `alembic upgrade head`.
4. Verify reversal: `alembic downgrade -1 && alembic upgrade head`.
5. If you have PostgreSQL available, run the same steps against it.

CI runs upgrade → downgrade base → upgrade head on PostgreSQL, so an irreversible
migration will fail the build.

Use portable constructs (`sa.text("CURRENT_TIMESTAMP")`, `sa.text("true")`) and
`native_enum=False` for enums, so SQLite tests keep working.

---

## Regenerating the OpenAPI document

`docs/openapi.json` is committed so reviewers and client generators do not need to run
the server.

```bash
make openapi
git diff docs/openapi.json
```

Include the regenerated file in the same pull request as the schema change, and update
the endpoint tables in `docs/API.md` if paths, parameters, or responses changed.

---

## Commit messages

Conventional Commits are appreciated but not enforced:

```
feat: add task estimate field
fix: reject refresh tokens with mismatched version
docs: clarify PDF truncation behaviour
test: cover bulk status update with foreign task ids
chore: bump fastapi
```

The subject line should complete "This change will ...". Keep the body focused on
motivation and trade-offs.

---

## Reporting bugs

Include:

- What you did (minimal reproduction: request, payload, headers).
- What you expected and what happened.
- The `request_id` from the error envelope, if there is one.
- Python version, database, and whether you run Docker.
- The relevant log lines (redact tokens and secrets).

Open an issue with the `bug` label. For security issues, follow
[`SECURITY.md`](SECURITY.md) instead — do not open a public issue.

---

## Proposing features

Open an issue describing the problem before writing a large patch. This project
favours small, composable changes and will push back on features that:

- break the layered boundaries,
- add a dependency for something the standard library does well,
- weaken the fail-closed defaults,
- expand the public API without a clear consumer.

---

## Code of conduct

Be direct, be kind, assume good faith. Harassment, discrimination, and personal attacks
are not welcome. Maintainers may remove comments, commits, or contributions that do not
meet this standard.

---

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
