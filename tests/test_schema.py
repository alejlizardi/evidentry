"""Keep schema/results.schema.json honest: every committed example pack must
validate against it. If the run output and the contract drift apart, this
fails — which is the whole point of having a contract for the frontend.

Skips when jsonschema isn't installed (it is a dev-only extra; the library
and its core tests stay pure-stdlib). Run `pip install -e .[dev]` to enable.
"""

import json
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator

    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover - exercised only without the dev extra
    HAVE_JSONSCHEMA = False

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ROOT / "schema" / "results.schema.json"


def _example_packs() -> list[Path]:
    return sorted((ROOT / "examples").glob("**/evidence/*/results.json"))


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed (pip install -e .[dev])")
class TestResultsSchema(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    def test_example_packs_validate(self):
        packs = _example_packs()
        self.assertTrue(packs, "expected committed example packs to validate against")
        for results_path in packs:
            data = json.loads(results_path.read_text(encoding="utf-8"))
            errors = sorted(self.validator.iter_errors(data), key=lambda e: list(e.path))
            self.assertEqual(
                errors,
                [],
                f"{results_path.relative_to(ROOT)} violates the schema: "
                + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5]),
            )


if __name__ == "__main__":
    unittest.main()
