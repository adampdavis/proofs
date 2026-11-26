# Contributing

Everything happens through GitHub. There are no site accounts. You need `git`, `gh` (logged in), Python 3.11+, and, to build locally, [elan](https://github.com/leanprover/elan).

## Rules CI enforces
1. A PR changes exactly one folder `Theorems/<hash>/`, and only `Statement.lean`, `Proof.lean`, `meta.toml` or `README.md` inside it.
2. Your branch is named `<hash>/<anything>`.
3. The folder name equals the hash of `Statement.lean`. A different statement is a different folder, and existing statements are never edited.
4. `Proof.lean` declares `theorem proof : statement` in `namespace Theorems.«<hash>»`, and uses only the axioms `propext`, `Classical.choice` and `Quot.sound`.

## Add a statement
```bash
gh repo fork arginf/proofs --clone && cd proofs
H=$(python3 tools/arginf.py new --kind lemma --title "Adding zero" "∀ n : ℕ, n + 0 = n")
git switch -c "$H/statement" && git add "Theorems/$H" && git commit -m "Add $H: Adding zero"
python3 tools/arginf.py check
git push -u origin HEAD && gh pr create --repo arginf/proofs --fill
```

## Prove an existing statement
```bash
H=<hash from arginf.com or Theorems/>
git switch -c "$H/proof"
cat > "Theorems/$H/Proof.lean" <<LEAN
import Theorems.«$H».Statement
-- import Theorems.«<other-hash>».Proof   -- build on merged results
namespace Theorems.«$H»
theorem proof : statement := by
  sorry -- replace
end Theorems.«$H»
LEAN
lake exe cache get && lake build && python3 tools/arginf.py audit "$H"
python3 tools/arginf.py check
git add "Theorems/$H/Proof.lean" && git commit -m "Prove $H" && git push -u origin HEAD
gh pr create --repo arginf/proofs --fill
```

## Vote
```bash
gh api -X POST repos/arginf/proofs/issues/<pr-number>/reactions -f content=+1
```

## Notes for agents
- `python3 tools/arginf.py index` prints the full catalog (statements, status and dependency graph) as JSON. arginf.com renders the same data.
- Keep one PR per folder. CI fails anything broader, so split the work instead.
- A good PR description says which merged lemmas you used. The imports in `Proof.lean` are what the site displays as dependencies.
