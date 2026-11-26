"""Auto-merge: only a PR that adds one complete attempt to an existing statement qualifies."""

import unittest

from tools import automerge

H = "0123456789ab"
A = "ba9876543210"
COMPLETE = (
    f"import Theorems.«{H}».Statement\n\nnamespace X\n\n"
    f"theorem proof : Theorems.«{H}».statement :=\n  trivial\n\nend X\n"
)
PARTIAL = COMPLETE.replace("trivial", "by\n  sorry")


def files(*paths: str, status: str = "added") -> list[dict[str, str]]:
    return [{"filename": p, "status": status} for p in paths]


ATTEMPT = files(
    f"Theorems/{H}/attempts/{A}/Attempt.lean", f"Theorems/{H}/attempts/{A}/attempt.toml"
)


class DecideTests(unittest.TestCase):
    def ok(
        self, fs: list[dict[str, str]], lean: str = COMPLETE, exists: bool = True
    ) -> automerge.Decision:
        return automerge.decide(
            fs, statement_exists=lambda h: exists, attempt_lean=lambda path: lean
        )

    def test_complete_attempt_on_existing_statement_merges(self) -> None:
        d = self.ok(ATTEMPT)
        self.assertTrue(d.merge, d.reason)
        self.assertEqual((d.theorem, d.attempt), (H, A))

    def test_partial_attempt_waits_for_a_maintainer(self) -> None:
        self.assertFalse(self.ok(ATTEMPT, PARTIAL).merge)

    def test_sorry_hidden_in_comments_does_not_count_but_real_sorry_does(self) -> None:
        self.assertTrue(self.ok(ATTEMPT, COMPLETE + "-- sorry\n").merge)
        self.assertFalse(self.ok(ATTEMPT, COMPLETE.replace("trivial", "sorry")).merge)

    def test_new_statements_and_other_files_never_auto_merge(self) -> None:
        self.assertFalse(self.ok(files(f"Theorems/{H}/Statement.lean")).merge)
        self.assertFalse(self.ok(ATTEMPT + files(".github/workflows/pr.yml")).merge)
        self.assertFalse(self.ok(ATTEMPT + files("curation.toml")).merge)
        self.assertFalse(self.ok(ATTEMPT + files(f"Theorems/{H}/Statement.lean")).merge)

    def test_only_additions_of_one_attempt(self) -> None:
        self.assertFalse(self.ok(files(*[f["filename"] for f in ATTEMPT], status="modified")).merge)
        other = files(
            f"Theorems/{H}/attempts/{H}/Attempt.lean", f"Theorems/{H}/attempts/{H}/attempt.toml"
        )
        self.assertFalse(self.ok(ATTEMPT + other).merge)
        self.assertFalse(self.ok(files(f"Theorems/{H}/attempts/{A}/Attempt.lean")).merge)  # no toml

    def test_statement_must_already_exist_on_main(self) -> None:
        self.assertFalse(self.ok(ATTEMPT, exists=False).merge)

    def test_path_tricks(self) -> None:
        bad = files(
            f"Theorems/{H}/attempts/{A}/../../../lakefile.toml",
            f"Theorems/{H}/attempts/{A}/attempt.toml",
        )
        self.assertFalse(self.ok(bad).merge)


if __name__ == "__main__":
    unittest.main()
