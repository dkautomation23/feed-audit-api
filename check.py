"""Release gate: the README's numbers and the repository are re-checked against reality.

    python check.py

Starts the stack with docker compose (Postgres is also what the tests run on),
then runs every check below. Prints ALL CHECKS PASSED only if all of them pass;
exit code 1 otherwise.

Checks 7 and 8 use the maintainer's scanners, found next to this repository or
through SECRET_SCANNER / PRIVACY_SCAN. Without them the run ends with
"CHECKS PASSED, 2 SKIPPED", never with ALL CHECKS PASSED.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
API = os.environ.get("CHECK_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "demo-key-change-me")
MAX_TRACKED_BYTES = 5 * 1024 * 1024
MIN_TESTS, MIN_COVERAGE = 25, 85
SECRET_SCANNER = Path(os.environ.get("SECRET_SCANNER", ROOT.parent / "repo-secret-scanner" / "scan.py"))
PRIVACY_SCAN = Path(os.environ.get("PRIVACY_SCAN", ROOT.parent / "_tools" / "privacy_scan.py"))

# Secret-scanner findings reviewed by hand and known to be harmless, as
# "path:line": "reason". Any finding not listed here fails check 7.
KNOWN_FALSE_POSITIVES: dict[str, str] = {
    "tests/conftest.py:7": "the API key the test suite sets for its own throwaway app instance",
}

CYRILLIC = re.compile(r"[\u0400-\u04ff]")
HOME_PATH = re.compile(r"[a-z]:[\\/]users[\\/]", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ALLOWED_EMAIL = re.compile(r"@(users\.noreply\.github\.com|example\.com)$")
# The privacy scanner reports in Russian: "CRITICAL n, IMPORTANT n".
PRIVACY_TOTAL = re.compile(r"\u041a\u0420\u0418\u0422\u0418\u0427\u041d\u041e (\d+), "
                           r"\u0412\u0410\u0416\u041d\u041e (\d+)")
SAMPLE_KEYS = ("rows_read", "products", "variants", "rows_left_out", "findings_total", "findings_blocking",
               "delimiter", "encoding", "decimal")


class Failed(Exception):
    pass


class Skipped(Exception):
    pass


def sh(cmd: list, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def tracked_files() -> list[Path]:
    """What git would publish: tracked plus untracked-but-not-ignored."""
    out = sh(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if out.returncode:
        raise Failed(f"git ls-files failed: {out.stderr.strip()}")
    return [ROOT / p for p in out.stdout.split("\0") if p and (ROOT / p).is_file()]


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def block(name: str) -> str:
    found = re.search(rf"<!-- {name}:start -->\s*```\w*\n(.*?)\n```\s*<!-- {name}:end -->", readme(), re.S)
    if not found:
        raise Failed(f"README block '{name}' not found")
    return found.group(1)


def get(path: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(API + path, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read()


def check_compose() -> str:
    out = sh(["docker", "compose", "up", "-d", "--build", "--wait"])
    if out.returncode:
        raise Failed(f"docker compose exit {out.returncode}: {out.stderr.strip()[-300:]}")
    status, body = get("/health")
    if status != 200 or json.loads(body) != {"status": "ok", "database": "ok"}:
        raise Failed(f"/health {status} {body[:100]!r}")
    return "both containers healthy, /health 200, database ok"


@functools.lru_cache(maxsize=1)
def pytest_run() -> subprocess.CompletedProcess:
    return sh([PY, "-m", "pytest", "-q", "--cov=app", "--cov-report=term"])


def check_pytest() -> str:
    out = pytest_run()
    passed = re.search(r"(\d+) passed", out.stdout)
    if out.returncode or not passed:
        raise Failed(f"pytest exit {out.returncode}: {out.stdout.strip().splitlines()[-1:]}")
    if re.search(r"\d+ (skipped|failed|errors?)\b", out.stdout):
        raise Failed(f"pytest: {out.stdout.strip().splitlines()[-1]}")
    if int(passed.group(1)) < MIN_TESTS:
        raise Failed(f"only {passed.group(1)} tests passed, need at least {MIN_TESTS}")
    stated = re.search(r"\*\*(\d+) pytest tests\*\*", readme())
    if not stated or stated.group(1) != passed.group(1):
        raise Failed(f"README says {stated.group(1) if stated else 'nothing'} tests, pytest ran {passed.group(1)}")
    return f"{passed.group(1)} passed = README"


def check_coverage() -> str:
    total = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", pytest_run().stdout, re.M)
    if not total:
        raise Failed("no TOTAL line in the coverage report")
    if int(total.group(1)) < MIN_COVERAGE:
        raise Failed(f"coverage {total.group(1)}%, need at least {MIN_COVERAGE}%")
    stated = re.search(r"Line coverage of `app/`: \*\*(\d+)%\*\*", readme())
    if not stated or stated.group(1) != total.group(1):
        raise Failed(f"README says {stated.group(1) if stated else 'nothing'}%, coverage is {total.group(1)}%")
    return f"{total.group(1)}% = README"


def check_sample() -> str:
    raw = (ROOT / "sample" / "supplier-feed.csv").read_bytes()
    boundary = "check-py-boundary"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="supplier-feed.csv"\r\n'
            f"Content-Type: text/csv\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    status, answer = get("/runs", "POST", body, {"X-API-Key": API_KEY,
                                                  "Content-Type": f"multipart/form-data; boundary={boundary}"})
    run = json.loads(answer)
    actual = json.dumps({k: run[k] for k in SAMPLE_KEYS}, indent=2)
    if status not in (200, 201) or block("sample") != actual:
        raise Failed(f"POST /runs {status}; README sample block differs from the live answer:\n{actual}")
    return f"live POST /runs answer = README ({run['products']} products, {run['findings_total']} findings)"


def check_load() -> str:
    report = ROOT / "docs" / "load-check.txt"
    if not report.exists():
        raise Failed("docs/load-check.txt missing - run python scripts/load_check.py")
    if block("load") != report.read_text(encoding="utf-8").rstrip("\n"):
        raise Failed("README load block differs from docs/load-check.txt")
    return "README load numbers = docs/load-check.txt"


def check_ruff() -> str:
    out = sh([PY, "-m", "ruff", "check", "."])
    if out.returncode:
        raise Failed(out.stdout.strip()[-400:])
    return "clean"


def check_hygiene() -> str:
    problems = []
    files = tracked_files()
    for path in files:
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if CYRILLIC.search(line):
                problems.append(f"{rel}:{lineno} Cyrillic text")
            if HOME_PATH.search(line):
                problems.append(f"{rel}:{lineno} local home path")
            for email in EMAIL.findall(line):
                if not ALLOWED_EMAIL.search(email):
                    problems.append(f"{rel}:{lineno} email {email}")
    if problems:
        raise Failed("\n      ".join(problems[:20]))
    return f"{len(files)} files clean"


def check_secrets() -> str:
    if not SECRET_SCANNER.exists():
        raise Skipped(f"secret scanner not found at {SECRET_SCANNER.name}")
    out = sh([PY, str(SECRET_SCANNER), ".", "--format", "json"])
    try:
        found = json.loads(out.stdout)
    except ValueError:
        raise Failed(f"scanner exit {out.returncode}, unreadable output: {out.stderr.strip()[:200]}") from None
    if out.returncode not in (0, 1) or (out.returncode == 1 and not found):
        raise Failed(f"scanner exit {out.returncode}: {out.stderr.strip()[:200]}")
    new = []
    for f in found:
        key = f"{Path(f['file']).as_posix().removeprefix('./')}:{f['line']}"
        if key not in KNOWN_FALSE_POSITIVES:
            new.append(f"{key} [{f['rule']}]")
    if new:
        raise Failed("unreviewed findings: " + ", ".join(new))
    reviewed = "; ".join(f"{k} - {v}" for k, v in KNOWN_FALSE_POSITIVES.items())
    return f"{len(found)} finding(s), all reviewed: {reviewed}"


def check_privacy() -> str:
    if not PRIVACY_SCAN.exists():
        raise Skipped(f"privacy scanner not found at {PRIVACY_SCAN.name}")
    out = sh([PY, str(PRIVACY_SCAN), ROOT.name], cwd=ROOT.parent)
    totals = PRIVACY_TOTAL.findall(out.stdout)
    if out.returncode or not totals:
        raise Failed(f"privacy scan exit {out.returncode}, no totals line")
    critical, important = map(int, totals[-1])
    if critical or important:
        raise Failed(f"critical {critical}, important {important}")
    return "critical 0, important 0"


def check_sizes() -> str:
    big = [f"{p.relative_to(ROOT).as_posix()} {p.stat().st_size:,}" for p in tracked_files()
           if p.stat().st_size > MAX_TRACKED_BYTES]
    if big:
        raise Failed("files over 5 MB: " + ", ".join(big))
    return "no file over 5 MB"


CHECKS = [
    ("docker compose up --wait, /health 200", check_compose),
    ("pytest green, test count matches README", check_pytest),
    ("coverage matches README", check_coverage),
    ("sample feed answer matches README", check_sample),
    ("load numbers match README", check_load),
    ("ruff clean", check_ruff),
    ("secret scan", check_secrets),
    ("privacy scan", check_privacy),
    ("no Cyrillic, home paths or personal emails", check_hygiene),
    ("no large files", check_sizes),
]


def venv_python() -> Path | None:
    """The project's .venv interpreter, if there is one and we are not already in it."""
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists() and Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
            return candidate
    return None


def main() -> int:
    venv = venv_python()
    if venv:  # run under the pinned dependencies, not whatever python started us
        return subprocess.call([str(venv), __file__, *sys.argv[1:]])
    failed, skipped = 0, 0
    for number, (name, check) in enumerate(CHECKS, 1):
        try:
            print(f"ok    {number}. {name}: {check()}", flush=True)
        except Skipped as exc:
            skipped += 1
            print(f"SKIP  {number}. {name}: {exc}", flush=True)
        except Exception as exc:  # any crash in a check is a failure, not a pass
            failed += 1
            print(f"FAIL  {number}. {name}: {exc}", flush=True)
    if failed:
        print(f"\n{failed} CHECK(S) FAILED")
        return 1
    if skipped:
        print(f"\nCHECKS PASSED, {skipped} SKIPPED")
        return 0
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
