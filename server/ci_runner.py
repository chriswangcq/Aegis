"""CI Runner — Aegis executes verification via SSH on remote machines.

Flow:
  1. Agent submits branch name (or commit_sha)
  2. Aegis SSHs into the project's configured ECS
  3. Remote: git clone repo_url → install deps → run tests/lint
  4. Aegis collects exit codes + output as tamper-proof evidence
  5. Remote work_dir is cleaned up after each run

All CI runs on the remote machine. Aegis never executes project code locally.
"""

import subprocess
import os
import re
from dataclasses import dataclass


@dataclass
class CIResult:
    gate: str
    passed: bool
    output: str          # raw output
    detail: str = ""     # human-readable summary


def _ssh_run(host: str, user: str, port: int, key_path: str,
             command: str, timeout: int = 120) -> tuple[int, str]:
    """Execute a command on a remote machine via SSH."""
    key_path = os.path.expanduser(key_path)
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-p", str(port),
        "-i", key_path,
        f"{user}@{host}",
        command,
    ]
    try:
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, f"SSH TIMEOUT after {timeout}s"
    except Exception as e:
        return -2, f"SSH ERROR: {e}"


def run_ci_via_ssh(repo_url: str, branch: str, commit_sha: str,
                   ci_config: dict,
                   test_specs: list[dict] | None = None,
                   checklist: list[dict] | None = None) -> list[CIResult]:
    """Full CI pipeline via SSH.

    1. SSH to remote → create temp dir
    2. git clone → checkout branch
    3. install deps
    4. run tests
    5. run lint (if configured)
    6. run kill_test (if checklist requires it)
    7. cleanup
    """
    host = ci_config.get("ssh_host", "")
    user = ci_config.get("ssh_user", "root")
    port = ci_config.get("ssh_port", 22)
    key_path = ci_config.get("ssh_key_path", "~/.ssh/id_rsa")
    work_dir = ci_config.get("work_dir", "/opt/aegis-ci")
    timeout = ci_config.get("timeout_seconds", 300)

    install_cmd = ci_config.get("install_command", "")
    test_cmd = ci_config.get("test_command", "python -m pytest tests/ -v --tb=short")
    lint_cmd = ci_config.get("lint_command", "")

    if not host:
        return [CIResult("ssh", False, "",
                          "ci_config.ssh_host is not configured")]

    results = []
    ref = commit_sha or branch or "main"

    # ── Step 1: Clone ──
    clone_script = f"""
set -e
rm -rf {work_dir}/{ref} 2>/dev/null || true
mkdir -p {work_dir}
git clone --depth 50 --branch {branch or 'main'} {repo_url} {work_dir}/{ref}
"""
    if commit_sha:
        clone_script += f"cd {work_dir}/{ref} && git checkout {commit_sha}\n"

    code, output = _ssh_run(host, user, port, key_path, clone_script, timeout=120)
    if code != 0:
        return [CIResult("git_clone", False, output,
                          f"Failed to clone {repo_url} @ {ref}")]
    results.append(CIResult("git_clone", True, output,
                             f"Cloned {repo_url} @ {ref}"))

    remote_path = f"{work_dir}/{ref}"

    # ── Step 2: Install deps ──
    if install_cmd:
        code, output = _ssh_run(
            host, user, port, key_path,
            f"cd {remote_path} && {install_cmd}",
            timeout=120)
        if code != 0:
            results.append(CIResult("install", False, output,
                                     f"Install failed: {install_cmd}"))
            _cleanup(host, user, port, key_path, remote_path)
            return results
        results.append(CIResult("install", True, output, "Dependencies installed"))

    # ── Step 3: Run tests ──
    code, output = _ssh_run(
        host, user, port, key_path,
        f"cd {remote_path} && {test_cmd}",
        timeout=timeout)

    # Parse test results
    match = re.search(r"(\d+) passed", output)
    passed_count = int(match.group(1)) if match else 0
    fail_match = re.search(r"(\d+) failed", output)
    failed_count = int(fail_match.group(1)) if fail_match else 0
    test_passed = code == 0 and passed_count > 0 and failed_count == 0
    results.append(CIResult("test", test_passed, output,
                             f"{passed_count} passed, {failed_count} failed"))

    # ── Step 4: Run lint (if configured) ──
    if lint_cmd:
        code, output = _ssh_run(
            host, user, port, key_path,
            f"cd {remote_path} && {lint_cmd}",
            timeout=60)
        results.append(CIResult("lint", code == 0, output,
                                 "lint clean" if code == 0 else f"lint failed"))

    # ── Step 5: Kill test (if checklist has [unit] items) ──
    need_kill = False
    if checklist:
        need_kill = any("[unit]" in c.get("description", "") for c in checklist)
    if need_kill:
        kill_result = _run_kill_test_remote(
            host, user, port, key_path, remote_path, timeout)
        results.append(kill_result)

    # ── Step 6: Spec coverage (local analysis of remote test names) ──
    if test_specs:
        spec_result = _run_spec_coverage_remote(
            host, user, port, key_path, remote_path, test_specs, timeout)
        results.append(spec_result)

    # ── Cleanup ──
    _cleanup(host, user, port, key_path, remote_path)

    return results


