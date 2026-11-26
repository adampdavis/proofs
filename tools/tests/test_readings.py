"""Machine readings: pinned model + runtime, fixed prompt, deterministic decoding."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from tools import arginf, readings

MODELS = """
[runtime]
name = "llama.cpp"
version = "b1"
url = "https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-ubuntu-x64.tar.gz"
sha256 = "{sha}"

[[model]]
name = "Tiny-Model"
license = "Apache-2.0"
url = "https://huggingface.co/x/y/resolve/0123/tiny.gguf"
sha256 = "{sha}"
template = "chatml"
"""
SHA = "a" * 64


class ModelsTests(unittest.TestCase):
    def _load(self, text: str) -> readings.Allowlist:
        f = Path(tempfile.mkdtemp()) / "models.toml"
        f.write_text(text)
        return readings.load_models(f)

    def test_loads_pinned_runtime_and_models(self) -> None:
        allow = self._load(MODELS.format(sha=SHA))
        self.assertEqual(allow.runtime.version, "b1")
        self.assertEqual(allow.model("Tiny-Model").template, "chatml")
        with self.assertRaises(readings.ReadingError):
            allow.model("Not-Allowlisted")

    def test_rejects_unpinned_or_insecure_entries(self) -> None:
        for bad in (
            MODELS.format(sha="abc"),
            MODELS.format(sha=SHA).replace("https://huggingface.co", "http://huggingface.co"),
            MODELS.format(sha=SHA).replace("https://huggingface.co", "https://evil.example"),
            MODELS.format(sha=SHA).replace('template = "chatml"', 'template = "raw"'),
        ):
            with self.assertRaises(readings.ReadingError):
                self._load(bad)

    def test_repo_models_toml_is_valid(self) -> None:
        allow = readings.load_models(Path(__file__).resolve().parents[2] / "models.toml")
        self.assertTrue(allow.models)


class PromptTests(unittest.TestCase):
    def test_chatml_has_no_system_message(self) -> None:
        p = readings.chat_prompt("chatml", "Hello")
        self.assertEqual(p, "<|im_start|>user\nHello<|im_end|>\n<|im_start|>assistant\n")
        self.assertNotIn("system", p)

    def test_user_message_is_the_fixed_translation_prompt(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "Theorems").mkdir()
        h = arginf.new_theorem(root, "∀ n : Nat, n + 0 = n", imports=("Init",))
        msg = readings.user_message(root, h)
        self.assertTrue(
            msg.startswith("Translate the following Lean statement into natural language:\n```\n")
        )
        self.assertIn("∀ n : Nat, n + 0 = n", msg)


class DecodingTests(unittest.TestCase):
    def test_args_are_deterministic(self) -> None:
        args = readings.generation_args(Path("bin/llama-completion"), Path("m.gguf"), Path("p.txt"))
        joined = " ".join(args)
        for flag in (
            "--temp 0",
            "--top-k 1",
            "--seed 0",
            "--threads 4",
            "-n 512",
            "-f p.txt",
            "-m m.gguf",
        ):
            self.assertIn(flag, joined)

    def test_clean_output_strips_chat_tokens(self) -> None:
        self.assertEqual(readings.clean_output("  Every n.<|im_end|>\n"), "Every n.")
        self.assertEqual(readings.clean_output("Every n. [end of text]\n"), "Every n.")
        self.assertEqual(readings.clean_output("Every n.\n\n> EOF by user\n"), "Every n.")

    def test_sha256_check(self) -> None:
        f = Path(tempfile.mkdtemp()) / "blob"
        f.write_bytes(b"data")
        readings.check_sha256(f, hashlib.sha256(b"data").hexdigest())
        with self.assertRaises(readings.ReadingError):
            readings.check_sha256(f, SHA)


if __name__ == "__main__":
    unittest.main()
