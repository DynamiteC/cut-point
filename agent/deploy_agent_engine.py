"""Scripted deployment of the CutPoint ADK app to Vertex AI Agent Engine.

Usage:
    python -m agent.deploy_agent_engine --dry-run
    python -m agent.deploy_agent_engine
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_ENV = ["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GEMINI_MODEL"]
REQUIREMENTS = [
    "google-adk",
    "google-cloud-aiplatform[adk,agent_engines]>=1.101.0",
    "google-genai",
    "mcp-clickhouse",
    "httpx",
    "pydantic>=2",
]


def validate_config() -> dict:
    from ingest.errors import MissingCredentialError

    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise MissingCredentialError(missing[0])

    return {
        "project": os.environ["GOOGLE_CLOUD_PROJECT"],
        "location": os.environ.get("GCP_REGION", "us-central1"),
        "display_name": "cutpoint-agent",
        "requirements": REQUIREMENTS,
    }


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Deploy CutPoint agent to Agent Engine")
    parser.add_argument("--dry-run", action="store_true", help="validate config without deploying")
    args = parser.parse_args()

    config = validate_config()
    print("Agent Engine deploy config:")
    for key, value in config.items():
        print(f"  {key}: {value}")

    if args.dry_run:
        print("\ndry-run: config validated, not deploying")
        return 0

    from vertexai import agent_engines

    from agent.cutpoint_agent.agent import build_root_agent

    agent_engines.create(
        agent_engine=build_root_agent(),
        requirements=config["requirements"],
        display_name=config["display_name"],
    )
    print("deployed to Agent Engine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
