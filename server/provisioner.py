"""Project provisioner — setup when a project is created.

Handles:
  1. Generate API keys (master + agent + readonly)
  2. Store user-provided CI config
"""

import json
import time
from dataclasses import dataclass
from . import auth


@dataclass
class ProvisionResult:
    project_id: str
    api_keys: dict[str, str]


def provision_project(project_id: str, master_id: str, db_conn) -> ProvisionResult:
    """Provision a new project: generate API keys.

    Called automatically when POST /projects succeeds.
    """
    api_keys = auth.create_project_keys(project_id, master_id, db_conn)
    return ProvisionResult(project_id=project_id, api_keys=api_keys)
