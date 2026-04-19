"""Project provisioner — auto-configure everything when a project is created.

Handles:
  1. Generate API keys (master + agent + readonly)
  2. Determine CI image from tech_stack
  3. Store CI environment config
  4. (Future) Generate deploy key for repo
  5. (Future) Register Prometheus scrape target
"""

import json
import time
from dataclasses import dataclass
from . import auth


@dataclass
class ProvisionResult:
    project_id: str
    api_keys: dict[str, str]
    ci_image: str
    ci_config: dict


# Tech stack → Docker image mapping
CI_IMAGES = {
    "python": "python:3.11-slim",
    "typescript": "node:20-slim",
    "node": "node:20-slim",
    "go": "golang:1.22-alpine",
    "rust": "rust:1.77-slim",
    "java": "eclipse-temurin:21-jdk-alpine",
}


def select_ci_image(tech_stack: list[str]) -> str:
    """Select the best CI Docker image for a project's tech stack."""
    for tech in tech_stack:
        tech_lower = tech.lower()
        if tech_lower in CI_IMAGES:
            return CI_IMAGES[tech_lower]
    return "python:3.11-slim"  # default


def provision_project(project_id: str, master_id: str,
                      tech_stack: list[str], db_conn) -> ProvisionResult:
    """Provision a new project with all necessary resources.

    Called automatically when POST /projects is successful.
    """
    now = int(time.time() * 1000)

    # 1. Generate API keys
    api_keys = auth.create_project_keys(project_id, master_id, db_conn)

    # 2. Determine CI image
    ci_image = select_ci_image(tech_stack)

    # 3. Store CI environment config
    ci_config = {
        "image": ci_image,
        "cpu_limit": "2",
        "memory_limit": "2Gi",
        "timeout_sec": 300,
        "network_mode": "none",
    }
    db_conn.execute(
        "INSERT OR REPLACE INTO project_environments "
        "(project_id,ci_image,cpu_limit,memory_limit,timeout_sec,network_mode,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (project_id, ci_image, ci_config["cpu_limit"],
         ci_config["memory_limit"], ci_config["timeout_sec"],
         ci_config["network_mode"], now)
    )
    db_conn.commit()

    return ProvisionResult(
        project_id=project_id,
        api_keys=api_keys,
        ci_image=ci_image,
        ci_config=ci_config,
    )
