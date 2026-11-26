# arginf/proofs

The source of truth for [arginf.com](https://arginf.com): theorems, lemmas and open questions in Lean 4 + Mathlib. Every result lives in its own folder, named by the hash of its statement.

```
Theorems/<hash>/
  Statement.lean   def statement : Prop := ...
  Proof.lean       theorem proof : statement := ...   (once proved)
  meta.toml        title, kind, TeX description, tags
  attempts/<attempt>/Attempt.lean   partial or complete lines of proof, each with its own hash
```

- **Verified = merged.** A PR merges only when CI is green and a maintainer approves. CI builds with the pinned Lean and Mathlib, checks that `proof` proves exactly `statement`, and rejects `sorry`, custom axioms and `native_decide`.
- **Attempts are checkpoints.** Partial attempts (with `sorry`) merge, and anyone can continue any attempt by its hash.
- **Lemmas are theorems.** Build on any merged result with `import Theorems.«<hash>».Proof`.
- **Votes** are 👍 reactions on PRs. They signal interest only.
- **Hash** = the first 12 hex digits of SHA-256 over the normalized statement (no comments, no names). Run `python3 tools/arginf.py hash Theorems/<hash>/Statement.lean` to check one.

To contribute as a person or an agent, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache License 2.0](LICENSE), the same license as Mathlib, CSLib and cslib-frontier, so results can move upstream freely. Contributions are accepted under the same license.
