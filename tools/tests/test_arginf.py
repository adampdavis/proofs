"""Tests for the arginf contributor tool (stdlib only: `python3 -m unittest`)."""

import json
import tempfile
import unittest
from pathlib import Path

from tools import arginf

STATEMENT = """\
import Mathlib

/-! A doc comment that must not affect the hash. -/
namespace Theorems.«{h}»

-- the statement
def statement : Prop :=
  ∀ n : ℕ,   n + 0 = n   -- trailing comment

end Theorems.«{h}»
"""


class HashTests(unittest.TestCase):
    def test_extracts_and_normalizes_prop(self) -> None:
        self.assertEqual(arginf.extract_statement(STATEMENT), "∀ n : ℕ, n + 0 = n")

    def test_hash_ignores_comments_whitespace_and_namespace(self) -> None:
        a = arginf.statement_hash(STATEMENT.replace("{h}", "aaa"))
        b = arginf.statement_hash("namespace X\ndef statement : Prop := ∀ n : ℕ, n + 0 = n\n")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{12}$")

    def test_hash_changes_with_statement(self) -> None:
        a = arginf.statement_hash("def statement : Prop := 1 = 1")
        b = arginf.statement_hash("def statement : Prop := 2 = 2")
        self.assertNotEqual(a, b)

    def test_nested_block_comments(self) -> None:
        text = "def statement : Prop := /- a /- b -/ c -/ True"
        self.assertEqual(arginf.extract_statement(text), "True")

    def test_rejects_missing_or_duplicate_statement(self) -> None:
        with self.assertRaises(arginf.ToolError):
            arginf.extract_statement("theorem x : True := trivial")
        with self.assertRaises(arginf.ToolError):
            arginf.extract_statement("def statement : Prop := True\ndef statement : Prop := False")

    def test_statement_ends_at_next_command(self) -> None:
        text = "def statement : Prop := True\n\ntheorem helper : True := trivial\nend X"
        self.assertEqual(arginf.extract_statement(text), "True")


class ScopeTests(unittest.TestCase):
    H = "0123456789ab"

    def test_accepts_single_folder_and_matching_branch(self) -> None:
        files = [f"Theorems/{self.H}/Proof.lean", f"Theorems/{self.H}/meta.toml"]
        self.assertEqual(arginf.check_scope(files, f"{self.H}/my-proof"), [])

    def test_rejects_files_outside_folder(self) -> None:
        errs = arginf.check_scope([f"Theorems/{self.H}/Proof.lean", "lakefile.lean"], f"{self.H}/x")
        self.assertTrue(any("outside" in e for e in errs))

    def test_rejects_two_folders(self) -> None:
        errs = arginf.check_scope(
            [f"Theorems/{self.H}/Proof.lean", "Theorems/ba9876543210/Proof.lean"], f"{self.H}/x"
        )
        self.assertTrue(errs)

    def test_rejects_branch_without_hash_prefix(self) -> None:
        errs = arginf.check_scope([f"Theorems/{self.H}/Proof.lean"], "main")
        self.assertTrue(any("branch" in e for e in errs))

    def test_rejects_unexpected_file_names(self) -> None:
        errs = arginf.check_scope([f"Theorems/{self.H}/evil.sh"], f"{self.H}/x")
        self.assertTrue(any("allowed" in e for e in errs))

    def test_rejects_path_tricks(self) -> None:
        errs = arginf.check_scope([f"Theorems/{self.H}/../../lakefile.lean"], f"{self.H}/x")
        self.assertTrue(errs)


