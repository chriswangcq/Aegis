"""CI Runner — Aegis executes verification itself.

Flow:
  1. Agent submits branch name (or commit_sha)
  2. Aegis does: git clone repo_url → git checkout branch
  3. Run: lint / pytest / kill_test / spec_coverage
  4. All results written as system evidence — agent cannot tamper
  5. Temporary clone is deleted after verification

All interaction is through git — no dependency on agent's local environment.
"""

import subprocess
import os
import re
import tempfile
import shutil
from dataclasses import dataclass, field


@dataclass
class CIResult:
    gate: str
    passed: bool
    output: str          # raw output
    detail: str = ""     # human-readable summary


def checkout_repo(repo_url: str, branch: str = "main",
                  commit_sha: str = "") -> tuple[str, CIResult | None]:
    """Clone a repo from URL and checkout specific branch/commit.

    Returns (work_dir, error_result).
    If error_result is not None, checkout failed.
    Caller must clean up work_dir when done.
    """
    work_dir = tempfile.mkdtemp(prefix="aegis_ci_")

    # Clone
    code, output = _run(
        ["git", "clone", "--depth", "50", repo_url, work_dir],
        cwd=work_dir, timeout=120
    )
    if code != 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        return "", CIResult("git_clone", False, output,
                            f"Failed to clone {repo_url}")

    # Checkout branch or commit
    ref = commit_sha or branch
    if ref and ref != "main":
        code, output = _run(
            ["git", "checkout", ref],
            cwd=work_dir, timeout=30
        )
        if code != 0:
            shutil.rmtree(work_dir, ignore_errors=True)
            return "", CIResult("git_checkout", False, output,
                                f"Failed to checkout {ref}")

    return work_dir, None


