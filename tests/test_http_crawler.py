import unittest
from unittest.mock import Mock, patch

import http_crawler


class HttpCrawlerTests(unittest.TestCase):
    def test_goofish_parser_reads_public_json_ld_without_mojibake(self):
        html = '''
        <script type="application/ld+json">
        {"@type":"Product","name":"华硕 RTX 5080 台式显卡","url":"https://www.goofish.com/item?id=123456789012","offers":{"price":"7499"}}
        </script>
        '''
        items = http_crawler._parse_goofish(html)
        self.assertEqual([("华硕 RTX 5080 台式显卡", 7499.0, "https://www.goofish.com/item?id=123456789012")], items)

    def test_goofish_parser_reads_visible_anchor_price(self):
        html = '<a href="/item?id=123456789013" title="微星 RTX 5080 显卡">微星 RTX 5080 显卡 ¥8,099</a>'
        items = http_crawler._parse_goofish(html)
        self.assertEqual(1, len(items))
        self.assertEqual(8099.0, items[0][1])

    def test_verification_page_is_reported_without_bypass(self):
        response = Mock(status_code=200, text="请完成安全验证后继续", headers={})
        with patch("http_crawler._safe_get", return_value=response):
            self.assertEqual("blocked", http_crawler.HttpGoofish().fetch("RTX 5080"))

    def test_unimplemented_platform_is_explicit(self):
        self.assertEqual("not_implemented", http_crawler.HttpTaobao().fetch("RTX 5080"))

    def test_public_request_honors_robots_and_cooldown(self):
        session = Mock()
        with patch("http_crawler._robots_allowed", return_value=False), patch("http_crawler._respect_rate"):
            self.assertEqual("robots_blocked", http_crawler._safe_get("goofish", session, "https://www.goofish.com/search"))
        http_crawler._trip_circuit("goofish")
        with patch("http_crawler._robots_allowed", return_value=True):
            self.assertEqual("cooldown", http_crawler._safe_get("goofish", session, "https://www.goofish.com/search"))
        http_crawler.clear_cache()

    def test_public_request_reuses_conditional_validator(self):
        first = Mock(status_code=200, headers={"ETag": '"v1"'}, text="first")
        second = Mock(status_code=304, headers={}, text="")
        session = Mock()
        session.get.side_effect = [first, second]
        with patch("http_crawler._robots_allowed", return_value=True), patch("http_crawler._respect_rate"), patch.object(http_crawler, "CACHE_TTL", 0):
            self.assertIs(first, http_crawler._safe_get("jd", session, "https://search.jd.com/Search", params={"keyword": "RTX 5080"}))
            self.assertIs(first, http_crawler._safe_get("jd", session, "https://search.jd.com/Search", params={"keyword": "RTX 5080"}))
        headers = session.get.call_args_list[-1].kwargs["headers"]
        self.assertEqual('"v1"', headers["If-None-Match"])
        http_crawler.clear_cache()


if __name__ == "__main__":
    unittest.main()
