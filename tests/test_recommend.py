import unittest
from types import SimpleNamespace

import recommend


def row(row_id, price, url):
    return {
        "id": row_id,
        "model": "RTX 5080",
        "series": "RTX 50 系",
        "platform": "闲鱼",
        "price": price,
        "title": f"RTX 5080 商品 {row_id}",
        "url": url,
        "ts": "2026-08-10 12:00:00",
    }


class RecommendationTests(unittest.TestCase):
    @staticmethod
    def snapshot(rows):
        grouped = {}
        for item in rows:
            grouped.setdefault(item["model"], []).append(item["price"])
        return SimpleNamespace(rows=tuple(rows), model_prices={k: tuple(v) for k, v in grouped.items()})

    def test_extreme_low_outlier_is_not_recommended(self):
        rows = [row(1, 500, "a"), row(2, 3000, "b"), row(3, 3100, "c"), row(4, 3200, "d")]
        recs = recommend._compute_recommendations(limit=5, snapshot=self.snapshot(rows))
        self.assertTrue(recs)
        self.assertNotIn(500, [item["price"] for item in recs])

    def test_single_sample_does_not_claim_to_be_a_deal(self):
        rows = [row(1, 3000, "a")]
        recs = recommend._compute_recommendations(limit=5, snapshot=self.snapshot(rows))
        self.assertEqual([], recs)


if __name__ == "__main__":
    unittest.main()