def _run(cmd: list[str], cwd: str, timeout: int = 60) -> tuple[int, str]:
    """Run a command and return (exit_code, output)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return -2, f"ERROR: {e}"


def run_lint(repo_path: str) -> CIResult:
    """Gate 1: Run lint_logic_purity.py on the repo."""
    lint_script = os.path.join(repo_path, "scripts", "lint_logic_purity.py")
    if not os.path.exists(lint_script):
        # Try parent directory (mono-repo structure)
        parent = os.path.dirname(repo_path)
        lint_script = os.path.join(parent, "scripts", "lint_logic_purity.py")

    if not os.path.exists(lint_script):
        return CIResult("lint_purity", True, "", "No lint script found — skipped")

    code, output = _run(["python", lint_script, repo_path], cwd=repo_path)
    passed = code == 0 and bool(re.search(r"Scanned \d+ logic files?, 0 violations", output))
    return CIResult("lint_purity", passed, output,
                    "lint clean" if passed else f"lint failed (exit {code})")


def run_pytest(repo_path: str, timeout: int = 120) -> CIResult:
    """Gate 2: Run pytest on the repo."""
    code, output = _run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
        cwd=repo_path, timeout=timeout
    )
    # Parse pytest output for pass/fail counts
    match = re.search(r"(\d+) passed", output)
    passed_count = int(match.group(1)) if match else 0
    fail_match = re.search(r"(\d+) failed", output)
    failed_count = int(fail_match.group(1)) if fail_match else 0

    passed = code == 0 and passed_count > 0 and failed_count == 0
    detail = f"{passed_count} passed, {failed_count} failed"
    return CIResult("pytest", passed, output, detail)


def run_kill_test(repo_path: str, timeout: int = 120) -> CIResult:
    """Gate 3: Kill test — delete each public function in _logic.py, verify tests break.

    For each _logic.py file:
      1. Find all public functions (def foo(...))
      2. For each function, replace body with 'raise NotImplementedError'
      3. Run pytest — tests MUST fail
      4. Restore original
      5. If tests still pass after mutation → kill_test FAILS (test is fake)
    """
    import ast
    import glob

    logic_files = glob.glob(os.path.join(repo_path, "**/*_logic.py"), recursive=True)
    logic_files += glob.glob(os.path.join(repo_path, "**/logic.py"), recursive=True)
    logic_files = [f for f in logic_files
                   if ".venv" not in f and "node_modules" not in f
                   and not os.path.basename(f).startswith("test_")]

    if not logic_files:
        return CIResult("kill_test", True, "", "No _logic.py files — skipped")

    surviving_mutants = []

    for lf in logic_files:
        original_content = open(lf).read()
        try:
            tree = ast.parse(original_content)
        except SyntaxError:
            continue

        public_funcs = [
            node for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]

        for func in public_funcs:
            # Mutate: replace function body with raise
            lines = original_content.split("\n")
            # Find the function body start (line after def)
            func_start = func.lineno - 1  # 0-indexed
            func_end = func.end_lineno  # 1-indexed, so this is the last line

            # Build mutated version: keep def line, replace body
            indent = "    "
            # Find actual indent of body
            if func.body:
                body_line = lines[func.body[0].lineno - 1]
                indent = body_line[:len(body_line) - len(body_line.lstrip())]

            mutated_lines = lines[:func_start + 1]  # keep everything up to and including def
            # Skip docstring if present
            has_docstring = (func.body and isinstance(func.body[0], ast.Expr)
                           and isinstance(func.body[0].value, (ast.Constant, ast.Str)))
            if has_docstring:
                ds_end = func.body[0].end_lineno
                mutated_lines.extend(lines[func_start + 1:ds_end])
                mutated_lines.append(f"{indent}raise NotImplementedError('KILLED by CC')")
            else:
                mutated_lines.append(f"{indent}raise NotImplementedError('KILLED by CC')")
            mutated_lines.extend(lines[func_end:])

            # Write mutated file
            with open(lf, "w") as f:
                f.write("\n".join(mutated_lines))

            # Run tests
            code, output = _run(
                ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=line"],
                cwd=repo_path, timeout=30
            )

            # Restore original
            with open(lf, "w") as f:
                f.write(original_content)

            # If tests STILL pass, this mutant survived — test is weak/fake
            if code == 0:
                surviving_mutants.append(f"{os.path.basename(lf)}::{func.name}")

    if surviving_mutants:
        detail = f"{len(surviving_mutants)} mutant(s) survived: {', '.join(surviving_mutants[:5])}"
        return CIResult("kill_test", False, detail, detail)

    return CIResult("kill_test", True, "",
                    f"All public functions in {len(logic_files)} logic file(s) properly tested")


def run_spec_coverage(repo_path: str, test_specs: list[dict]) -> CIResult:
    """Gate 4: Check if test_specs defined by Master are covered by actual tests.

    For each test_spec, check if there's a test function whose name or docstring
    relates to the spec's input/expect pattern.
    """
    if not test_specs:
        return CIResult("spec_coverage", True, "", "No test_specs defined — skipped")

    # Collect all test function names from the repo
    import ast
    import glob

    test_files = glob.glob(os.path.join(repo_path, "tests/test_*.py"))
    test_names = []
    for tf in test_files:
        try:
            tree = ast.parse(open(tf).read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # Get docstring if any
                ds = ast.get_docstring(node) or ""
                test_names.append({"name": node.name, "doc": ds, "file": os.path.basename(tf)})

    uncovered_specs = []
    for spec in test_specs:
        spec_input = spec.get("input", "").lower()
        spec_expect = spec.get("expect", "").lower()
        spec_desc = f"{spec_input} → {spec_expect}"

        # Check if any test name/doc references this spec
        covered = False
        for t in test_names:
            name_lower = t["name"].lower().replace("_", " ")
            doc_lower = t["doc"].lower()
            # Fuzzy match: check if key words from spec appear in test
            keywords = [w for w in re.split(r'[^a-zA-Z\u4e00-\u9fff]+', spec_input + " " + spec_expect) if len(w) > 2]
            matches = sum(1 for kw in keywords if kw in name_lower or kw in doc_lower)
            if matches >= max(1, len(keywords) // 2):
                covered = True
                break

        if not covered:
            uncovered_specs.append(spec_desc)

    if uncovered_specs:
        detail = f"{len(uncovered_specs)}/{len(test_specs)} specs uncovered: {'; '.join(uncovered_specs[:3])}"
        return CIResult("spec_coverage", False, detail, detail)

    return CIResult("spec_coverage", True, "",
                    f"All {len(test_specs)} test specs covered")


def run_all_gates(repo_path: str, test_specs: list[dict] | None = None,
                  checklist: list[dict] | None = None) -> list[CIResult]:
    """Run all CI gates on a local repo path. Returns list of CIResults."""
    results = []

    # Always run pytest
    results.append(run_pytest(repo_path))

    # Run lint if checklist mentions _logic.py or [unit]
    need_lint = False
    if checklist:
        for c in checklist:
            desc = c.get("description", "").lower()
            if "_logic" in desc or "[unit]" in desc:
                need_lint = True
                break
    if need_lint:
        results.append(run_lint(repo_path))

    # Run kill_test if checklist has [unit] items
    need_kill = False
    if checklist:
        need_kill = any("[unit]" in c.get("description", "") for c in checklist)
    if need_kill:
        results.append(run_kill_test(repo_path))

    # Run spec coverage if test_specs provided
    if test_specs:
        results.append(run_spec_coverage(repo_path, test_specs))

    return results


def run_all_gates_from_git(repo_url: str, branch: str = "main",
                           commit_sha: str = "",
                           test_specs: list[dict] | None = None,
                           checklist: list[dict] | None = None) -> list[CIResult]:
    """Full git-based CI: clone → checkout → run gates → cleanup.

    This is the production flow where Aegis is fully independent
    of the agent's local environment.

    Agent only needs to push to git and submit the branch name.
    """
    # Step 1: Clone and checkout
    work_dir, error = checkout_repo(repo_url, branch, commit_sha)
    if error:
        return [error]

    try:
        # Step 2: Run all gates on the cloned repo
        results = run_all_gates(work_dir, test_specs, checklist)

        # Prepend git info
        git_info = CIResult(
            "git_checkout", True, "",
            f"Cloned {repo_url} @ {commit_sha or branch}")
        return [git_info] + results
    finally:
        # Step 3: Always cleanup
        shutil.rmtree(work_dir, ignore_errors=True)

