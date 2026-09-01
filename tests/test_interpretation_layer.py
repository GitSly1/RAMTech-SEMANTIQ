"""Regression tests for generic website relationship interpretation."""
import unittest

from interpretation_layer import (
    _url_role,
    interpret_website_snapshot,
    interpretation_summary,
)


class UrlRoleClassificationTests(unittest.TestCase):
    SOURCE = "https://www.example.test/catalog/items?sort=name"

    def test_relative_and_same_site_absolute_links_are_relationships(self):
        candidates = (
            "/catalog/items/42",
            "items/42",
            "../records/42?view=full",
            "https://example.test/catalog/items/42",
            "//www.example.test/catalog/items/42",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    "RELATIONSHIP",
                    _url_role(self.SOURCE, candidate, "View details"),
                )

    def test_relative_links_are_internal_even_without_a_source_host(self):
        self.assertEqual("RELATIONSHIP", _url_role("", "records/42", "Details"))
        self.assertEqual("RELATIONSHIP", _url_role("", "/records/42", "Details"))

    def test_pagination_is_recognized_from_query_path_and_label_evidence(self):
        cases = (
            ("?page=2", "2"),
            ("/catalog/page/3/", "Page 3"),
            ("/catalog?offset=50", "More results"),
            ("/catalog?cursor=next-token", "Load more"),
            ("/catalog", "Previous page"),
            ("/catalog", "Older posts"),
            ("/catalog", "»"),
        )
        for candidate, label in cases:
            with self.subTest(candidate=candidate, label=label):
                self.assertEqual(
                    "CONTINUATION",
                    _url_role(self.SOURCE, candidate, label),
                )

    def test_external_pagination_like_links_remain_external(self):
        self.assertEqual(
            "EXTERNAL",
            _url_role(
                self.SOURCE,
                "https://other.test/catalog?page=2",
                "Next page",
            ),
        )

    def test_downloadable_resources_are_distinguished_generically(self):
        cases = (
            ("/files/report.PDF?version=2", "Report"),
            ("/export?format=csv", "Export"),
            ("/reports/current?download=1", "Current report"),
            ("/downloads/archive", "Archive"),
            ("/reports/current", "Download report"),
        )
        for candidate, label in cases:
            with self.subTest(candidate=candidate, label=label):
                self.assertEqual(
                    "RESOURCE",
                    _url_role(self.SOURCE, candidate, label),
                )

    def test_media_links_remain_distinct_from_document_resources(self):
        cases = (
            "/media/photo.webp?size=large",
            "/asset?id=17&format=png",
            "https://cdn.other.test/video/clip.mp4",
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    "MEDIA",
                    _url_role(self.SOURCE, candidate, "View media"),
                )

    def test_navigation_external_and_detail_roles_remain_distinct(self):
        self.assertEqual("NAVIGATION", _url_role(self.SOURCE, "/", "Home"))
        self.assertEqual(
            "NAVIGATION",
            _url_role(self.SOURCE, "/privacy-policy", "Privacy policy"),
        )
        self.assertEqual(
            "EXTERNAL",
            _url_role(self.SOURCE, "https://other.test/records/42", "Details"),
        )
        self.assertEqual(
            "RELATIONSHIP",
            _url_role(self.SOURCE, "/records/42", "Details"),
        )

    def test_actions_empty_links_and_malformed_web_urls_are_not_relationships(self):
        self.assertEqual("IGNORE", _url_role(self.SOURCE, "", "Empty"))
        self.assertEqual("FIELD_OR_ACTION", _url_role(self.SOURCE, "#filters", "Filters"))
        self.assertEqual(
            "FIELD_OR_ACTION",
            _url_role(self.SOURCE, "mailto:help@example.test", "Email"),
        )
        self.assertEqual(
            "FIELD_OR_ACTION",
            _url_role(self.SOURCE, "javascript:void(0)", "Open"),
        )
        self.assertEqual("UNKNOWN", _url_role(self.SOURCE, "https:///missing-host", "Broken"))


class WebsiteInterpretationRegressionTests(unittest.TestCase):
    def test_interpretation_aggregates_roles_without_following_or_mutating_links(self):
        links = [
            ("Details", "/records/42"),
            ("Next page", "?page=2"),
            ("Report", "/files/report.pdf"),
            ("Photo", "/images/photo.jpg"),
            ("Privacy policy", "/privacy-policy"),
            ("Partner", "https://partner.test/record/9"),
        ]
        original_links = list(links)

        model = interpret_website_snapshot(
            source_url="https://example.test/records",
            title="Record index",
            element_counts={"article": 6, "li": 8},
            datasets=[{"name": "record collection", "count": 6}],
            links=links,
            dom_count=120,
        )

        self.assertEqual(original_links, links)
        self.assertEqual("OBSERVATION_ONLY", model["mode"])
        self.assertEqual("Collection → Detail relationship", model["page_model"])
        self.assertEqual(
            {
                "RELATIONSHIP": 1,
                "CONTINUATION": 1,
                "RESOURCE": 1,
                "MEDIA": 1,
                "NAVIGATION": 1,
                "EXTERNAL": 1,
            },
            model["relationship_roles"],
        )
        self.assertIn("Pagination / continuation", model["controls"])
        self.assertEqual(
            ["Document / file links", "Media links"],
            model["resources"],
        )

        summary = interpretation_summary(model)
        self.assertIn("OBSERVATION ONLY", summary)
        self.assertIn(
            "does not select, exclude, follow, rename, preview, or extract anything",
            summary,
        )

    def test_semantic_plan_observations_still_merge_with_url_roles(self):
        model = interpret_website_snapshot(
            source_url="https://example.test/list",
            links=[("Details", "/record/1")],
            semantic_plan={
                "relationship_roles": {
                    "DETAIL": 2,
                    "PAGINATION": 1,
                    "RESOURCE": 1,
                },
                "recommended_record_count": 3,
            },
        )

        self.assertEqual(3, model["relationship_roles"]["RELATIONSHIP"])
        self.assertEqual(1, model["relationship_roles"]["CONTINUATION"])
        self.assertEqual(3, model["primary_record_count"])
        self.assertIn("Pagination / continuation", model["controls"])
        self.assertIn("Document / file links", model["resources"])
        self.assertEqual("OBSERVATION_ONLY", model["mode"])


if __name__ == "__main__":
    unittest.main()
