import gc
import os
import tempfile
import unittest

import database as db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_path
        gc.collect()
        self.temp_dir.cleanup()

    def test_distinct_items_keeps_separate_rows_without_urls(self):
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品 A", 8000, "")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品 B", 8100, "")
        self.assertEqual(2, len(db.distinct_items()))

    def test_latest_prices_uses_refresh_time_not_largest_id(self):
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品 A", 8000, "https://a")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品 B", 8100, "https://b")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品 A 已更新", 7900, "https://a")
        latest = db.latest_prices()
        self.assertEqual(1, len(latest))
        self.assertEqual("商品 A 已更新", latest[0]["title"])

    def test_clear_price_data_clears_current_and_history_but_keeps_state(self):
        db.set_state("keep_me", "yes")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品", 8000, "https://a")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品", 7900, "https://a")
        db.clear_price_data()
        self.assertEqual([], db.all_prices())
        self.assertEqual([], db.history("RTX 5080"))
        self.assertEqual("yes", db.get_state("keep_me"))

    def test_connections_are_closed_after_reads(self):
        db.get_state("missing")
        db.all_prices()
        replacement = os.path.join(self.temp_dir.name, "renamed.db")
        os.replace(db.DB_PATH, replacement)
        os.replace(replacement, db.DB_PATH)

    def test_history_keeps_one_latest_snapshot_per_item_per_day(self):
        db.add_price("RTX 5090", "RTX 50 系", 50, "闲鱼", "商品", 15000, "https://item")
        db.add_price("RTX 5090", "RTX 50 系", 50, "闲鱼", "商品降价", 14500, "https://item")
        rows = db.history("RTX 5090", "闲鱼")
        self.assertEqual(1, len(rows))
        self.assertEqual(14500, rows[0]["price"])
        self.assertEqual("商品降价", rows[0]["title"])


if __name__ == "__main__":
    unittest.main()
