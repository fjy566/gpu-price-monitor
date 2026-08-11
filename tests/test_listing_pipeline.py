import unittest

from listing_pipeline import FilterPolicy, ListingFilter


class ListingPipelineTests(unittest.TestCase):
    def test_pipeline_is_pure_and_reports_rejection_categories(self):
        pipeline = ListingFilter(FilterPolicy(500, 0.55, 3.0, True))
        items = [
            ("RTX5090 32G 自用显卡", 20000, "ok"),
            ("RTX5090 水冷头单出", 800, "accessory"),
            ("RTX5090 Laptop Mobile", 19000, "mobile"),
            ("RTX5080 16G 显卡", 9000, "wrong-model"),
        ]
        kept, stats = pipeline.filter("RTX 5090", items)
        self.assertEqual([("RTX5090 32G 自用显卡", 20000.0, "ok")], kept)
        self.assertEqual(1, stats.content)
        self.assertEqual(1, stats.mobile)
        self.assertEqual(1, stats.kept)


if __name__ == "__main__":
    unittest.main()
