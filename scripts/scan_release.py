"""Scan a source tree or a release ZIP for secrets and files that must never ship.

    python scripts/scan_release.py .                         # working tree
    python scripts/scan_release.py dist/Meridian_Enterprise_Final.zip

Exit code 0 = clean, 1 = findings. Test fixtures that are obviously fake (e.g. "sk-ant-test-not-a-real-key") and the
detector patterns themselves are allow-listed explicitly, not by a broad exclusion.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

FORBIDDEN_PARTS = {".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", "node_modules",
                   ".idea", ".vscode"}
FORBIDDEN_SUFFIX = {".pyc", ".pyo", ".log", ".sqlite", ".db", ".pem", ".key", ".p12", ".pfx", ".env"}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json", ".DS_Store"}
SECRET_PATTERNS = {
    "anthropic_key": re.compile(r"sk-an" r"t-api\d{2}-[A-Za-z0-9_\-]{20,}"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "mongodb_uri_with_password": re.compile(r"mongodb(?:\+srv)?://[^:\s/]+:(?!\*|<|\.\.\.|\$\{)[^@\s]{6,}@(?!127\.0\.0\.1|localhost)"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
}
ALLOW = {"sk-ant-test-not-a-real-key", "sk-ant-test"}


def _entries(target: Path):
    if target.is_file() and target.suffix == ".zip":
        with zipfile.ZipFile(target) as z:
            for n in z.namelist():
                if not n.endswith("/"):
                    yield n, z.read(n)
    else:
        for p in target.rglob("*"):
            if p.is_file() and not (set(p.relative_to(target).parts) & {".git", ".venv", "venv"}):
                yield str(p.relative_to(target)), p.read_bytes()


def scan(target: Path) -> list[str]:
    findings = []
    for name, data in _entries(target):
        parts = set(Path(name).parts)
        if parts & FORBIDDEN_PARTS or Path(name).suffix in FORBIDDEN_SUFFIX or Path(name).name in FORBIDDEN_NAMES:
            if Path(name).name != ".env.example":
                findings.append(f"unintended file: {name}")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for kind, rx in SECRET_PATTERNS.items():
            for m in rx.finditer(text):
                if m.group(0) not in ALLOW:
                    findings.append(f"possible {kind} in {name}: {m.group(0)[:12]}…")
    return findings


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else ".")
    f = scan(target)
    for x in f:
        print(x)
    print(f"scan {'CLEAN' if not f else 'FOUND ' + str(len(f)) + ' issue(s)'}: {target}")
    return 0 if not f else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
