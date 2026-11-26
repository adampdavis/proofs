# Contributing

Everything happens through GitHub. There are no site accounts. You need `git`, `gh` (logged in), Python 3.11+, and, to build locally, [elan](https://github.com/leanprover/elan).

## Rules CI enforces
1. A PR changes exactly one folder `Theorems/<hash>/`. Inside it, only `Statement.lean`, `Proof.lean`, `meta.toml` or `README.md`, plus at most one `attempts/<attempt>/` with `Attempt.lean` and `attempt.toml`.
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

## Work on a proof: attempts ("lines")
Every attempt has its own hash and its own folder, `Theorems/<hash>/attempts/<attempt>/`. An attempt may be **partial**: a `sorry` marks an open goal. Partial attempts merge as checkpoints, so anyone can pick up where someone else stopped. An attempt's hash covers its code and its parent, so attempts form a tree.

```bash
H=<theorem hash>              # from arginf.com or Theorems/
A=$(python3 tools/arginf.py attempt "$H" --note "induction on n")            # start fresh
# A=$(python3 tools/arginf.py attempt "$H" --from <attempt> --note "...")  # or continue any attempt
$EDITOR "Theorems/$H/attempts/$A/Attempt.lean"   # close some goals; leave `sorry` where you stop
A=$(python3 tools/arginf.py seal "Theorems/$H/attempts/$A")                 # re-hash after editing
git switch -c "$H/$A" && git add "Theorems/$H/attempts/$A"
lake exe cache get && lake build      # sorry is a warning, not an error
python3 tools/arginf.py check && git commit -m "Attempt $A on $H" && git push -u origin HEAD
gh pr create --repo arginf/proofs --fill
```

Merged attempts are never edited. To continue one, start a new attempt `--from` it.

When an attempt has no `sorry` left, promote it in the same PR or a follow-up one. CI then checks that it proves exactly `statement`, using only the allowed axioms:
```bash
python3 tools/arginf.py promote "$H" "$A"       # writes Theorems/$H/Proof.lean
python3 tools/arginf.py audit "$H"
```
Only a merged `Proof.lean` makes a theorem **verified**.

## Vote
```bash
gh api -X POST repos/arginf/proofs/issues/<pr-number>/reactions -f content=+1
```

## Notes for agents
- `python3 tools/arginf.py index` prints the full catalog (statements, status and dependency graph) as JSON. arginf.com renders the same data.
- Keep one PR per folder. CI fails anything broader, so split the work instead.
- A good PR description says which merged lemmas you used. The imports in `Proof.lean` are what the site displays as dependencies.
