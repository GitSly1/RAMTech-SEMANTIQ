import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import semantiq
from semantiq.identity import get_product_identity


class ProductIdentityTests(unittest.TestCase):
    def test_product_name(self):
        self.assertEqual(semantiq.PRODUCT_NAME, "SEMANTIQ")
        self.assertEqual(get_product_identity().name, "SEMANTIQ")

    def test_version_is_semantic(self):
        self.assertRegex(semantiq.__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(get_product_identity().version, semantiq.__version__)

    def test_public_exports(self):
        expected = {"PRODUCT_NAME", "ProductIdentity", "__version__", "get_product_identity"}
        self.assertEqual(set(semantiq.__all__), expected)
        for name in expected:
            self.assertTrue(hasattr(semantiq, name), name)


if __name__ == "__main__":
    unittest.main()
