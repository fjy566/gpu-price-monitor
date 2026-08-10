import csv
import gc
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import api_panel
import app as app_module
import database as db


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        db.init_db()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        db.DB_PATH = self.old_path
        gc.collect()
        self.temp_dir.cleanup()

    def test_api_config_never_returns_plaintext_secrets(self):
        response = self.client.post("/api/api_config", json={
            "jd_appkey": "public-key",
            "jd_secret": "super-secret",
        })
        self.assertEqual(200, response.status_code)
        self.assertNotIn("super-secret", response.get_data(as_text=True))
        response = self.client.get("/api/api_config")
        self.assertNotIn("super-secret", response.get_data(as_text=True))

    def test_blank_secret_update_preserves_saved_secret(self):
        api_panel.save_api_config({"jd_appkey": "key", "jd_secret": "secret"})
        self.client.post("/api/api_config", json={"jd_secret": ""})
        self.assertEqual("secret", db.get_state("api_jd_secret"))

    def test_invalid_settings_return_400(self):
        response = self.client.post("/api/settings", json={"low_ratio": 2})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])

    def test_cross_origin_mutation_is_rejected(self):
        response = self.client.post(
            "/api/settings",
            json={"absolute_min": 100},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(403, response.status_code)
        self.assertFalse(response.get_json()["ok"])

    def test_clear_removes_prices_and_history(self):
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "商品", 8000, "https://a")
        response = self.client.post("/api/clear")
        self.assertEqual(200, response.status_code)
        self.assertEqual([], db.all_prices())
        self.assertEqual([], db.history("RTX 5080"))

    def test_empty_trend_model_is_rejected(self):
        response = self.client.get("/api/trend")
        self.assertEqual(400, response.status_code)

    def test_only_browser_mode_can_start_crawl(self):
        response = self.client.post("/api/settings", json={"crawl_mode": "http"})
        self.assertEqual(400, response.status_code)
        with patch.object(app_module.crawler, "start", return_value=True):
            response = self.client.post("/api/control/start_crawl")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual("browser", response.get_json()["mode"])

    def test_status_exposes_only_goofish(self):
        response = self.client.get("/api/status")
        self.assertEqual(["goofish"], [item["platform"] for item in response.get_json()["login_state"]])

    def test_price_views_hide_legacy_platform_rows(self):
        db.add_price("RTX 5080", "RTX 50 系", 50, "京东", "旧平台商品", 8000, "https://jd.example")
        db.add_price("RTX 5080", "RTX 50 系", 50, "闲鱼", "闲鱼商品", 8200, "https://goofish.example")
        prices = self.client.get("/api/prices").get_json()["data"]
        self.assertEqual(["闲鱼"], [row["platform"] for row in prices])
        stats = self.client.get("/api/stats").get_json()
        self.assertEqual({"闲鱼": 1}, stats["platform_dist"])

    def test_builtin_model_can_be_hidden_and_restored(self):
        hidden = self.client.patch("/api/models", json={"name": "RTX 5090", "action": "hide"})
        self.assertEqual(200, hidden.status_code)
        visible = self.client.get("/api/models").get_json()
        self.assertNotIn("RTX 5090", {item["name"] for item in visible["models"]})
        self.assertIn("RTX 5090", {item["name"] for item in visible["hidden_models"]})

        restored = self.client.patch("/api/models", json={"name": "rtx 5090", "action": "restore"})
        self.assertEqual(200, restored.status_code)
        visible = self.client.get("/api/models").get_json()
        self.assertIn("RTX 5090", {item["name"] for item in visible["models"]})
        self.assertNotIn("RTX 5090", {item["name"] for item in visible["hidden_models"]})

    def test_custom_model_can_be_deleted_case_insensitively(self):
        self.assertEqual(200, self.client.post("/api/models", json={"name": "RX 7900 XTX"}).status_code)
        response = self.client.delete("/api/models", json={"name": "rx 7900 xtx"})
        self.assertEqual(200, response.status_code)
        names = {item["name"] for item in self.client.get("/api/models").get_json()["models"]}
        self.assertNotIn("RX 7900 XTX", names)

    def test_export_neutralizes_spreadsheet_formulas(self):
        db.add_price(
            "RTX 5080", "RTX 50 系", 50, "闲鱼",
            "=HYPERLINK(\"https://evil\")", 8000, "https://safe.example/item",
        )
        response = self.client.get("/api/export")
        text = response.get_data(as_text=True).lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertTrue(rows[1][4].startswith("'="))
        self.assertEqual("text/csv; charset=utf-8", response.content_type)


if __name__ == "__main__":
    unittest.main()
