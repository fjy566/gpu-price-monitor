# -*- coding: utf-8 -*-
"""
常规 HTTP 爬虫（requests 版）——免浏览器的公开页面采集。

合规边界：
- 只访问平台公开页面，不调用未公开的内部接口；
- 不做验证码/人机校验绕过、指纹伪装、代理轮换或风控规避；
- 使用复用 Session、连接池、缓存、限速和指数退避减少重复请求；
- 收到 401/403/429 或访问验证页面时立即停止当前平台并返回状态，交给上层提示。

闲鱼页面经常是前端壳页面，只有服务器返回公开商品 HTML/JSON-LD 时才能被 requests
模式解析；如果页面需要登录或由 JavaScript 二次加载商品，应该切换浏览器/官方 API。
"""

import html as html_lib
import json
import random
import re
import threading
import time
from urllib.parse import parse_qs, urljoin, urlparse
from urllib import robotparser

import requests


# 请求参数：足够快，但不连续轰击同一主机。
MIN_DELAY = 0.75
MAX_DELAY = 1.40
CACHE_TTL = 20.0
ROBOTS_TTL = 3600.0
COOLDOWN_SECONDS = 120.0
MAX_ITEMS = 30
_USER_AGENT = "gpu-price-monitor/1.0 (public-page collector; local use)"

_SESSIONS = {}
_CACHE = {}
_VALIDATORS = {}
_ROBOTS = {}
_LAST_REQUEST = {}
_COOLDOWN = {}
_HTTP_LOCK = threading.RLock()


def _session(platform):
    """按平台复用连接池；不伪造浏览器指纹。"""
    with _HTTP_LOCK:
        session = _SESSIONS.get(platform)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": _USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            })
            _SESSIONS[platform] = session
        return session


def _cache_key(platform, url, params):
    pairs = sorted((str(key), str(value)) for key, value in (params or {}).items())
    return platform, url, tuple(pairs)


def _cached(key):
    with _HTTP_LOCK:
        value = _CACHE.get(key)
        if not value:
            return None
        created, response = value
        if time.monotonic() - created >= CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return response


def _remember(key, response):
    with _HTTP_LOCK:
        _CACHE[key] = (time.monotonic(), response)


def clear_cache():
    """测试或用户切换关键词后清理本地短缓存。"""
    with _HTTP_LOCK:
        _CACHE.clear()
        _VALIDATORS.clear()
        _ROBOTS.clear()
        _COOLDOWN.clear()


def _trip_circuit(platform):
    with _HTTP_LOCK:
        _COOLDOWN[platform] = time.monotonic() + COOLDOWN_SECONDS


def _cooldown(platform):
    with _HTTP_LOCK:
        until = _COOLDOWN.get(platform, 0.0)
        if until <= time.monotonic():
            _COOLDOWN.pop(platform, None)
            return False
        return True


def _robots_allowed(platform, session, url):
    """遵守公开 robots.txt；robots 暂时不可读时不伪造拒绝结果。"""
    parsed = urlparse(url)
    host = parsed.netloc
    now = time.monotonic()
    with _HTTP_LOCK:
        cached = _ROBOTS.get(host)
    if cached and now - cached[0] < ROBOTS_TTL:
        return cached[1].can_fetch(_USER_AGENT, url)
    robots_url = f"{parsed.scheme}://{host}/robots.txt"
    try:
        _respect_rate(host)
        response = session.get(robots_url, timeout=10)
        if response.status_code != 200:
            return True
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        with _HTTP_LOCK:
            _ROBOTS[host] = (now, parser)
        return parser.can_fetch(_USER_AGENT, url)
    except requests.RequestException:
        return True


def _respect_rate(host):
    with _HTTP_LOCK:
        previous = _LAST_REQUEST.get(host, 0.0)
        wait = max(0.0, previous + random.uniform(MIN_DELAY, MAX_DELAY) - time.monotonic())
        _LAST_REQUEST[host] = time.monotonic() + wait
    if wait:
        time.sleep(wait)


