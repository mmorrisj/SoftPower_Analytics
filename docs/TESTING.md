# Testing Guide

This document describes the testing infrastructure for the SoftPower Analytics Dashboard project.

## Table of Contents

- [Overview](#overview)
- [Test Organization](#test-organization)
- [Running Tests](#running-tests)
- [Test Categories (Markers)](#test-categories-markers)
- [Project Fixtures](#project-fixtures)
- [CI/CD Integration](#cicd-integration)
- [Coverage](#coverage)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## Overview

The project uses **pytest** as the primary testing framework with shared fixtures, markers, and coverage reporting.

### Key Features

- **Organized test structure** with fixtures in `conftest.py`
- **Test categorization** using pytest markers (unit, integration, slow, llm, etc.)
- **Database fixtures** with automatic setup/teardown
- **API testing** with FastAPI test client
- **Coverage reporting** with pytest-cov
- **CI/CD integration** with GitHub Actions
- **Parallel test execution** with pytest-xdist

## Test Organization

```
tests/
├── __init__.py
├── conftest.py                    # Shared fixtures and configuration
├── test_health.py                 # Health/smoke tests
├── test_models.py                 # Database model unit tests
├── test_api.py                    # API integration tests
├── test_pipeline.py               # Pipeline processing tests
├── test_atom_pipeline.py          # ATOM CSV ingestion tests
├── test_ingestion_service.py      # Web ingestion service tests
├── test_citation_utils.py         # Citation utility tests
├── test_sota_features.py          # RAG/search feature tests
├── test_llm_jwt_forwarding.py     # Gateway-JWT forwarding to the LLM proxy
├── test_agent_tools.py            # Agent tool tests
├── test_agent_tool_linkage.py     # Agent tool linkage tests
└── test_agent_provider_jwt.py     # Agent provider JWT tests
```

## Running Tests

### Prerequisites

```bash
# Install test dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov pytest-timeout pytest-xdist
```

### Basic Commands

```bash
# Run all tests
pytest

# Run a specific file / class / function
pytest tests/test_models.py
pytest tests/test_models.py::TestDocumentModel
pytest tests/test_models.py::TestDocumentModel::test_create_document_minimal
```

### Run Tests by Category

```bash
pytest -m unit                                # fast, no external dependencies
pytest -m integration                         # needs database/API
pytest -m database                            # database connectivity required
pytest -m api                                 # API tests
pytest -m pipeline                            # pipeline tests
pytest -m "not slow"                          # exclude slow tests
pytest -m "not llm"                           # exclude tests that call real LLM APIs
pytest -m "integration and not slow and not llm"
```

### Run Tests with Coverage

```bash
pytest --cov=shared --cov=services --cov=server        # terminal report
pytest --cov --cov-report=html                         # HTML report (htmlcov/index.html)
pytest --cov --cov-report=xml                          # XML report (for CI/CD)
```

### Parallel / Advanced

```bash
pytest -n auto        # parallel (pytest-xdist)
pytest -x             # stop on first failure
pytest --lf           # re-run only failed tests
pytest --durations=10 # show slowest tests
pytest --timeout=300  # prevent hanging tests
```

## Test Categories (Markers)

| Marker | Meaning |
|---|---|
| `@pytest.mark.unit` | Fast, no external dependencies |
| `@pytest.mark.integration` | Requires external services (database, API, file system) |
| `@pytest.mark.database` | Requires database connectivity |
| `@pytest.mark.api` | Exercises the FastAPI app |
| `@pytest.mark.pipeline` | Pipeline processing tests |
| `@pytest.mark.slow` | Takes longer than ~1 second; excluded in rapid cycles via `-m "not slow"` |
| `@pytest.mark.llm` | Makes real LLM API calls (costs money) — excluded by default in development via `-m "not llm"` |

Marker registration lives in [pytest.ini](../pytest.ini).

## Project Fixtures

Defined in [tests/conftest.py](../tests/conftest.py) (`pytest --fixtures` lists everything):

- **`db_session`** — database session that automatically rolls back after each test.
- **`create_test_document`** — factory fixture for `Document` rows (`create_test_document(doc_id="TEST001", title=..., salience=...)`).
- **`api_client`** — FastAPI `TestClient` for the server app.
- **`mock_s3_client`** — in-memory stand-in for the S3 API client (`mock_s3_client.files[...] = df`).

Mock external services (LLM, S3) rather than calling them; tests that must hit a real LLM get `@pytest.mark.llm`.

## CI/CD Integration

> **Honest status (2026-07):** the workflows exist (`.github/workflows/ci.yml`, `cd.yml`,
> `release-registry.yml`) but CI is **non-blocking** and line coverage is low (~4% per the
> [maintainability assessment](archive/MAINTAINABILITY_ASSESSMENT_2026-06.md)). The coverage targets below
> are aspirational. This section replaces the former `CI_CD_SUMMARY.md`, which overstated
> maturity and was removed.

### GitHub Actions Workflow

The CI pipeline runs automatically on:
- Push to `main` or `develop` branches
- Pull requests to `main`

#### Test Jobs

1. **Unit Tests** - Fast tests, no external dependencies
2. **Integration Tests** - Tests with database (PostgreSQL service)
3. **Coverage Report** - Full test suite with coverage

#### Test Services

The CI uses GitHub Actions services to provide:
- **PostgreSQL with pgvector** - For database tests
- **Redis** - For caching tests (future)

### CI Environment Variables

The following are automatically configured in CI:

```yaml
POSTGRES_USER: testuser
POSTGRES_PASSWORD: testpass
POSTGRES_DB: testdb
POSTGRES_HOST: localhost
POSTGRES_PORT: 5432
DATABASE_URL: postgresql+psycopg2://testuser:testpass@localhost:5432/testdb
TESTING: 1
```

### Coverage Reports in CI

Coverage reports are:
- **Uploaded to Codecov** - For historical tracking
- **Stored as artifacts** - Downloadable from GitHub Actions
- **Commented on PRs** - Automatic PR comments with coverage changes

## Coverage

Coverage is configured in [.coveragerc](../.coveragerc):

- **Source directories**: `shared/`, `services/`, `server/`
- **Omitted**: Tests, migrations, config files, Streamlit pages

### Coverage Targets (aspirational)

Actual coverage is currently in the single digits (see the honest status
above); the CI floor in `ci.yml` is set just below the current baseline to
prevent backsliding, and should be ratcheted up as coverage improves. The
long-term **aspirational** targets are:

- **Unit tests**: >80% coverage
- **Integration tests**: Focus on critical paths
- **Overall**: >70% coverage

## Troubleshooting

### Database Connection Issues

```bash
docker ps      # is PostgreSQL running (Docker)?
pg_isready     # local PostgreSQL

export DATABASE_URL="postgresql://user:pass@localhost:5432/testdb"
pytest
```

### Import Errors

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

### Slow Tests

```bash
pytest --durations=10   # identify them
pytest -m "not slow"    # skip during development
```

### Fixture Not Found

```bash
ls tests/conftest.py    # conftest must be in tests/
pytest --fixtures       # list all available fixtures
```

## Contributing

When adding new features:

1. **Write tests first** (TDD approach recommended)
2. **Use appropriate markers** (`@pytest.mark.unit`, etc.)
3. **Don't reduce coverage** — the CI floor is enforced; the >70% overall figure is an aspirational target, not the current state
4. **Document complex tests** with docstrings
5. **Run tests before committing**: `pytest`
6. **Check coverage**: `pytest --cov`

For general pytest patterns (parameterization, mocking, test isolation, naming), see the
[pytest documentation](https://docs.pytest.org/), [pytest-cov](https://pytest-cov.readthedocs.io/),
[FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), and
[SQLAlchemy session/transaction testing](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites)
— this guide only documents what is specific to this repo.
