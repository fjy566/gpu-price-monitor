import os
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

import crawler
import database as db


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_parse_price_prefers_displayed_price(self):
        self.assertEqual(4999.0, crawler._parse_price("¥4999 12期 月供416"))
        self.assertEqual(4999.0, crawler._parse_price("4999 领券减300"))

    def test_goofish_price_parser_expands_line_broken_wan_value(self):
        text = "\u00a5\n3\n.26\n\u4e07\n\u00a549000"
        self.assertAlmostEqual(32600.0, crawler._parse_goofish_price_text(text))

    def test_goofish_card_fallback_handles_generated_classes(self):
        class Element:
            def __init__(self, text=""):
                self.text = text

            def inner_text(self):
                return self.text

            def query_selector(self, _selector):
                return None

            def get_attribute(self, _name):
                return None

        class Card(Element):
            def query_selector(self, selector):
                if "row3-wrap-price" in selector:
                    return Element("¥ 12999")
                return None

            def get_attribute(self, name):
                return "https://www.goofish.com/item?id=5090" if name == "href" else None

        card = Card("RTX 5090 Gaming OC\n¥ 12999\n全新")
        self.assertEqual("RTX 5090 Gaming OC", crawler._goofish_card_title(card, "RTX5090"))
        self.assertEqual(12999.0, crawler._goofish_card_price(card, card.inner_text()))

    def test_invalid_settings_are_rejected(self):
        for payload in (
            {"crawl_mode": "unknown"},
            {"crawl_mode": "http"},
            {"crawl_mode": "api"},
            {"selected_platforms": "jd"},
            {"abs_min": "nan"},
            {"low_ratio": "1.5"},
            {"high_ratio": "0.5"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    crawler.save_settings(payload)

    def test_pause_and_resume_do_not_create_a_fake_running_state(self):
        worker = crawler.Crawler()
        worker.pause()
        self.assertEqual("stopped", worker.status)
        worker.resume()
        self.assertEqual("stopped", worker.status)

    def test_browser_start_error_releases_running_flag(self):
        worker = crawler.Crawler()
        worker._running = True
        with (
            patch("crawler.get_settings", return_value={"crawl_mode": "browser"}),
            patch.object(type(crawler.manager), "is_ready", new_callable=PropertyMock, return_value=False),
            patch.object(crawler.manager, "start_browser", return_value=(False, {"msg": "启动失败"})),
            patch.object(crawler.manager, "close"),
            patch("crawler.db.set_state"),
        ):
            worker._loop()
        self.assertFalse(worker._running)
        self.assertEqual("error", worker.status)

    def test_each_start_runs_exactly_one_round_and_closes_browser(self):
        worker = crawler.Crawler()
        worker._running = True
        with (
            patch.object(type(crawler.manager), "is_ready", new_callable=PropertyMock, return_value=True),
            patch.object(worker, "_one_round") as one_round,
            patch.object(crawler.manager, "close") as close_browser,
            patch("crawler.db.set_state"),
        ):
            worker._loop()
        one_round.assert_called_once_with()
        close_browser.assert_called_once_with()
        self.assertEqual(1, worker.rounds)
        self.assertEqual("completed", worker.status)

    def test_streamed_items_are_success_even_if_full_result_times_out(self):
        worker = crawler.Crawler()
        worker._running = True
        worker._pause_event.set()
        model = {"name": "RTX 5090", "series": "RTX 50 系", "generation": 50}
        pc = next(p for p in worker.platforms if p.name == "goofish")

        def partial(_pc, _kw, _transport, on_batch=None):
            on_batch([("RTX5090 32G 自用显卡", 16000, "https://item")])
            worker._last_transport_note = "浏览器操作超时"
            return []

        with (
            patch.object(worker, "_collect_via", side_effect=partial),
            patch.object(worker, "_transport_order", return_value=["browser"]),
            patch.object(worker, "_log") as log,
            patch("crawler.random.uniform", return_value=0),
        ):
            worker._fetch_model(pc, model)
        self.assertEqual(1, len(db.all_prices()))
        self.assertFalse(any("无数据" in call.args[0] for call in log.call_args_list))

    def test_custom_model_metadata_is_preserved_when_storing(self):
        worker = crawler.Crawler()
        worker._running = True
        worker._pause_event.set()
        model = {"name": "RX 7900 XTX", "series": "RX 7000 系", "generation": 70}
        settings = dict(crawler.DEFAULT_SETTINGS)
        settings.update({"crawl_mode": "http", "abs_min": "500"})
        pc = next(p for p in worker.platforms if p.name == "jd")
        with (
            patch("crawler.get_settings", return_value=settings),
            patch.object(worker, "_collect_via", return_value=[("蓝宝石 RX 7900 XTX 24G", 6000, "https://item")]),
            patch.object(worker, "_transport_order", return_value=["http"]),
            patch("crawler.db.add_price") as add_price,
            patch("crawler.db.set_state"),
            patch("crawler.time.sleep"),
        ):
            worker._fetch_model(pc, model)
        add_price.assert_called_once()
        args = add_price.call_args.args
        self.assertEqual(("RX 7900 XTX", "RX 7000 系", 70), args[:3])


if __name__ == "__main__":
    unittest.main()