def _safe_get(platform, session, url, params=None, timeout=18, cookies=None, referer=None):
    """公开 GET：连接复用、限速、瞬时错误退避；不重试权限/验证错误。"""
    if _cooldown(platform):
        return "cooldown"
    if not _robots_allowed(platform, session, url):
        return "robots_blocked"
    key = _cache_key(platform, url, params)
    with _HTTP_LOCK:
        stale = _CACHE.get(key)
    if not cookies:
        cached = _cached(key)
        if cached is not None:
            return cached
    host = urlparse(url).netloc
    headers = {"Referer": referer} if referer else {}
    with _HTTP_LOCK:
        validators = dict(_VALIDATORS.get(key, {}))
    headers.update(validators)
    for attempt in range(3):
        _respect_rate(host)
        try:
            response = session.get(url, params=params, timeout=timeout, cookies=cookies, headers=headers)
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(1.0 * (2 ** attempt))
            continue
        if response.status_code == 304 and stale:
            return stale[1]
        if response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < 2:
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(12.0, max(1.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = min(12.0, 1.5 * (2 ** attempt) + random.uniform(0.1, 0.6))
            time.sleep(delay)
            continue
        if response.status_code in {401, 403, 429}:
            _trip_circuit(platform)
            return response
        if response.status_code == 200 and not cookies:
            _remember(key, response)
            # Response headers use ETag / Last-Modified; translate to request names.
            response_validators = {}
            if response.headers.get("ETag"):
                response_validators["If-None-Match"] = response.headers["ETag"]
            if response.headers.get("Last-Modified"):
                response_validators["If-Modified-Since"] = response.headers["Last-Modified"]
            if response_validators:
                with _HTTP_LOCK:
                    _VALIDATORS[key] = response_validators
        return response
    return None


def _is_verification_page(text):
    sample = (text or "")[:12000].casefold()
    return bool(re.search(r"安全验证|访问验证|人机校验|请完成验证|captcha|robot check", sample))


def _clean_text(value):
    if value is None:
        return ""
    value = html_lib.unescape(str(value))
    value = value.replace("\\/", "/")
    if re.search(r"\\u[0-9a-fA-F]{4}", value):
        value = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda match: chr(int(match.group(1), 16)),
            value,
        )
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _price(value):
    text = _clean_text(value).replace(",", "")
    match = re.search(r"(?:¥|￥|价格\s*)\s*(\d+(?:\.\d{1,2})?)", text)
    if not match:
        match = re.search(r"\b(\d{2,7}(?:\.\d{1,2})?)\b", text)
    if not match:
        return None
    try:
        result = float(match.group(1))
    except ValueError:
        return None
    return result if 1 <= result <= 2_000_000 else None


def _item_url(value):
    url = urljoin("https://www.goofish.com/", html_lib.unescape(str(value or "")))
    parsed = urlparse(url)
    if parsed.netloc not in {"www.goofish.com", "goofish.com"}:
        return None
    query = parse_qs(parsed.query)
    if not query.get("id") or not re.fullmatch(r"\d{8,}", query["id"][0]):
        return None
    return f"https://www.goofish.com/item?id={query['id'][0]}"


def _append_item(items, seen, title, price, url):
    title = _clean_text(title)
    url = _item_url(url)
    price = _price(price)
    if not title or not url or price is None or url in seen:
        return
    seen.add(url)
    items.append((title[:240], price, url))


def _walk_json(value, items, seen):
    if isinstance(value, dict):
        name = value.get("name") or value.get("title") or value.get("itemTitle") or value.get("item_name")
        offers = value.get("offers") if isinstance(value.get("offers"), dict) else value
        url = value.get("url") or value.get("itemUrl") or value.get("detailUrl")
        price = offers.get("price") if isinstance(offers, dict) else None
        if name and url and price is not None:
            _append_item(items, seen, name, price, url)
        for child in value.values():
            _walk_json(child, items, seen)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, items, seen)


def _parse_json_ld(html):
    items, seen = [], set()
    blocks = re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.I | re.S)
    for block in blocks:
        try:
            _walk_json(json.loads(html_lib.unescape(block)), items, seen)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return items, seen