def _cleanup(host: str, user: str, port: int, key_path: str, remote_path: str):
    """Remove the cloned repo from the remote machine."""
    _ssh_run(host, user, port, key_path, f"rm -rf {remote_path}", timeout=30)


def _run_kill_test_remote(host: str, user: str, port: int, key_path: str,
                          remote_path: str, timeout: int) -> CIResult:
    """Kill test via SSH — mutate logic functions and verify tests break.

    Sends a self-contained Python script to run on the remote machine.
    """
    kill_script = '''
import ast, glob, os, subprocess, sys

repo = sys.argv[1]
logic_files = glob.glob(os.path.join(repo, "**/*_logic.py"), recursive=True)
logic_files += glob.glob(os.path.join(repo, "**/logic.py"), recursive=True)
logic_files = [f for f in logic_files
               if ".venv" not in f and "node_modules" not in f
               and not os.path.basename(f).startswith("test_")]

if not logic_files:
    print("SKIP: no logic files"); sys.exit(0)

survivors = []
for lf in logic_files:
    original = open(lf).read()
    try:
        tree = ast.parse(original)
    except SyntaxError:
        continue
    funcs = [n for n in ast.iter_child_nodes(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and not n.name.startswith("_")]
    for func in funcs:
        lines = original.split("\\n")
        indent = "    "
        if func.body:
            bl = lines[func.body[0].lineno - 1]
            indent = bl[:len(bl) - len(bl.lstrip())]
        mutated = lines[:func.lineno]
        has_ds = (func.body and isinstance(func.body[0], ast.Expr)
                  and isinstance(func.body[0].value, ast.Constant))
        if has_ds:
            mutated.extend(lines[func.lineno:func.body[0].end_lineno])
        mutated.append(f"{indent}raise NotImplementedError('KILLED')")
        mutated.extend(lines[func.end_lineno:])
        with open(lf, "w") as f:
            f.write("\\n".join(mutated))
        r = subprocess.run(["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=line"],
                           cwd=repo, capture_output=True, timeout=30)
        with open(lf, "w") as f:
            f.write(original)
        if r.returncode == 0:
            survivors.append(f"{os.path.basename(lf)}::{func.name}")

if survivors:
    print(f"FAIL: {len(survivors)} survivors: {', '.join(survivors[:5])}")
    sys.exit(1)
else:
    print(f"OK: all functions in {len(logic_files)} file(s) tested")
'''

    # Write script to remote, execute, clean up
    escaped = kill_script.replace("'", "'\\''")
    code, output = _ssh_run(
        host, user, port, key_path,
        f"echo '{escaped}' > /tmp/_aegis_kill.py && python /tmp/_aegis_kill.py {remote_path} && rm -f /tmp/_aegis_kill.py",
        timeout=timeout)

    if "SKIP" in output:
        return CIResult("kill_test", True, output, "No logic files — skipped")

    return CIResult("kill_test", code == 0, output,
                     "All mutants killed" if code == 0 else output.strip().split("\n")[-1])


def _run_spec_coverage_remote(host: str, user: str, port: int, key_path: str,
                               remote_path: str, test_specs: list[dict],
                               timeout: int) -> CIResult:
    """Check spec coverage by listing test function names from the remote repo."""
    # Get all test function names
    code, output = _ssh_run(
        host, user, port, key_path,
        f"cd {remote_path} && grep -rh 'def test_' tests/*.py 2>/dev/null || true",
        timeout=30)

    test_names = [line.strip() for line in output.split("\n")
                  if line.strip().startswith("def test_")]

    uncovered = []
    for spec in test_specs:
        spec_input = spec.get("input", "").lower()
        spec_expect = spec.get("expect", "").lower()
        keywords = [w for w in re.split(r'[^a-zA-Z]+', spec_input + " " + spec_expect) if len(w) > 2]

        covered = False
        for t in test_names:
            t_lower = t.lower()
            matches = sum(1 for kw in keywords if kw in t_lower)
            if matches >= max(1, len(keywords) // 2):
                covered = True
                break
        if not covered:
            uncovered.append(f"{spec_input} → {spec_expect}")

    if uncovered:
        detail = f"{len(uncovered)}/{len(test_specs)} specs uncovered"
        return CIResult("spec_coverage", False, "\n".join(uncovered[:5]), detail)

    return CIResult("spec_coverage", True, "",
                     f"All {len(test_specs)} test specs covered")


# ── Legacy compatibility ──────────────────────────────────────
# Keep function signatures for existing callers

def checkout_repo(repo_url: str, branch: str = "main",
                  commit_sha: str = "") -> tuple[str, CIResult | None]:
    """Legacy: used by check-deps endpoint. Runs locally."""
    import tempfile, shutil
    work_dir = tempfile.mkdtemp(prefix="aegis_ci_")
    cmd = ["git", "clone", "--depth", "50", repo_url, work_dir]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            shutil.rmtree(work_dir, ignore_errors=True)
            return "", CIResult("git_clone", False, result.stdout + result.stderr,
                                f"Failed to clone {repo_url}")
        return work_dir, None
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        return "", CIResult("git_clone", False, str(e), f"Clone error: {e}")
