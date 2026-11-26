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
ATTEMPT_DIR = "attempts"
ATTEMPT_FILES = frozenset({"Attempt.lean", "attempt.toml"})
NOTE_MAX = 500
KINDS = ("theorem", "lemma", "open-question")
TITLE_MAX = 200
DESCRIPTION_MAX = 5000
_STATEMENT_RE = re.compile(r"(?m)^def\s+statement\s*:\s*Prop\s*:=")
_COMMAND_RE = re.compile(
    r"(?m)^(?:end|theorem|lemma|def|abbrev|namespace|section|open|variable|instance|"
    r"example|structure|class|inductive|noncomputable|private|protected|@\[|#|import|set_option)\b"
)
_IMPORT_RE = re.compile(r"(?m)^import\s+Theorems\.«([0-9a-f]+)»\.(?:Proof|Statement)\s*$")
_ATTEMPT_IMPORT_RE = re.compile(
    r"(?m)^import\s+Theorems\.«([0-9a-f]+)»\.attempts\.«([0-9a-f]+)»\.Attempt\s*$"
)


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
    """A PR touches one theorem folder and at most one attempt inside it."""
    errors: list[str] = []
    folders: set[str] = set()
    attempts: set[str] = set()
    for raw in changed:
        p = PurePosixPath(raw)
        parts = p.parts
        if ".." in parts or p.is_absolute():
            errors.append(f"{raw}: path tricks are not allowed")
            continue
        if len(parts) < 3 or parts[0] != ROOT_DIR or not HASH_RE.match(parts[1]):
            errors.append(f"{raw}: outside a {ROOT_DIR}/<hash>/ folder")
            continue
        folders.add(parts[1])
        if len(parts) == 3:
            if parts[2] not in ALLOWED_FILES:
                errors.append(f"{raw}: only {', '.join(sorted(ALLOWED_FILES))} are allowed")
        elif len(parts) == 5 and parts[2] == ATTEMPT_DIR and HASH_RE.match(parts[3]):
            attempts.add(parts[3])
            if parts[4] not in ATTEMPT_FILES:
                errors.append(f"{raw}: only {', '.join(sorted(ATTEMPT_FILES))} are allowed")
        else:
            errors.append(f"{raw}: not an allowed file in {ROOT_DIR}/<hash>/")
    if len(folders) > 1:
        errors.append(f"a PR may change one folder only, found {sorted(folders)}")
    if len(attempts) > 1:
        errors.append(f"a PR may add one attempt only, found {sorted(attempts)}")
    if len(folders) == 1:
        (h,) = folders
        if not branch.startswith(f"{h}/"):
            errors.append(f"branch must start with '{h}/' (got '{branch}')")
    elif not changed:
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
        if f.name == ATTEMPT_DIR and f.is_dir():
            continue
        if f.name not in ALLOWED_FILES:
            errors.append(f"{f}: only {', '.join(sorted(ALLOWED_FILES))} are allowed")
    return errors


# Attempts ("lines"): content-addressed, possibly partial, forming a tree -------

_NS_LINE_RE = re.compile(r"(?m)^\s*(?:namespace|end)\b.*$")
_SORRY_RE = re.compile(r"\bsorry\b")


def _attempt_body(text: str) -> str:
    """Attempt.lean without comments and namespace/end boilerplate, whitespace-normalized."""
    return " ".join(_NS_LINE_RE.sub("", strip_comments(text)).split())


def attempt_hash(text: str, parent: str | None) -> str:
    material = f"parent:{parent or ''}\n{_attempt_body(text)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:HASH_LEN]


def _attempt_ns(h: str, a: str) -> str:
    return f"{ROOT_DIR}.«{h}».Attempt_{a}"


