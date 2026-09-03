import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import semantiq
from semantiq.identity import get_product_identity, get_recovery_probe_metadata


class ProductIdentityTests(unittest.TestCase):
    def test_product_name(self):
        self.assertEqual(semantiq.PRODUCT_NAME, "SEMANTIQ")
        self.assertEqual(get_product_identity().name, "SEMANTIQ")

    def test_version_is_semantic(self):
        self.assertRegex(semantiq.__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(get_product_identity().version, semantiq.__version__)

    def test_structured_identity_metadata(self):
        self.assertEqual(
            semantiq.get_identity_metadata(),
            {"name": semantiq.PRODUCT_NAME, "version": semantiq.__version__},
        )

    def test_identity_metadata_is_returned_independently(self):
        metadata = semantiq.get_identity_metadata()
        metadata["name"] = "changed"
        self.assertEqual(semantiq.get_identity_metadata()["name"], "SEMANTIQ")

    def test_recovery_probe_metadata_exact_value(self):
        self.assertEqual(
            get_recovery_probe_metadata(),
            {
                "component": "semantiq",
                "purpose": "rvsc_interrupted_mission_recovery_proof",
                "schema_version": "1",
            },
        )

    def test_recovery_probe_metadata_is_deterministic_and_independent(self):
        first = get_recovery_probe_metadata()
        second = get_recovery_probe_metadata()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_public_exports(self):
        expected = {
            "PRODUCT_NAME",
            "ProductIdentity",
            "__version__",
            "get_identity_metadata",
            "get_product_identity",
        }
        self.assertEqual(set(semantiq.__all__), expected)
        for name in expected:
            self.assertTrue(hasattr(semantiq, name), name)


if __name__ == "__main__":
    unittest.main()
