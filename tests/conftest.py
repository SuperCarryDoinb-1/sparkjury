import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"


@pytest.fixture(scope="session", autouse=True)
def _ensure_samples() -> None:
    if not (SAMPLES / "tau2_retail_sample.json").exists() or not (SAMPLES / "otel_sample.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_samples.py")], check=True)


@pytest.fixture
def tau2_path() -> Path:
    return SAMPLES / "tau2_retail_sample.json"


@pytest.fixture
def otel_path() -> Path:
    return SAMPLES / "otel_sample.json"
