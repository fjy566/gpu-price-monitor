import csv
import gc
import io
import os
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

import api_panel
import app as app_module
import database as db
import crawler as crawler_module


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

    def test_remote_client_is_rejected_by_default(self):
        response = self.client.get("/api/status", environ_base={"REMOTE_ADDR": "192.0.2.10"})
        self.assertEqual(403, response.status_code)

    def test_api_responses_disable_caching_and_clickjacking(self):
        response = self.client.get("/api/status")
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("DENY", response.headers["X-Frame-Options"])
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])

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
        payload = response.get_json()
        self.assertEqual(["goofish"], [item["platform"] for item in payload["login_state"]])
        self.assertIn("readiness", payload)
        self.assertIn("run_summary", payload)

    def test_invalid_or_hidden_model_cannot_be_saved(self):
        response = self.client.post("/api/settings", json={"selected_models": "RTX 9999"})
        self.assertEqual(400, response.status_code)
        self.assertIn("型号不存在", response.get_json()["msg"])

    def test_history_rejects_control_characters(self):
        response = self.client.get("/api/history?model=RTX%205090%0Aother")
        self.assertEqual(400, response.status_code)

    def test_browser_mode_change_does_not_start_an_idle_browser(self):
        with (
            patch.object(type(crawler_module.manager), "is_ready", new_callable=PropertyMock, return_value=False),
            patch.object(crawler_module.manager, "reboot") as reboot,
        ):
            response = self.client.post("/api/browser/mode", json={"mode": "visible"})
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()["restarting"])
        reboot.assert_not_called()

    def test_debug_tools_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GPU_MONITOR_DEBUG_TOOLS", None)
            response = self.client.post("/api/debug/fetch", json={"keyword": "RTX5090"})
        self.assertEqual(404, response.status_code)

    def test_prices_revision_avoids_resending_unchanged_catalog(self):
        db.add_price("RTX 5090", "RTX 50 系", 50, "闲鱼", "商品", 15000, "https://item")
        first = self.client.get("/api/prices").get_json()
        self.assertEqual(1, len(first["data"]))
        second = self.client.get(f"/api/prices?since={first['revision']}").get_json()
        self.assertTrue(second["unchanged"])
        self.assertNotIn("data", second)

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
