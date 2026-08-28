import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "baseline"


class BaselineDocumentationTests(unittest.TestCase):
    def test_required_documents_exist(self):
        for name in ("REPOSITORY_INVENTORY.md", "LEGACY_CAPABILITY_MAP.md", "MIGRATION_GAPS.md"):
            self.assertTrue((DOCS / name).is_file(), name)

    def test_evidence_vocabulary_is_explicit(self):
        combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS.glob("*.md"))
        for state in ("VERIFIED-IN-REPO", "KNOWN-LEGACY-NOT-IMPORTED", "PLANNED"):
            self.assertIn(state, combined)

    def test_inventory_lists_dispatch_top_level_components(self):
        text = (DOCS / "REPOSITORY_INVENTORY.md").read_text(encoding="utf-8")
        for component in (".rvsc/", "README.md", "pyproject.toml", "src/", "tests/"):
            self.assertIn(component, text)

    def test_migration_gap_requires_authoritative_baseline(self):
        text = (DOCS / "MIGRATION_GAPS.md").read_text(encoding="utf-8").lower()
        self.assertIn("authoritative legacy", text)
        self.assertIn("bounded work packages", text)


if __name__ == "__main__":
    unittest.main()
