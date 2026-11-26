#!/usr/bin/env python3
"""Merge complete, verified proofs without a human (Python 3.11+, standard library only).

Runs from the default branch after "PR checks" succeed (workflow_run), so no PR code executes
with write access. It merges only a PR that *adds* exactly one attempt folder
(Attempt.lean + attempt.toml) to a statement that already exists on main, and whose
Attempt.lean has no `sorry`. CI has already compiled it and audited its axioms. Everything else,
including new statements and partial attempts, waits for a maintainer.

    python3 -m tools.automerge <head-sha>
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from tools import arginf

_SORRY = re.compile(r"\bsorry\b")


@dataclass(frozen=True, slots=True)
class Decision:
    merge: bool
    reason: str
    theorem: str | None = None
    attempt: str | None = None


def decide(
    files: list[dict[str, Any]],
    statement_exists: Callable[[str], bool],
    attempt_lean: Callable[[str], str],
) -> Decision:
    expected = {"Attempt.lean", "attempt.toml"}
    folders: set[tuple[str, str]] = set()
    names: set[str] = set()
    for f in files:
        p = PurePosixPath(str(f.get("filename", "")))
        parts = p.parts
        if ".." in parts or p.is_absolute():
            return Decision(False, f"path trick: {p}")
        if f.get("status") != "added":
            return Decision(False, f"{p} is {f.get('status')}, not a new file")
        if (
            len(parts) != 5
            or parts[0] != arginf.ROOT_DIR
            or not arginf.HASH_RE.match(parts[1])
            or parts[2] != arginf.ATTEMPT_DIR
            or not arginf.HASH_RE.match(parts[3])
            or parts[4] not in expected
        ):
            return Decision(False, f"{p} is not part of a new attempt")
        folders.add((parts[1], parts[3]))
        names.add(parts[4])
    if len(folders) != 1 or names != expected:
        return Decision(False, "must add exactly one attempt (Attempt.lean and attempt.toml)")
    ((h, a),) = folders
    if not statement_exists(h):
        return Decision(False, f"statement {h} is not on main yet (new statements need review)")
    code = arginf.strip_comments(
        attempt_lean(f"{arginf.ROOT_DIR}/{h}/{arginf.ATTEMPT_DIR}/{a}/Attempt.lean")
    )
    if _SORRY.search(code):
        return Decision(False, "partial attempt (sorry): a maintainer merges checkpoints", h, a)
    return Decision(True, "complete proof of an existing statement, checks passed", h, a)


def _api(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - fixed https://api.github.com / raw hosts
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "arginf-automerge",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        raw = r.read()
    return json.loads(raw) if raw else None


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or not re.fullmatch(r"[0-9a-f]{40}", args[0]):
        print("usage: automerge <head-sha>", file=sys.stderr)
        return 2
    (sha,) = args
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    base = f"https://api.github.com/repos/{repo}"
    # workflow_run doesn't list fork PRs, so find the open PR whose head is exactly this commit.
    open_prs = _api("GET", f"{base}/pulls?state=open&base=main&per_page=100", token)
    matches = [p for p in open_prs if p["head"]["sha"] == sha]
    if len(matches) != 1:
        print("skip: no single open PR at this commit (closed, updated or ambiguous)")
        return 0
    number = str(matches[0]["number"])
    files = _api("GET", f"{base}/pulls/{number}/files?per_page=100", token)

    def statement_exists(h: str) -> bool:
        try:
            _api("GET", f"{base}/contents/{arginf.ROOT_DIR}/{h}/Statement.lean?ref=main", token)
            return True
        except OSError:
            return False

    def attempt_lean(path: str) -> str:
        meta = _api("GET", f"{base}/contents/{path}?ref={sha}", token)
        return base64.b64decode(meta["content"]).decode("utf-8")

    d = decide(files, statement_exists, attempt_lean)
    print(f"{'merge' if d.merge else 'skip'}: {d.reason}")
    if not d.merge:
        return 0
    _api(
        "PUT",
        f"{base}/pulls/{number}/merge",
        token,
        {
            "sha": sha,
            "merge_method": "merge",
            "commit_title": f"Merge #{number}: proof {d.attempt}",
        },
    )
    # Merges made with GITHUB_TOKEN don't trigger push workflows, so publish explicitly.
    _api("POST", f"{base}/actions/workflows/main.yml/dispatches", token, {"ref": "main"})
    print(f"merged #{number} and started the main workflow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