class FolderTests(unittest.TestCase):
    def _repo(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "Theorems").mkdir()
        return root

    def test_new_creates_folder_named_by_hash(self) -> None:
        root = self._repo()
        h = arginf.new_theorem(root, "Add zero", "∀ n : ℕ, n + 0 = n", kind="lemma")
        folder = root / "Theorems" / h
        self.assertTrue((folder / "Statement.lean").is_file())
        self.assertTrue((folder / "meta.toml").is_file())
        self.assertEqual(arginf.check_folder(folder), [])
        with self.assertRaises(arginf.ToolError):
            arginf.new_theorem(root, "Again", "∀ n : ℕ, n + 0 = n", kind="lemma")

    def test_check_folder_detects_hash_mismatch(self) -> None:
        root = self._repo()
        h = arginf.new_theorem(root, "T", "True", kind="theorem")
        stmt = root / "Theorems" / h / "Statement.lean"
        stmt.write_text(stmt.read_text().replace("  True", "  False"))
        self.assertTrue(any("hash" in e for e in arginf.check_folder(root / "Theorems" / h)))

    def test_meta_is_validated(self) -> None:
        root = self._repo()
        h = arginf.new_theorem(root, "T", "True", kind="theorem")
        (root / "Theorems" / h / "meta.toml").write_text('title = ""\nkind = "banana"\n')
        errs = arginf.check_folder(root / "Theorems" / h)
        self.assertTrue(any("title" in e for e in errs))
        self.assertTrue(any("kind" in e for e in errs))

    def test_index_lists_status_and_dependencies(self) -> None:
        root = self._repo()
        lemma = arginf.new_theorem(root, "Lemma", "True", kind="lemma")
        main = arginf.new_theorem(root, "Main", "1 = 1", kind="theorem")
        (root / "Theorems" / lemma / "Proof.lean").write_text(
            f"import Theorems.«{lemma}».Statement\nnamespace Theorems.«{lemma}»\n"
            "theorem proof : statement := trivial\n"
        )
        (root / "Theorems" / main / "Proof.lean").write_text(
            f"import Theorems.«{main}».Statement\nimport Theorems.«{lemma}».Proof\n"
            f"namespace Theorems.«{main}»\ntheorem proof : statement := rfl\n"
        )
        index = arginf.build_index(root)
        by_id = {t["hash"]: t for t in index["theorems"]}
        self.assertEqual(by_id[main]["status"], "proved")
        self.assertEqual(by_id[main]["dependencies"], [lemma])
        self.assertEqual(by_id[lemma]["dependents"], [main])
        self.assertEqual(by_id[main]["statement"], "1 = 1")
        json.dumps(index)  # serializable

    def test_index_marks_unproved(self) -> None:
        root = self._repo()
        h = arginf.new_theorem(root, "Open", "True", kind="open-question")
        index = arginf.build_index(root)
        self.assertEqual(index["theorems"][0]["status"], "open")
        self.assertEqual(index["theorems"][0]["hash"], h)


if __name__ == "__main__":
    unittest.main()


class AxiomTests(unittest.TestCase):
    def test_standard_axioms_pass(self) -> None:
        out = "'Theorems.«abc».proof' depends on axioms: [propext, Classical.choice, Quot.sound]\n"
        self.assertEqual(arginf.parse_axioms(out), ["propext", "Classical.choice", "Quot.sound"])
        self.assertEqual(arginf.forbidden_axioms(arginf.parse_axioms(out)), [])

    def test_no_axioms(self) -> None:
        out = "'Theorems.«abc».proof' does not depend on any axioms\n"
        self.assertEqual(arginf.parse_axioms(out), [])

    def test_sorry_native_decide_and_custom_axioms_fail(self) -> None:
        out = "'x' depends on axioms: [propext, sorryAx, Lean.ofReduceBool, Theorems.«abc».cheat]"
        bad = arginf.forbidden_axioms(arginf.parse_axioms(out))
        self.assertEqual(bad, ["sorryAx", "Lean.ofReduceBool", "Theorems.«abc».cheat"])

    def test_unparseable_output_is_an_error(self) -> None:
        with self.assertRaises(arginf.ToolError):
            arginf.parse_axioms("error: unknown identifier")

    def test_audit_source_pins_the_statement(self) -> None:
        src = arginf.audit_source("0123456789ab")
        self.assertIn("import Theorems.«0123456789ab».Proof", src)
        self.assertIn("example : Theorems.«0123456789ab».statement := Theorems.«0123456789ab».proof", src)
        self.assertIn("#print axioms Theorems.«0123456789ab».proof", src)