def _parse_goofish(html):
    """解析公开返回的 JSON-LD、商品链接和链接附近的标题/价格。"""
    items, seen = _parse_json_ld(html)
    # 公开页面可能没有 JSON-LD，但会保留商品详情链接；仅使用链接附近的可见文本/属性。
    anchor_pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*?href=[\"'](?P<href>[^\"']*item\?[^\"']*)[\"'][^>]*)>(?P<body>.*?)</a>",
        re.I | re.S,
    )
    for match in anchor_pattern.finditer(html):
        attrs = match.group("attrs")
        body = match.group("body")
        title_match = re.search(r"(?:data-title|title)=[\"']([^\"']+)", attrs, re.I)
        title = title_match.group(1) if title_match else body
        price_match = re.search(r"(?:¥|￥)\s*[\d,.]+|价格\s*[\d,.]+", body, re.I)
        if price_match:
            _append_item(items, seen, title, price_match.group(0), match.group("href"))
        if len(items) >= MAX_ITEMS:
            return items[:MAX_ITEMS]

    # 部分公开 SSR 数据会将字段编码在脚本中；只接受同一小片段内同时出现 id/title/price 的记录。
    for match in re.finditer(r"(?:itemId|item_id|itemIdStr)\"?\s*[:=]\s*[\"']?(\d{8,})", html, re.I):
        chunk = html[match.start():match.start() + 2200]
        title_match = re.search(r"(?:title|itemTitle|item_name)\"?\s*[:=]\s*[\"']([^\"']{2,240})", chunk, re.I)
        price_match = re.search(r"(?:price|soldPrice|currentPrice)\"?\s*[:=]\s*[\"']?(\d+(?:\.\d{1,2})?)", chunk, re.I)
        if title_match and price_match:
            _append_item(items, seen, title_match.group(1), price_match.group(1), f"https://www.goofish.com/item?id={match.group(1)}")
        if len(items) >= MAX_ITEMS:
            break
    return items[:MAX_ITEMS]


class HttpGoofish:
    """闲鱼公开搜索页采集器；无公开商品数据或需要验证时返回明确状态。"""
    name = "goofish"
    label = "闲鱼"

    def fetch(self, kw, cookies=None):
        url = "https://www.goofish.com/search"
        params = {"q": str(kw or "").strip()}
        response = _safe_get(self.name, _session(self.name), url, params=params, cookies=cookies)
        if isinstance(response, str):
            return response
        if response is None:
            return "unavailable"
        if response.status_code in {401, 403}:
            return "need_login"
        if response.status_code == 429 or _is_verification_page(response.text):
            _trip_circuit(self.name)
            return "blocked"
        if response.status_code != 200:
            return "unavailable"
        return _parse_goofish(response.text)


class HttpJD:
    """京东：搜索页服务端渲染时可解析商品卡。"""
    name = "jd"
    label = "京东"

    def fetch(self, kw, cookies=None):
        url = "https://search.jd.com/Search"
        response = _safe_get(self.name, _session(self.name), url,
                             params={"keyword": str(kw or "").strip(), "enc": "utf-8"}, cookies=cookies)
        if isinstance(response, str):
            return response
        if response is None or response.status_code != 200 or _is_verification_page(response.text):
            if response is not None and _is_verification_page(response.text):
                _trip_circuit(self.name)
                return "blocked"
            return []
        results, seen = [], set()
        for match in re.finditer(
            r'data-sku=["\'](\d+)["\'][\s\S]*?class=["\']p-name[^"\']*["\'][\s\S]*?<em>([^<]{5,160})</em>[\s\S]*?'
            r'class=["\']p-price["\'][^>]*>[\s\S]*?<i>([\d.]+)</i>', response.text, re.I,
        ):
            sku, title, price = match.groups()
            item_url = f"https://item.jd.com/{sku}.html"
            if item_url in seen:
                continue
            seen.add(item_url)
            results.append((_clean_text(title), float(price), item_url))
            if len(results) >= MAX_ITEMS:
                break
        return results


class HttpTaobao:
    name = "taobao"
    label = "淘宝"

    def fetch(self, kw, cookies=None):
        return "not_implemented"


class HttpPDD:
    name = "pdd"
    label = "拼多多"

    def fetch(self, kw, cookies=None):
        return "not_implemented"


HTTP_PLATFORMS = {
    "goofish": HttpGoofish().fetch,
    "jd": HttpJD().fetch,
    "taobao": HttpTaobao().fetch,
    "pdd": HttpPDD().fetch,
}
