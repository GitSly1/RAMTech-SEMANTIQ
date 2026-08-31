import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import semantiq
from semantiq.identity import get_product_identity, parse_semantic_version


class ProductIdentityTests(unittest.TestCase):
    def test_product_name(self):
        self.assertEqual(semantiq.PRODUCT_NAME, "SEMANTIQ")
        self.assertEqual(get_product_identity().name, "SEMANTIQ")

    def test_version_is_semantic(self):
        self.assertRegex(semantiq.__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(get_product_identity().version, semantiq.__version__)

    def test_semantic_version_parser(self):
        parsed = parse_semantic_version("12.3.45")
        self.assertEqual((parsed.major, parsed.minor, parsed.patch), (12, 3, 45))
        self.assertLess(parse_semantic_version("1.9.9"), parse_semantic_version("2.0.0"))

    def test_semantic_version_parser_rejects_invalid_values(self):
        for value in ("1.2", "v1.2.3", "1.2.3-beta", "01.2.3", "1.02.3", "1.2.03", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_semantic_version(value)

    def test_public_exports(self):
        expected = {
            "PRODUCT_NAME",
            "ProductIdentity",
            "SemanticVersion",
            "__version__",
            "get_product_identity",
            "parse_semantic_version",
        }
        self.assertEqual(set(semantiq.__all__), expected)
        for name in expected:
            self.assertTrue(hasattr(semantiq, name), name)


if __name__ == "__main__":
    unittest.main()
