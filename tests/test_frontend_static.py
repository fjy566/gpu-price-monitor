from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendStaticTests(unittest.TestCase):
    def test_dynamic_content_does_not_use_html_sinks(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for unsafe_sink in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
            self.assertNotIn(unsafe_sink, script)

    def test_template_has_no_inline_event_handlers(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE))

    def test_scope_only_exposes_browser_and_goofish(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("模拟浏览器", html)
        self.assertIn("当前采集平台：闲鱼", html)
        self.assertNotIn("常规 HTTP", html)
        self.assertNotIn("官方 API", html)
        self.assertNotIn("jd", script)
        self.assertNotIn("taobao", script)
        self.assertNotIn("pdd", script)
        self.assertNotIn("cfg-crawl_mode", html)


if __name__ == "__main__":
    unittest.main()
