import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import semantiq
from semantiq.identity import SemanticVersion, get_product_identity


class ProductIdentityTests(unittest.TestCase):
    def test_product_name(self):
        self.assertEqual(semantiq.PRODUCT_NAME, "SEMANTIQ")
        self.assertEqual(get_product_identity().name, "SEMANTIQ")

    def test_version_is_semantic(self):
        parsed = SemanticVersion.parse(semantiq.__version__)
        self.assertEqual(parsed, SemanticVersion(0, 1, 0))
        self.assertEqual(get_product_identity().version, semantiq.__version__)

    def test_public_exports(self):
        expected = {
            "PRODUCT_NAME",
            "ProductIdentity",
            "SemanticVersion",
            "__version__",
            "get_product_identity",
        }
        self.assertEqual(set(semantiq.__all__), expected)
        for name in expected:
            self.assertTrue(hasattr(semantiq, name), name)


class SemanticVersionTests(unittest.TestCase):
    def test_parse_canonical_version(self):
        version = SemanticVersion.parse("12.34.56")
        self.assertEqual(version.major, 12)
        self.assertEqual(version.minor, 34)
        self.assertEqual(version.patch, 56)
        self.assertEqual(str(version), "12.34.56")

    def test_reject_non_canonical_versions(self):
        invalid_versions = (
            "",
            "1",
            "1.2",
            "1.2.3.4",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.-3",
            "1.2.3-alpha",
            "v1.2.3",
            " 1.2.3",
            "1.2.3 ",
            "１.２.３",
        )
        for value in invalid_versions:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SemanticVersion.parse(value)

    def test_parse_requires_string(self):
        with self.assertRaises(TypeError):
            SemanticVersion.parse(123)

    def test_components_must_be_non_negative_integers(self):
        invalid_components = (
            (-1, 0, 0),
            (0, -1, 0),
            (0, 0, -1),
            (True, 0, 0),
            (0, 1.0, 0),
        )
        for components in invalid_components:
            with self.subTest(components=components):
                with self.assertRaises(ValueError):
                    SemanticVersion(*components)

    def test_versions_are_orderable(self):
        versions = [
            SemanticVersion(2, 0, 0),
            SemanticVersion(1, 10, 0),
            SemanticVersion(1, 2, 4),
            SemanticVersion(1, 2, 3),
        ]
        self.assertEqual(
            sorted(versions),
            [
                SemanticVersion(1, 2, 3),
                SemanticVersion(1, 2, 4),
                SemanticVersion(1, 10, 0),
                SemanticVersion(2, 0, 0),
            ],
        )

    def test_versions_are_immutable(self):
        version = SemanticVersion(1, 2, 3)
        with self.assertRaises(AttributeError):
            version.patch = 4


if __name__ == "__main__":
    unittest.main()
