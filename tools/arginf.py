#!/usr/bin/env python3
"""arginf contributor tool (Python 3.11+, standard library only).

    python3 tools/arginf.py hash Theorems/<hash>/Statement.lean
    python3 tools/arginf.py new --title "..." --kind lemma "<Lean Prop>"
    python3 tools/arginf.py check [--base origin/main]   # same checks as CI
    python3 tools/arginf.py index > index.json

Each theorem lives in Theorems/<hash>/. The hash is the first 12 hex digits of
SHA-256 over the normalized `def statement : Prop := ...` body, with comments
and whitespace normalized and names excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ROOT_DIR = "Theorems"
HASH_LEN = 12
HASH_RE = re.compile(rf"^[0-9a-f]{{{HASH_LEN}}}$")
ALLOWED_FILES = frozenset({"Statement.lean", "Proof.lean", "meta.toml", "README.md"})
KINDS = ("theorem", "lemma", "open-question")
TITLE_MAX = 200
DESCRIPTION_MAX = 5000
_STATEMENT_RE = re.compile(r"(?m)^def\s+statement\s*:\s*Prop\s*:=")
_COMMAND_RE = re.compile(
    r"(?m)^(?:end|theorem|lemma|def|abbrev|namespace|section|open|variable|instance|"
    r"example|structure|class|inductive|noncomputable|private|protected|@\[|#|import|set_option)\b"
)
_IMPORT_RE = re.compile(r"(?m)^import\s+Theorems\.«([0-9a-f]+)»\.(?:Proof|Statement)\s*$")


class ToolError(Exception):
    """A problem the contributor needs to fix."""


def strip_comments(text: str) -> str:
    """Remove Lean `--` and nested `/- -/` comments, leaving string literals intact."""
    out: list[str] = []
    i, n, depth = 0, len(text), 0
    while i < n:
        two = text[i : i + 2]
        if depth:
            if two == "/-":
                depth, i = depth + 1, i + 2
            elif two == "-/":
                depth, i = depth - 1, i + 2
                if depth == 0:
                    out.append(" ")
            else:
                i += 1
            continue
        if two == "/-":
            depth, i = 1, i + 2
        elif two == "--":
            while i < n and text[i] != "\n":
                i += 1
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    if depth:
        raise ToolError("unterminated block comment")
    return "".join(out)


def extract_statement(text: str) -> str:
    """Return the normalized Prop from `def statement : Prop := ...`."""
    code = strip_comments(text)
    matches = list(_STATEMENT_RE.finditer(code))
    if len(matches) != 1:
        raise ToolError("Statement.lean must contain exactly one `def statement : Prop :=`")
    rest = code[matches[0].end() :]
    nxt = _COMMAND_RE.search(rest)
    body = " ".join((rest[: nxt.start()] if nxt else rest).split())
    if not body:
        raise ToolError("the statement is empty")
    return body


def statement_hash(text: str) -> str:
    return hashlib.sha256(extract_statement(text).encode("utf-8")).hexdigest()[:HASH_LEN]


# Scope checks (used by CI on every PR) -------------------------------------


def check_scope(changed: list[str], branch: str) -> list[str]:
    errors: list[str] = []
    folders: set[str] = set()
    for raw in changed:
        p = PurePosixPath(raw)
        parts = p.parts
        if ".." in parts or p.is_absolute():
            errors.append(f"{raw}: path tricks are not allowed")
            continue
        if len(parts) != 3 or parts[0] != ROOT_DIR or not HASH_RE.match(parts[1]):
            errors.append(f"{raw}: outside a {ROOT_DIR}/<hash>/ folder")
            continue
        if parts[2] not in ALLOWED_FILES:
            errors.append(f"{raw}: only {', '.join(sorted(ALLOWED_FILES))} are allowed")
        folders.add(parts[1])
    if len(folders) > 1:
        errors.append(f"a PR may change one folder only, found {sorted(folders)}")
    if len(folders) == 1:
        (h,) = folders
        if not branch.startswith(f"{h}/"):
            errors.append(f"branch must start with '{h}/' (got '{branch}')")
    elif not errors and not changed:
        errors.append("no changes")
    if not folders and changed and not any("branch" in e for e in errors):
        errors.append("branch must start with '<hash>/' of the folder it changes")
    return errors


# Folders ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Meta:
    title: str
    kind: str
    description: str
    tags: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> tuple[Meta | None, list[str]]:
        try:
            raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as err:
            return None, [f"{path}: {err}"]
        errors: list[str] = []
        title, kind = raw.get("title"), raw.get("kind")
        desc, tags = raw.get("description", ""), raw.get("tags", [])
        if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
            errors.append(f"{path}: title must be 1-{TITLE_MAX} characters")
        if kind not in KINDS:
            errors.append(f"{path}: kind must be one of {', '.join(KINDS)}")
        if not isinstance(desc, str) or len(desc) > DESCRIPTION_MAX:
            errors.append(f"{path}: description must be a string up to {DESCRIPTION_MAX} chars")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append(f"{path}: tags must be a list of strings")
        unknown = set(raw) - {"title", "kind", "description", "tags"}
        if unknown:
            errors.append(f"{path}: unknown keys {sorted(unknown)}")
        if errors:
            return None, errors
        return cls(str(title), str(kind), str(desc), tuple(tags)), []


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def new_theorem(root: Path, title: str, prop: str, kind: str, description: str = "") -> str:
    if kind not in KINDS:
        raise ToolError(f"kind must be one of {', '.join(KINDS)}")
    body = f"def statement : Prop :=\n  {prop.strip()}\n"
    h = statement_hash(body)
    folder = root / ROOT_DIR / h
    if folder.exists():
        raise ToolError(f"{folder} already exists: this statement is already in the repo")
    folder.mkdir(parents=True)
    (folder / "Statement.lean").write_text(
        f"import Mathlib\n\nnamespace {ROOT_DIR}.«{h}»\n\n{body}\nend {ROOT_DIR}.«{h}»\n",
        encoding="utf-8",
    )
    (folder / "meta.toml").write_text(
        f"title = {_toml_str(title)}\nkind = {_toml_str(kind)}\n"
        f"description = {_toml_str(description)}\ntags = []\n",
        encoding="utf-8",
    )
    return h


def check_folder(folder: Path) -> list[str]:
    errors: list[str] = []
    stmt = folder / "Statement.lean"
    if not stmt.is_file():
        return [f"{folder}: missing Statement.lean"]
    try:
        h = statement_hash(stmt.read_text(encoding="utf-8"))
        if h != folder.name:
            errors.append(f"{folder}: folder name does not match statement hash {h}")
    except ToolError as err:
        errors.append(f"{stmt}: {err}")
    _, meta_errors = Meta.load(folder / "meta.toml")
    errors += meta_errors
    for f in folder.iterdir():
        if f.name not in ALLOWED_FILES:
            errors.append(f"{f}: only {', '.join(sorted(ALLOWED_FILES))} are allowed")
    return errors


# Axiom audit (CI runs this after `lake build`) ---------------------------------

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
_AXIOMS_RE = re.compile(r"depends on axioms: \[(.*?)\]", re.S)


def audit_source(h: str) -> str:
    """A Lean file that fails unless `proof` proves exactly `statement`, then lists axioms."""
    ns = f"{ROOT_DIR}.«{h}»"
    return (
        f"import {ns}.Proof\n"
        f"example : {ns}.statement := {ns}.proof\n"
        f"#print axioms {ns}.proof\n"
    )


def parse_axioms(output: str) -> list[str]:
    if "does not depend on any axioms" in output:
        return []
    m = _AXIOMS_RE.search(output)
    if not m:
        raise ToolError(f"could not read axioms from Lean output: {output.strip()[:300]}")
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def forbidden_axioms(axioms: list[str]) -> list[str]:
    return [a for a in axioms if a not in ALLOWED_AXIOMS]


def audit(root: Path, h: str) -> list[str]:
    if not HASH_RE.match(h):
        return [f"bad hash {h!r}"]
    src = root / ".lake" / f"Audit_{h}.lean"
    src.parent.mkdir(exist_ok=True)
    src.write_text(audit_source(h), encoding="utf-8")
    run = subprocess.run(
        ["lake", "env", "lean", str(src)], cwd=root, capture_output=True, text=True, timeout=1800
    )
    output = run.stdout + run.stderr
    if run.returncode != 0:
        return [f"{h}: Lean rejected the proof:\n{output.strip()[:2000]}"]
    bad = forbidden_axioms(parse_axioms(output))
    return [f"{h}: forbidden axioms {bad}"] if bad else []


# Index (rendered by arginf.com) ------------------------------------------------


def build_index(root: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    base = root / ROOT_DIR
    for folder in sorted(p for p in base.iterdir() if p.is_dir() and HASH_RE.match(p.name)):
        meta, errors = Meta.load(folder / "meta.toml")
        stmt_text = (folder / "Statement.lean").read_text(encoding="utf-8")
        proof = folder / "Proof.lean"
        proof_text = proof.read_text(encoding="utf-8") if proof.is_file() else ""
        deps = sorted(
            {d for d in _IMPORT_RE.findall(stmt_text + "\n" + proof_text) if d != folder.name}
        )
        entries[folder.name] = {
            "hash": folder.name,
            "title": meta.title if meta else folder.name,
            "kind": meta.kind if meta else "theorem",
            "description": meta.description if meta else "",
            "tags": list(meta.tags) if meta else [],
            "statement": extract_statement(stmt_text),
            "statementLean": stmt_text,
            "proofLean": proof_text or None,
            "status": "proved" if proof_text else "open",
            "dependencies": deps,
            "dependents": [],
            "valid": not errors,
        }
    for h, e in entries.items():
        for d in e["dependencies"]:
            if d in entries:
                entries[d]["dependents"].append(h)
    for e in entries.values():
        e["dependents"].sort()
    return {"version": 1, "theorems": list(entries.values())}


# CLI ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arginf")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_hash = sub.add_parser("hash", help="print the hash of a Statement.lean")
    p_hash.add_argument("file", type=Path)
    p_new = sub.add_parser("new", help="create Theorems/<hash>/ for a new statement")
    p_new.add_argument("prop", help="the Lean Prop, e.g. '∀ n : ℕ, n + 0 = n'")
    p_new.add_argument("--title", required=True)
    p_new.add_argument("--kind", choices=KINDS, default="theorem")
    p_new.add_argument("--description", default="")
    p_check = sub.add_parser("check", help="run the PR scope and folder checks locally")
    p_check.add_argument("--base", default="origin/main")
    p_check.add_argument("--branch", default=None)
    sub.add_parser("index", help="print index.json for the site")
    p_audit = sub.add_parser("audit", help="check proofs and axioms (needs `lake build` first)")
    p_audit.add_argument("hashes", nargs="*", help="default: every folder with a Proof.lean")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.cmd == "hash":
            print(statement_hash(args.file.read_text(encoding="utf-8")))
        elif args.cmd == "new":
            h = new_theorem(root, args.title, args.prop, args.kind, args.description)
            print(h)
            print(f"next: git switch -c {h}/statement && git add {ROOT_DIR}/{h}", file=sys.stderr)
        elif args.cmd == "check":
            branch = args.branch or _git("rev-parse", "--abbrev-ref", "HEAD").strip()
            changed = _git("diff", "--name-only", f"{args.base}...HEAD").split()
            errors = check_scope(changed, branch)
            for h in {PurePosixPath(c).parts[1] for c in changed if c.startswith(f"{ROOT_DIR}/")}:
                if HASH_RE.match(h) and (root / ROOT_DIR / h).is_dir():
                    errors += check_folder(root / ROOT_DIR / h)
            for e in errors:
                print(f"error: {e}", file=sys.stderr)
            if errors:
                return 1
            print("ok")
        elif args.cmd == "audit":
            hashes = args.hashes or sorted(
                p.parent.name for p in (root / ROOT_DIR).glob("*/Proof.lean")
            )
            errors = [e for h in hashes for e in audit(root, h)]
            for e in errors:
                print(f"error: {e}", file=sys.stderr)
            if errors:
                return 1
            print(f"ok: {len(hashes)} proof(s) use only {sorted(ALLOWED_AXIOMS)}")
        elif args.cmd == "index":
            json.dump(build_index(root), sys.stdout, ensure_ascii=False, indent=1)
            print()
    except ToolError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
