"""Preflight checks for CutPoint: env vars, ClickHouse reachability, gcloud ADC,
Vertex AI model availability, ffmpeg/ffprobe, uv.

Usage:
    uv run python scripts/preflight.py                 # exits 1 on any hard-requirement FAIL
    uv run python scripts/preflight.py --report-only    # always exits 0, prints PASS/FAIL table
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_ENV_VARS = [
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GEMINI_MODEL",
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    fix: str = ""
    hard_requirement: bool = True


def check_binary(name: str, fix: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(f"binary:{name}", True, path)
    return CheckResult(f"binary:{name}", False, "not found on PATH", fix)


def check_env_vars() -> list[CheckResult]:
    results = []
    for var in REQUIRED_ENV_VARS:
        value = os.environ.get(var)
        if value:
            results.append(CheckResult(f"env:{var}", True, "set"))
        else:
            results.append(
                CheckResult(
                    f"env:{var}",
                    False,
                    "not set",
                    f"set {var} in .env (see .env.example)",
                    hard_requirement=False,
                )
            )
    return results


def check_clickhouse() -> CheckResult:
    host = os.environ.get("CLICKHOUSE_HOST")
    if not host:
        return CheckResult(
            "clickhouse:reachable",
            False,
            "CLICKHOUSE_HOST not set",
            "set CLICKHOUSE_HOST in .env -- see README section Setup",
            hard_requirement=False,
        )
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=host,
            port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
            username=os.environ.get("CLICKHOUSE_USER", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
            secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
            verify=os.environ.get("CLICKHOUSE_VERIFY", "true").lower() == "true",
            connect_timeout=5,
        )
        client.ping()
        return CheckResult("clickhouse:reachable", True, f"connected to {host}")
    except Exception as exc:  # noqa: BLE001 - report any connectivity failure
        return CheckResult(
            "clickhouse:reachable",
            False,
            f"{type(exc).__name__}: {exc}",
            "verify CLICKHOUSE_HOST/PORT/USER/PASSWORD in .env and that the service is running",
            hard_requirement=False,
        )


def check_gcloud_adc() -> CheckResult:
    if not shutil.which("gcloud"):
        return CheckResult(
            "gcloud:cli",
            False,
            "gcloud not found on PATH",
            "install Google Cloud SDK: https://cloud.google.com/sdk/docs/install",
            hard_requirement=False,
        )
    try:
        proc = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return CheckResult("gcloud:adc", True, "application default credentials valid")
        return CheckResult(
            "gcloud:adc",
            False,
            proc.stderr.strip()[:200] or "no ADC token",
            "run: gcloud auth application-default login",
            hard_requirement=False,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "gcloud:adc",
            False,
            f"{type(exc).__name__}: {exc}",
            "run: gcloud auth application-default login",
            hard_requirement=False,
        )


def check_vertex_model(adc_ok: bool) -> CheckResult:
    model = os.environ.get("GEMINI_MODEL", "")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if not model or not project or not adc_ok:
        return CheckResult(
            "vertex:model-available",
            False,
            "skipped -- requires GEMINI_MODEL, GOOGLE_CLOUD_PROJECT and valid ADC",
            "set GOOGLE_CLOUD_PROJECT in .env and run gcloud auth application-default login",
            hard_requirement=False,
        )
    try:
        from google import genai

        client = genai.Client(vertexai=True, project=project, location=location)
        models = [m.name for m in client.models.list()]
        matches = [m for m in models if model in m]
        if matches:
            return CheckResult("vertex:model-available", True, f"found {matches[0]}")
        return CheckResult(
            "vertex:model-available",
            False,
            f"{model} not found in {len(models)} available models",
            "check GEMINI_MODEL spelling or choose a listed model",
            hard_requirement=False,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "vertex:model-available",
            False,
            f"{type(exc).__name__}: {exc}",
            "verify Vertex AI API is enabled and ADC has aiplatform.user role",
            hard_requirement=False,
        )


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(check_binary("uv", "install uv: https://docs.astral.sh/uv/getting-started/installation/"))
    results.append(check_binary("ffmpeg", "install ffmpeg: brew install ffmpeg"))
    results.append(check_binary("ffprobe", "install ffmpeg (bundles ffprobe): brew install ffmpeg"))
    results.extend(check_env_vars())
    results.append(check_clickhouse())
    adc_result = check_gcloud_adc()
    results.append(adc_result)
    results.append(check_vertex_model(adc_ok=adc_result.passed))
    return results


def print_report(results: list[CheckResult]) -> None:
    name_width = max(len(r.name) for r in results) + 2
    print(f"{'CHECK'.ljust(name_width)}{'STATUS'.ljust(8)}DETAIL")
    print("-" * 100)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{r.name.ljust(name_width)}{status.ljust(8)}{r.detail}")
        if not r.passed and r.fix:
            print(f"{''.ljust(name_width)}{''.ljust(8)}fix: {r.fix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="CutPoint preflight checks")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="always exit 0; use for CI/gates that tolerate missing cloud creds",
    )
    args = parser.parse_args()

    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    results = run_checks()
    print_report(results)

    hard_failures = [r for r in results if not r.passed and r.hard_requirement]
    if args.report_only:
        return 0
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
