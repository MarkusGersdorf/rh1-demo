"""Shared fixtures.

Every test sets SCANNER_BACKEND explicitly. `app.main` builds its backend at
import time, so tests that need a particular backend import the module through
the `scanner_app` fixture rather than at module scope.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "components" / "vuln-demo-app" / ".demo"
REPLAY_DIR = SERVICE_ROOT / "replay"

sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture
def vulnerable_diff() -> str:
    return (DEMO_DIR / "01-introduce-statements-endpoint.diff").read_text()


@pytest.fixture
def fixed_diff() -> str:
    return (DEMO_DIR / "02-fix-ownership-check.diff").read_text()


@pytest.fixture
def vulnerable_source() -> str:
    return (REPO_ROOT / "components" / "vuln-demo-app" / "src" / "main" / "java"
            / "com" / "redhat" / "demo" / "accounts" / "AccountResource.java").read_text()


@pytest.fixture
def fixed_source() -> str:
    return (DEMO_DIR / "AccountResource.fixed.java").read_text()


@pytest.fixture
def replay_entries() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(REPLAY_DIR.glob("*.json"))]


@pytest.fixture
def make_request():
    """Build a ReviewRequest without importing app.main."""
    from app.schema import FileContext, ReviewRequest

    def _make(diff: str, *, files: list[tuple[str, str]] | None = None, **kw):
        defaults = dict(
            repository="http://gitea.svc/demo/accounts-api.git",
            commit="4c1b9e7aa1f2c3d4e5f60718293a4b5c6d7e8f90",
            base_commit="0ad3f1190b2c3d4e5f60718293a4b5c6d7e8f901",
            diff=diff,
            files=[FileContext(path=p, content=c) for p, c in (files or [])],
        )
        defaults.update(kw)
        return ReviewRequest(**defaults)

    return _make


# Real recorded output from the pinned `antares/` submodule, not hand-written
# data: the parser tracks a report format this project does not control, so
# the fixtures have to be genuine. The submodule is pinned to a commit, so an
# upstream push cannot change these from under the tests.
ANTARES_REPORT = (REPO_ROOT / "antares" / "reports" / "cpu-sweep" / "report.json")


@pytest.fixture
def antares_report_dir(tmp_path):
    """A copy of the submodule's recorded CPU sweep."""
    import shutil

    if not ANTARES_REPORT.is_file():
        pytest.fail(
            "The `antares` submodule is not checked out, so there is no real "
            "Antares output to test the parser against.\n\n"
            "    git submodule update --init --recursive\n\n"
            f"(expected {ANTARES_REPORT})",
            pytrace=False,
        )

    src = ANTARES_REPORT.parent
    dest = tmp_path / "antares-report"
    shutil.copytree(src, dest)
    # The antares-localize task writes this sidecar so the attestation can pin
    # Stage A. Include it, or the digest path goes untested.
    (dest / "model.sha256").write_text(
        "7c1e4b0ad93f2516c8a4d7e0b51f3296a8c4d0e2f1b7a6943c5d8e0f1a2b3c4d\n")
    return dest


@pytest.fixture
def localizer_result(antares_report_dir):
    from app.localizer import parse_report_dir

    return parse_report_dir(antares_report_dir)


@pytest.fixture
def scanner_app(monkeypatch):
    """Reload app.main with a given environment, returning a TestClient factory."""
    from fastapi.testclient import TestClient

    def _build(**env):
        env.setdefault("SCANNER_BACKEND", "replay")
        env.setdefault("REPLAY_DIR", str(REPLAY_DIR))
        env.setdefault("REPLAY_MAX_DELAY_SECONDS", "0")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for mod in ("app.main", "app.backends", "app.attest"):
            if mod in sys.modules:
                del sys.modules[mod]
        main = importlib.import_module("app.main")
        return TestClient(main.app), main

    return _build


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Stop a stray environment from leaking between tests.

    Every env var the service reads is cleared, because `scanner_app` reloads
    modules and a value left set by one test would otherwise change another's
    behaviour depending on collection order.
    """
    for key in ("FAIL_ON", "MIN_CONFIDENCE", "MODEL_DIGEST", "MODEL_MAX_TOKENS",
                "SCANNER_BACKEND", "REPLAY_FALLBACK", "MODEL_ENDPOINT",
                "REPLAY_DIR", "REPLAY_MAX_DELAY_SECONDS",
                "MODEL_TIMEOUT_SECONDS", "MODEL_NAME", "MODEL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
