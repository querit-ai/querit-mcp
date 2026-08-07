# Contributing to Querit MCP

Thank you for contributing. Keep changes focused, test observable behavior, and never include production credentials or user data in issues, fixtures, logs, or commits.

## Development setup

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/), then run:

```bash
git clone <repository-url>
cd querit-mcp
uv sync --all-groups
```

The virtual environment is created at `.venv`. Editors should use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows.

## Making a change

1. Create a branch from the default branch.
2. Add or update tests for behavior changes.
3. Keep public tool schemas backward compatible unless the change is intentionally breaking.
4. Update `README.md`, deployment notes, and `CHANGELOG.md` when applicable.
5. Run the full local quality gate before opening a pull request.

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright
uv run pytest
uv build
```

Deprecation warnings fail the test suite. Replace deprecated APIs rather than suppressing warnings. A narrowly scoped suppression is acceptable only when an upstream library has no supported replacement and the reason is documented next to it.

## Pull requests

Pull requests should explain the user-visible behavior, security or quota impact, tests performed, and any deployment changes. Keep unrelated refactors in separate pull requests.

Do not use public issues or pull requests to report vulnerabilities. Follow [SECURITY.md](SECURITY.md).