def _read_attempt_meta(folder: Path) -> tuple[str | None, str, list[str]]:
    try:
        raw: dict[str, Any] = tomllib.loads((folder / "attempt.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as err:
        return None, "", [f"{folder}/attempt.toml: {err}"]
    errors: list[str] = []
    parent, note = raw.get("parent", ""), raw.get("note", "")
    if not isinstance(parent, str) or (parent and not HASH_RE.match(parent)):
        errors.append(f"{folder}/attempt.toml: parent must be empty or a hash")
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        errors.append(f"{folder}/attempt.toml: note must be a string up to {NOTE_MAX} chars")
    if set(raw) - {"parent", "note"}:
        errors.append(
            f"{folder}/attempt.toml: unknown keys {sorted(set(raw) - {'parent', 'note'})}"
        )
    return (str(parent) or None) if isinstance(parent, str) else None, str(note), errors


def _write_attempt(folder: Path, h: str, a: str, code: str, parent: str | None, note: str) -> None:
    ns = _attempt_ns(h, a)
    folder.mkdir(parents=True)
    (folder / "Attempt.lean").write_text(
        f"import {ROOT_DIR}.«{h}».Statement\n\nnamespace {ns}\n\n{code.strip()}\n\nend {ns}\n",
        encoding="utf-8",
    )
    (folder / "attempt.toml").write_text(
        f"parent = {_toml_str(parent or '')}\nnote = {_toml_str(note)}\n", encoding="utf-8"
    )


def _attempt_code(text: str) -> str:
    """The code between the namespace lines (what a continuation copies)."""
    lines = [ln for ln in text.splitlines() if not _NS_LINE_RE.match(ln)]
    return "\n".join(ln for ln in lines if not ln.startswith("import ")).strip()


def start_attempt(root: Path, h: str, parent: str | None, note: str) -> str:
    base = root / ROOT_DIR / h
    if not (base / "Statement.lean").is_file():
        raise ToolError(f"no statement {h}")
    if parent:
        ptext = (base / ATTEMPT_DIR / parent / "Attempt.lean").read_text(encoding="utf-8")
        imports = [ln for ln in ptext.splitlines() if ln.startswith("import ")]
        code = "\n".join(imports[1:] + ["", _attempt_code(ptext)]).strip()
    else:
        code = f"theorem proof : {ROOT_DIR}.«{h}».statement := by\n  sorry"
    a = attempt_hash(code, parent)
    folder = base / ATTEMPT_DIR / a
    if folder.exists():
        raise ToolError(f"attempt {a} already exists")
    _write_attempt(folder, h, a, code, parent, note)
    return a


def seal_attempt(folder: Path) -> str:
    """Re-hash an attempt after editing it, renaming its folder and namespace."""
    h = folder.parent.parent.name
    parent, note, errors = _read_attempt_meta(folder)
    if errors:
        raise ToolError("; ".join(errors))
    text = (folder / "Attempt.lean").read_text(encoding="utf-8")
    extra_imports = [
        ln for ln in text.splitlines() if ln.startswith("import ") and "Statement" not in ln
    ]
    code = "\n".join(extra_imports + ["", _attempt_code(text)]).strip()
    a = attempt_hash(code, parent)
    if a == folder.name:
        return a
    target = folder.parent / a
    if target.exists():
        raise ToolError(f"attempt {a} already exists")
    _write_attempt(target, h, a, code, parent, note)
    for f in folder.iterdir():
        f.unlink()
    folder.rmdir()
    return a


def check_attempt(folder: Path) -> list[str]:
    errors: list[str] = []
    parent, _, meta_errors = _read_attempt_meta(folder)
    errors += meta_errors
    f = folder / "Attempt.lean"
    if not f.is_file():
        return errors + [f"{folder}: missing Attempt.lean"]
    text = f.read_text(encoding="utf-8")
    extra = [ln for ln in text.splitlines() if ln.startswith("import ") and "Statement" not in ln]
    code = "\n".join(extra + ["", _attempt_code(text)]).strip()
    if attempt_hash(code, parent) != folder.name:
        errors.append(f"{folder}: folder name does not match attempt hash (run `arginf.py seal`)")
    if parent and not (folder.parent / parent).is_dir():
        errors.append(f"{folder}: parent attempt {parent} not found")
    for g in folder.iterdir():
        if g.name not in ATTEMPT_FILES:
            errors.append(f"{g}: only {', '.join(sorted(ATTEMPT_FILES))} are allowed")
    return errors


def attempt_info(folder: Path) -> dict[str, Any]:
    parent, note, _ = _read_attempt_meta(folder)
    code = strip_comments((folder / "Attempt.lean").read_text(encoding="utf-8"))
    goals = len(_SORRY_RE.findall(code))
    return {
        "hash": folder.name,
        "parent": parent,
        "note": note,
        "openGoals": goals,
        "complete": goals == 0,
        "children": [],
    }


def promote(root: Path, h: str, a: str) -> None:
    """Make a complete attempt the theorem's Proof.lean."""
    folder = root / ROOT_DIR / h / ATTEMPT_DIR / a
    errors = check_attempt(folder)
    if errors:
        raise ToolError("; ".join(errors))
    if not attempt_info(folder)["complete"]:
        raise ToolError(f"attempt {a} still has open goals (sorry)")
    proof = root / ROOT_DIR / h / "Proof.lean"
    if proof.exists():
        raise ToolError(f"{proof} already exists: this theorem is already proved")
    proof.write_text(
        f"import {ROOT_DIR}.«{h}».{ATTEMPT_DIR}.«{a}».Attempt\n\n"
        f"namespace {ROOT_DIR}.«{h}»\n\n"
        f"theorem proof : statement := Attempt_{a}.proof\n\n"
        f"end {ROOT_DIR}.«{h}»\n",
        encoding="utf-8",
    )


# Axiom audit (CI runs this after `lake build`) ---------------------------------

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
_AXIOMS_RE = re.compile(r"depends on axioms: \[(.*?)\]", re.S)


def audit_source(h: str, attempt: str | None = None) -> str:
    """A Lean file that fails unless the proof proves exactly `statement`, then lists axioms."""
    ns = f"{ROOT_DIR}.«{h}»"
    if attempt:
        module, proof = (
            f"{ns}.{ATTEMPT_DIR}.«{attempt}».Attempt",
            f"{_attempt_ns(h, attempt)}.proof",
        )
    else:
        module, proof = f"{ns}.Proof", f"{ns}.proof"
    return f"import {module}\nexample : {ns}.statement := {proof}\n#print axioms {proof}\n"


def parse_axioms(output: str) -> list[str]:
    if "does not depend on any axioms" in output:
        return []
    m = _AXIOMS_RE.search(output)
    if not m:
        raise ToolError(f"could not read axioms from Lean output: {output.strip()[:300]}")
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def forbidden_axioms(axioms: list[str]) -> list[str]:
    return [a for a in axioms if a not in ALLOWED_AXIOMS]


def audit(root: Path, h: str, attempt: str | None = None) -> list[str]:
    if not HASH_RE.match(h) or (attempt is not None and not HASH_RE.match(attempt)):
        return [f"bad hash {h!r}/{attempt!r}"]
    src = root / ".lake" / f"Audit_{h}_{attempt or 'proof'}.lean"
    src.parent.mkdir(exist_ok=True)
    src.write_text(audit_source(h, attempt), encoding="utf-8")
    run = subprocess.run(
        ["lake", "env", "lean", str(src)], cwd=root, capture_output=True, text=True, timeout=1800
    )
    output = run.stdout + run.stderr
    if run.returncode != 0:
        return [f"{h}: Lean rejected the proof:\n{output.strip()[:2000]}"]
    bad = forbidden_axioms(parse_axioms(output))
    return [f"{h}: forbidden axioms {bad}"] if bad else []


# Index (rendered by arginf.com) ------------------------------------------------


def _attempt_tree(folder: Path) -> list[dict[str, Any]]:
    adir = folder / ATTEMPT_DIR
    if not adir.is_dir():
        return []
    nodes = {
        p.name: attempt_info(p)
        for p in sorted(adir.iterdir())
        if p.is_dir() and HASH_RE.match(p.name)
    }
    for n in nodes.values():
        if n["parent"] in nodes:
            nodes[n["parent"]]["children"].append(n["hash"])
    return list(nodes.values())


def build_index(root: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    base = root / ROOT_DIR
    for folder in sorted(p for p in base.iterdir() if p.is_dir() and HASH_RE.match(p.name)):
        meta, errors = Meta.load(folder / "meta.toml")
        stmt_text = (folder / "Statement.lean").read_text(encoding="utf-8")
        proof = folder / "Proof.lean"
        proof_text = proof.read_text(encoding="utf-8") if proof.is_file() else ""
        for ah, aa in _ATTEMPT_IMPORT_RE.findall(proof_text):
            att = root / ROOT_DIR / ah / ATTEMPT_DIR / aa / "Attempt.lean"
            if ah == folder.name and att.is_file():
                proof_text += "\n" + att.read_text(encoding="utf-8")
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
            "attempts": _attempt_tree(folder),
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
    p_att = sub.add_parser("attempt", help="start a new attempt, optionally continuing another")
    p_att.add_argument("theorem")
    p_att.add_argument("--from", dest="parent", default=None, help="attempt hash to continue")
    p_att.add_argument("--note", default="")
    p_seal = sub.add_parser("seal", help="re-hash an edited attempt folder")
    p_seal.add_argument("folder", type=Path)
    p_prom = sub.add_parser("promote", help="make a complete attempt the theorem's proof")
    p_prom.add_argument("theorem")
    p_prom.add_argument("attempt")
    p_audit = sub.add_parser("audit", help="check proofs and axioms (needs `lake build` first)")
    p_audit.add_argument("hashes", nargs="*", help="default: every folder with a Proof.lean")
    p_audit.add_argument("--attempt", default=None, help="audit this complete attempt instead")
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
            for c in changed:
                parts = PurePosixPath(c).parts
                if len(parts) >= 3 and parts[0] == ROOT_DIR and HASH_RE.match(parts[1]):
                    if (root / ROOT_DIR / parts[1]).is_dir():
                        errors += check_folder(root / ROOT_DIR / parts[1])
                    if len(parts) == 5 and parts[2] == ATTEMPT_DIR:
                        att = root / ROOT_DIR / parts[1] / ATTEMPT_DIR / parts[3]
                        if att.is_dir():
                            errors += check_attempt(att)
            errors = list(dict.fromkeys(errors))
            for e in errors:
                print(f"error: {e}", file=sys.stderr)
            if errors:
                return 1
            print("ok")
        elif args.cmd == "attempt":
            a = start_attempt(root, args.theorem, args.parent, args.note)
            print(a)
            print(
                f"next: edit {ROOT_DIR}/{args.theorem}/{ATTEMPT_DIR}/{a}/Attempt.lean, then "
                f"`arginf.py seal` it",
                file=sys.stderr,
            )
        elif args.cmd == "seal":
            print(seal_attempt(args.folder))
        elif args.cmd == "promote":
            promote(root, args.theorem, args.attempt)
            print(f"wrote {ROOT_DIR}/{args.theorem}/Proof.lean")
        elif args.cmd == "audit":
            hashes = args.hashes or sorted(
                p.parent.name for p in (root / ROOT_DIR).glob("*/Proof.lean")
            )
            errors = [e for h in hashes for e in audit(root, h, args.attempt)]
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
