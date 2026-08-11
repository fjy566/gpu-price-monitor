# -*- coding: utf-8 -*-
"""
合规爬虫核心 —— 方案 C：真实 Chrome 复用登录 + 手动逐个登录 + 礼貌采集。

设计要点（重要）：
- 【复用你的真实 Chrome】：通过 Playwright 的 channel="chrome" + user_data_dir
  指向你系统 Chrome 的用户数据目录。这样你平时在 Chrome 里登录过的京东/淘宝/闲鱼
  会话【直接可用】，不再每次扫码，也彻底解决"无痕浏览器"问题。
  ⚠️ 注意：连接真实用户目录时，建议先【完全退出】正在运行的 Chrome，
  否则 Playwright 会因 profile 占用而报错。
- 【完全手动逐个登录】：每个平台一个独立"登录"按钮，点击才打开该平台登录页。
  绝不在确认后自动跳到下一个平台。你想登录几个就登录几个。
- 只采集登录后【自身可见范围内】的公开搜索页商品标题/价格/链接，
  不做任何反爬对抗（无指纹伪装/无验证码自动绕过/无代理池）。
- 高频受限：每个请求随机延时 + 限速，尊重服务器。
- 若某平台未登录/采集失败，自动降级为内置演示数据，保证流程不断。

线程模型：登录与采集各自独立控制；threading.Event 实现
采集的 暂停(pause) / 继续(resume) / 停止(stop)。
"""

import os
import math
import random
import re
import subprocess
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from urllib.parse import quote_plus, urljoin
from queue import Queue

from playwright.sync_api import sync_playwright

import database as db
from settings_store import DEFAULT_SETTINGS, get_settings, save_settings
from listing_pipeline import FilterPolicy, ListingFilter

# 限速参数（秒）
MIN_DELAY = 3.0
MAX_DELAY = 6.0      # 页与页之间
ROUND_GAP = 15       # 兼容旧配置；当前任务每个型号只采集一轮，不再循环
PAGE_WAIT = 4        # 页面渲染等待秒数

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sessions")
os.makedirs(STATE_DIR, exist_ok=True)

# 演示数据基础价格区间（仅当某平台未登录/采集失败时兜底，非真实数据）
# 已设为贴近 2026 年二手/行情参考价，避免出现离谱数字误导
DEMO_BASE = {
    50: (4299, 16800),   # RTX 50 系全新/二手主流区间
    40: (1599, 16000),   # RTX 40 系
    30: (799, 8000),     # RTX 30 系
}

_PLATFORM_LABEL = {"goofish": "闲鱼", "jd": "京东", "taobao": "淘宝", "pdd": "拼多多"}
# 产品当前只开放一个真实数据源；其余解析器保留在代码中，便于未来扩展，
# 但不会被设置、调度器或 Web API 选中。
ACTIVE_PLATFORM_NAMES = ("goofish",)
ACTIVE_PLATFORM_LABEL = _PLATFORM_LABEL[ACTIVE_PLATFORM_NAMES[0]]

# ------------------------------------------------------------------
# 可配置的噪声过滤阈值（后台可改，存于 state 表）
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# 真实 Chrome 定位
# ------------------------------------------------------------------
def find_chrome():
    candidates = [
        os.environ.get("PROGRAMFILES", "C:/Program Files") + "/Google/Chrome/Application/chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)") + "/Google/Chrome/Application/chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + "/Google/Chrome/Application/chrome.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None

def find_chrome_user_data():
    return os.environ.get("LOCALAPPDATA", "") + "/Google/Chrome/User Data"

CHROME_PATH = find_chrome()
CHROME_USER_DATA = find_chrome_user_data()
# 本项目专用的持久化浏览器 profile（独立于你的日常 Chrome，避免占用冲突；
# 登录一次长期保留，解决"无痕每次重登"的问题，也无需关闭日常 Chrome）
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome_profile")
os.makedirs(PROFILE_DIR, exist_ok=True)
LOGIN_URLS = {
    "goofish": "https://www.goofish.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
    "taobao": "https://login.taobao.com/",
    "pdd": "https://mobile.yangkeduo.com/personal.html",
}

# 登录/采集的浏览器访问锁：Playwright + 真实用户目录同一时间只开一个浏览器实例
_browser_lock = threading.Lock()

# ======================================================================
# 平台采集器
# ======================================================================
class PlatformCrawler:
    name = "base"
    label = "base"

    def __init__(self):
        self.logged_in = False
        self.login_hint = ""

    # 判断是否已登录：检测"登录成功"特有的页面元素（各平台不同）
    def check_login(self, context):
        raise NotImplementedError

    # 采集单个型号，返回 [(title, price, url)]
    def fetch(self, context, model_keyword, on_batch=None):
        raise NotImplementedError


class GoofishCrawler(PlatformCrawler):
    name = "goofish"
    label = "闲鱼"

    def check_login(self, context):
        page = None
        try:
            page = context.new_page()
            page.goto("https://www.goofish.com/", timeout=30000)
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            # 已登录：出现"我的/发布/消息/退出"等用户功能词；未登录通常有明显的"登录/注册"入口
            logged_markers = ["退出", "我的", "发布", "消息"]
            login_markers = ["立即登录", "注册", "请登录"]
            logged_in = any(m in body for m in logged_markers) and "立即登录" not in body
            return logged_in
        except Exception:
            return False
        finally:
            if page:
                try: page.close()
                except Exception: pass

    def fetch(self, context, kw, on_batch=None):
        page = context.new_page()
        try:
            return self._fetch_page(page, kw, on_batch=on_batch)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _fetch_page(self, page, kw, on_batch=None):
        page.goto(f"https://www.goofish.com/search?q={quote_plus(kw)}", timeout=60000)
        page.wait_for_timeout(5000)
        # 自动翻页：闲鱼有分页【右箭头 >】按钮，逐页点击翻到最后一页，收集所有商品
        results = []
        seen_ids = set()
        max_pages = 50              # 兜底上限（RTX 5080 实际 50 页，一般 10-50 页）
        try:
            for _ in range(max_pages):
                page_start = len(results)
                # 收集当前页所有卡片
                cards = page.query_selector_all(_GOOFISH_CARD_SELECTOR)
                if not cards:
                    # 商品卡片是懒加载的；轻滚动一次再读取，避免只看到“加载中”。
                    try:
                        page.mouse.wheel(0, 900)
                        page.wait_for_timeout(1200)
                        cards = page.query_selector_all(_GOOFISH_CARD_SELECTOR)
                    except Exception:
                        pass
                for card in cards:
                    try:
                        href = card.get_attribute("href") or ""
                        absolute = _abs_url(href, "https://www.goofish.com")
                        if not absolute or absolute in seen_ids:
                            continue
                        seen_ids.add(absolute)
                        raw_text = _card_text(card)
                        t = _goofish_card_title(card, kw, raw_text)
                        p = _goofish_card_price(card, raw_text)
                        if t and p:
                            results.append((t, p, absolute))
                    except Exception:
                        continue
                # 找右箭头（下一页）按钮：search-pagination-arrow-container 或 search-page-tiny-arrow-container
                if on_batch and len(results) > page_start:
                    try:
                        should_continue = on_batch(results[page_start:])
                        if should_continue is False:
                            break
                    except Exception:
                        pass
                next_btn = None
                try:
                    for sel in ["button[class*='search-pagination-arrow-container']",
                                "[class*='search-pagination-arrow-base']",
                                "button[class*='search-page-tiny-arrow-container']",
                                "[class*='search-page-tiny-arrow'][class*='right']"]:
                        el = page.query_selector(sel)
                        if el:
                            next_btn = el
                            break
                except Exception:
                    pass
                # 检测是否还有下一页
                no_next = False
                if next_btn:
                    try:
                        cls = next_btn.get_attribute("class") or ""
                        if "disabled" in cls.lower() or "disable" in cls.lower():
                            no_next = True
                    except Exception:
                        pass
                    if not no_next:
                        # 记录点击前首卡（判断是否真的翻页了）
                        before_first = None
                        try:
                            f = page.query_selector(_GOOFISH_CARD_SELECTOR)
                            before_first = f.get_attribute("href") if f else None
                        except Exception:
                            pass
                        try:
                            advanced = False
                            # 方法A：点页码（当前页+1）触发翻页
                            try:
                                cur_page = None
                                try:
                                    tiny = page.query_selector("[class*='search-page-tiny-page']")
                                    ti = tiny.inner_text() if tiny else ""
                                    import re as _re
                                    mm = _re.search(r"(\d+)\s*/\s*(\d+)", ti)
                                    if mm:
                                        cur_page = int(mm.group(1))
                                except Exception:
                                    pass
                                target = (cur_page + 1) if cur_page else 2
                                next_box = None
                                for box in page.query_selector_all("[class*='search-pagination-page-box']"):
                                    if box.inner_text().strip() == str(target):
                                        next_box = box
                                        break
                                if next_box:
                                    try: next_box.scroll_into_view_if_needed()
                                    except Exception: pass
                                    try:
                                        next_box.click(force=True)
                                        page.wait_for_timeout(3000)
                                        advanced = True
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            # 方法B：底部箭头
                            if not advanced and next_btn:
                                try: next_btn.scroll_into_view_if_needed()
                                except Exception: pass
                                try:
                                    next_btn.click(force=True)
                                    page.wait_for_timeout(3000)
                                    advanced = True
                                except Exception:
                                    pass
                            # 判断是否真的翻页（首卡变化），否则停
                            after_first = None
                            try:
                                f2 = page.query_selector(_GOOFISH_CARD_SELECTOR)
                                after_first = f2.get_attribute("href") if f2 else None
                            except Exception:
                                pass
                            if (not advanced) or (before_first and before_first == after_first):
                                no_next = True
                        except Exception:
                            no_next = True
                else:
                    no_next = True
                if no_next:
                    break
        except Exception:
            pass
        return results


class JDCrawler(PlatformCrawler):
    name = "jd"
    label = "京东"

    def check_login(self, context):
        page = None
        try:
            page = context.new_page()
            page.goto("https://passport.jd.com/new/login.aspx", timeout=30000)
            page.wait_for_timeout(2500)
            # 京东：未登录会停在登录页；已登录访问会自动跳转到首页
            u = page.url.lower()
            logged = ("login" not in u) and ("passport" not in u) and ("" != page.title())
            return logged
        except Exception:
            return False
        finally:
            if page:
                try: page.close()
                except Exception: pass

    def fetch(self, context, kw, on_batch=None):
        page = context.new_page()
        try:
            return self._fetch_page(page, kw)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _fetch_page(self, page, kw):
        page.goto(f"https://search.jd.com/Search?keyword={quote_plus(kw)}&enc=utf-8", timeout=45000)
        page.wait_for_timeout((PAGE_WAIT + 2) * 1000)
        try:
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        results = _collect(
            page,
            card_sels=[".gl-item", ".gl-i-wrap", "[data-sku]"],
            title_sels=[".p-name em", ".p-name a", "em"],
            price_sels=[".p-price i", ".p-price strong", "i"],
            link_sels=[".p-name a", "a"],
            base="https://search.jd.com",
        )
        return results


class TaobaoCrawler(PlatformCrawler):
    name = "taobao"
    label = "淘宝"

    def check_login(self, context):
        page = None
        try:
            page = context.new_page()
            # 访问登录后的个人页：已登录则停留，未登录会被重定向到登录页
            page.goto("https://i.taobao.com/my_taobao.htm", timeout=30000)
            page.wait_for_timeout(3500)
            u = page.url.lower()
            # 已登录：URL 仍是个人页；未登录：被重定向到 login/passport
            logged = ("login" not in u) and ("passport" not in u) and ("i.taobao.com" in u or "taobao.com" in u)
            if not logged:
                # 兜底：看页面是否出现用户名/我的淘宝等登录态标识
                body = page.inner_text("body")
                if "我的淘宝" in body and "登录" not in body[:200]:
                    logged = True
            return logged
        except Exception:
            return False
        finally:
            if page:
                try: page.close()
                except Exception: pass

    def fetch(self, context, kw, on_batch=None):
        page = context.new_page()
        try:
            return self._fetch_page(page, kw)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _fetch_page(self, page, kw):
        page.goto(f"https://s.taobao.com/search?q={quote_plus(kw)}", timeout=45000)
        page.wait_for_timeout((PAGE_WAIT + 2) * 1000)
        try:
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        results = _collect(
            page,
            card_sels=["[class*='item']", "[class*='Card']", "[class*='content--']"],
            title_sels=["[class*='title']", "[class*='Title']", "h3", "a[href*='item.taobao']"],
            price_sels=["[class*='price']", "[class*='Price']"],
            link_sels=["a[href*='item.taobao']", "a[href*='detail.tmall']", "a"],
            base="https://s.taobao.com",
        )
        return results


class PDDCrawler(PlatformCrawler):
    name = "pdd"
    label = "拼多多"

    def check_login(self, context):
        page = None
        try:
            page = context.new_page()
            page.goto("https://mobile.yangkeduo.com/personal.html", timeout=30000)
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            # 拼多多个人中心：已登录会显示用户名/订单/收藏等；未登录有明显"登录/立即登录"
            logged = ("立即登录" not in body) and ("请登录" not in body) and ("退出登录" in body or "我的订单" in body or len(body) > 300)
            return logged
        except Exception:
            return False
        finally:
            if page:
                try: page.close()
                except Exception: pass

    def fetch(self, context, kw, on_batch=None):
        page = context.new_page()
        try:
            return self._fetch_page(page, kw)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _fetch_page(self, page, kw):
        page.goto(f"https://mobile.yangkeduo.com/search_result.html?search_key={quote_plus(kw)}", timeout=45000)
        page.wait_for_timeout((PAGE_WAIT + 2) * 1000)
        results = _collect(
            page,
            card_sels=["[class*='goods']", "[class*='item']", "[class*='card']"],
            title_sels=["[class*='name']", "[class*='title']", "[class*='Title']"],
            price_sels=["[class*='price']", "[class*='Price']"],
            link_sels=["a[href*='goods']", "a"],
            base="https://mobile.yangkeduo.com",
        )
        return results


# ======================================================================
# 工具函数
# ======================================================================
def _parse_price(text):
    raw = text or ""
    # 优先使用货币符号后的主价格；否则采用第一个数字，避免把“12期/月供/券减”当售价。
    marked = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)", raw)
    match = marked or re.search(r"[\d,]+(?:\.\d+)?", raw)
    if not match:
        return None
    value = match.group(1) if marked else match.group(0)
    try:
        price = float(value.replace(",", ""))
    except ValueError:
        return None
    return price if math.isfinite(price) and price > 0 else None


def _parse_goofish_price_text(text):
    """Parse 闲鱼 price text, including line-broken Chinese ``万`` values."""
    raw = text or ""
    compact = re.sub(r"\s+", "", raw)
    # 页面常把“¥3.26万”渲染成“¥\n3\n.26\n万”。
    wan_matches = re.findall(r"[¥￥]?([\d,]+(?:\.\d+)?)万", compact, flags=re.IGNORECASE)
    for value in wan_matches:
        try:
            price = float(value.replace(",", "")) * 10000
            if math.isfinite(price) and price > 0:
                return price
        except ValueError:
            continue
    marked = re.findall(r"[¥￥]([\d,]+(?:\.\d+)?)", compact)
    for value in marked:
        try:
            price = float(value.replace(",", ""))
            if math.isfinite(price) and price > 0:
                return price
        except ValueError:
            continue
    return _parse_price(raw)


_GOOFISH_CARD_SELECTOR = (
    "a[class*='feeds-item-wrap'],"
    "a[href*='/item?id='],"
    "a[href*='item?id='],"
    "a[href*='/item/']"
)


def _compact_search_text(value):
    """Normalize search text for matching dynamic card labels."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _card_text(element):
    try:
        return re.sub(r"[ \t\r\f\v]+", " ", element.inner_text() or "").strip()
    except Exception:
        return ""


def _goofish_card_title(card, kw, raw_text=""):
    """Read a title without relying on one generated CSS module class."""
    selectors = (
        "[class*='main-title']", "[class*='row1-wrap-title']",
        "[class*='title']", "[class*='Title']", "[class*='name']",
        "[class*='Name']", "h3", "h4"
    )
    compact_kw = _compact_search_text(kw)
    fallback = ""
    for selector in selectors:
        try:
            element = card.query_selector(selector)
            value = _card_text(element) if element else ""
            if not value:
                continue
            fallback = fallback or value
            if not compact_kw or compact_kw in _compact_search_text(value):
                return value[:300]
        except Exception:
            continue
    for attr in ("title", "aria-label"):
        try:
            value = (card.get_attribute(attr) or "").strip()
            if value and (not compact_kw or compact_kw in _compact_search_text(value)):
                return value[:300]
            fallback = fallback or value
        except Exception:
            pass
    lines = [line.strip() for line in re.split(r"\n+", raw_text or _card_text(card)) if line.strip()]
    for line in lines:
        compact_line = _compact_search_text(line)
        if compact_kw and compact_kw in compact_line and not re.fullmatch(r"[\d,.¥￥ ]+", line):
            return line[:300]
    for line in lines:
        if len(line) >= 4 and not re.fullmatch(r"[\d,.¥￥ ]+", line):
            return line[:300]
    return fallback[:300]


def _goofish_card_price(card, raw_text=""):
    """Read the displayed sale price, tolerating generated class names."""
    raw = raw_text or _card_text(card)
    # 闲鱼的“万”单位经常是 price 节点的兄弟文本，必须先看整张卡片，
    # 否则只取到 number 子节点里的“3”或“3.26”。
    raw_compact = re.sub(r"\s+", "", raw)
    raw_value = _parse_goofish_price_text(raw)
    if raw_value is not None and any(mark in raw_compact for mark in ("¥", "￥", "万")):
        return raw_value
    selectors = (
        "[class*='price-wrap']", "[class*='row3-wrap-price']",
        "[class*='price']", "[class*='Price']", "[class*='amount']",
        "[class*='Amount']", "[class*='money']", "[class*='Money']"
    )
    for selector in selectors:
        try:
            element = card.query_selector(selector)
            if not element:
                continue
            number = element.query_selector("[class*='number'],[class*='Number']")
            value = _parse_goofish_price_text(_card_text(element))
            if value is None and number:
                value = _parse_goofish_price_text(_card_text(number))
            if value:
                return value
        except Exception:
            continue
    # Currency-marked values are unambiguous and take precedence over title numbers.
    value = _parse_goofish_price_text(raw)
    if value is not None:
        return value
    # Some versions omit the currency glyph. Prefer a line containing one number,
    # while ignoring instalments, coupons and social-proof counters.
    for line in re.split(r"\n+", raw):
        lower = line.casefold()
        if any(marker in lower for marker in ("期", "月供", "券", "减", "人想要", "人付款")):
            continue
        values = re.findall(r"(?<![a-z])\d[\d,]*(?:\.\d+)?", line, flags=re.IGNORECASE)
        if len(values) == 1:
            try:
                price = float(values[0].replace(",", ""))
                if 0 < price < 1_000_000:
                    return price
            except ValueError:
                pass
    return None


def _abs_url(href, base):
    if not href:
        return ""
    return urljoin(base.rstrip("/") + "/", href)


def _collect(page, card_sels, title_sels, price_sels, link_sels, base, limit=30):
    results = []
    seen = set()
    cards = []
    for sel in card_sels:
        cards += page.query_selector_all(sel)
    for card in cards:
        try:
            title_el = price_el = link_el = None
            for s in title_sels:
                if card.query_selector(s):
                    title_el = card.query_selector(s); break
            for s in price_sels:
                if card.query_selector(s):
                    price_el = card.query_selector(s); break
            for s in link_sels:
                if card.query_selector(s):
                    link_el = card.query_selector(s); break
            if not (title_el and price_el):
                continue
            t = title_el.inner_text().strip()
            p = _parse_price(price_el.inner_text())
            href = link_el.get_attribute("href") if link_el else ""
            if t and p:
                absolute = _abs_url(href, base)
                key = absolute or (t, p)
                if key in seen:
                    continue
                seen.add(key)
                results.append((t, p, absolute))
                if len(results) >= limit:
                    break
        except Exception:
            continue
    return results


# ======================================================================
# 浏览器会话管理（单线程 Worker 模型）
#
# 关键：Playwright sync API 的对象绑定创建它的线程，不能跨线程调用
# （否则报 "cannot switch to a different thread"）。
# 因此启动专用浏览器线程，所有操作（启动/开登录页/校验/采集）通过队列
# 提交到该线程串行执行，结果经 threading.Event + 槽位回传。
# ======================================================================
class BrowserManager:
    def __init__(self, headless=True):
        self._pw = None
        self._browser = None
        self._context = None
        self._thread = None
        self._queue = Queue()
        self._state_lock = threading.Lock()
        self._running = False
        self._starting = False
        self.ready = False
        self.last_error = ""
        self.headless = headless   # True=静默采集（不弹窗口）；登录二维码改由后台截图/前端展示
        self.minimized = False

    # ---------- 提交操作（供任何线程调用） ----------
    def _submit(self, op, *args, timeout=180):
        """把操作交给浏览器线程执行，阻塞等待结果。返回 (ok, result)。"""
        done = threading.Event()
        slot = {}
        self._queue.put((op, args, done, slot))
        done.wait(timeout=timeout)
        if not done.is_set():
            slot["cancelled"] = True
            return False, {"msg": "浏览器操作超时"}
        return slot.get("ok"), slot.get("result")

    def _process_queue(self):
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            op, args, done, slot = job
            if slot.get("cancelled"):
                done.set()
                self._queue.task_done()
                continue
            try:
                ok, result = op(*args)
                slot["ok"] = ok
                slot["result"] = result
            except Exception as e:
                slot["ok"] = False
                slot["result"] = {"msg": str(e)}
            finally:
                done.set()
                self._queue.task_done()

    # ---------- 浏览器线程内的实际操作 ----------
    def _boot_impl(self, headless=True, minimized=False):
        args = ["--start-minimized"] if (not headless and minimized) else []
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",
            headless=headless,
            executable_path=CHROME_PATH,
            viewport={"width": 1280, "height": 900},
            args=args,
        )
        self._browser = self._context.browser
        return True, {"msg": "浏览器已启动"}

    def _open_login_impl(self, platform):
        url = LOGIN_URLS.get(platform)
        if not url:
            return False, {"msg": "未知平台"}
        page = self._context.new_page()
        page.set_viewport_size({"width": 900, "height": 800})
        page.goto(url, timeout=45000)
        page.wait_for_timeout(4000)
        qr_data = None
        # 方式1：尝试提取二维码 <img> 为 data URL
        try:
            img = page.query_selector("img[class*='qr'], img[class*='QR'], img[class*='code'], img[src*='qr']")
            if img:
                src = img.get_attribute("src") or ""
                if src.startswith("http") or src.startswith("//"):
                    import base64
                    resp = page.request.get(src if src.startswith("http") else "https:" + src)
                    if resp.ok:
                        ctype = resp.headers.get("content-type", "image/png")
                        qr_data = "data:" + ctype + ";base64," + base64.b64encode(resp.body()).decode()
        except Exception:
            pass
        # 方式2：直接截取登录页（含二维码）为图片供前端展示
        screenshot_b64 = None
        try:
            shot = page.screenshot(full_page=False)
            import base64 as _b64
            screenshot_b64 = "data:image/png;base64," + _b64.b64encode(shot).decode()
        except Exception:
            pass
        return True, {"msg": f"已打开{_label_of(platform)}登录页（请扫码）", "url": url,
                      "page_open": True, "qr": qr_data, "screenshot": screenshot_b64}

    def _check_impl(self, pc):
        return True, pc.check_login(self._context)

    def _fetch_impl(self, pc, kw, on_batch=None):
        return True, pc.fetch(self._context, kw, on_batch=on_batch)

    def _debug_html_impl(self, url, save_path, wait_ms):
        pg = self._context.new_page()
        pg.goto(url, timeout=45000)
        pg.wait_for_timeout(wait_ms)
        html = pg.content()
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(html)
        try: pg.close()
        except Exception: pass
        return True, {"saved": save_path, "len": len(html)}

    def _debug_fetch_impl(self, pc, kw):
        pg = self._context.new_page()
        pg.goto(f"https://www.goofish.com/search?q={quote_plus(kw)}", timeout=45000)
        pg.wait_for_timeout(6000)
        try:
            pg.mouse.wheel(0, 800); pg.wait_for_timeout(1500)
        except Exception: pass
        cards = pg.query_selector_all(_GOOFISH_CARD_SELECTOR)
        info = {"cards": len(cards), "samples": []}
        for card in cards[:4]:
            try:
                raw = _card_text(card)
                href = card.get_attribute("href") or ""
                info["samples"].append({
                    "href": _abs_url(href, "https://www.goofish.com"),
                    "title": _goofish_card_title(card, kw, raw)[:120],
                    "price": _goofish_card_price(card, raw),
                    "text": raw[:240],
                })
            except Exception as e:
                info["samples"].append({"err": str(e)[:60]})
        try: pg.close()
        except Exception: pass
        return True, info

    def debug_fetch(self, pc, kw):
        return self._submit(self._debug_fetch_impl, pc, kw)

    def debug_paginate(self, kw):
        """诊断：实际跑一遍闲鱼翻页采集，返回抓到的卡片数与去重数。"""
        return self._submit(self._debug_paginate_impl, kw)

    def _debug_paginate_impl(self, kw):
        page = self._context.new_page()
        page.goto(f"https://www.goofish.com/search?q={quote_plus(kw)}", timeout=60000)
        page.wait_for_timeout(6000)
        info = {"kw": kw}
        # 逐页点击右箭头，记录每页卡片唯一数与累计
        seen = set()
        page_progress = []
        # 打印所有分页/箭头元素细节，定位可点的右箭头
        info["arrows"] = []
        try:
            js = "() => Array.from(document.querySelectorAll('[class*=\"pagination\"],[class*=\"arrow\"],[class*=\"page-tiny\"]')).map(el=>({tag:el.tagName,cls:(el.className||'').toString().slice(0,60),dis:(el.className||'').includes('disabled'),txt:(el.textContent||'').trim().slice(0,12)})).slice(0,24)"
            info["arrows"] = page.evaluate(js)
        except Exception as e:
            info["arrows_err"] = str(e)[:80]
        max_pages = 12
        try:
            for i in range(max_pages):
                cards = page.query_selector_all(_GOOFISH_CARD_SELECTOR)
                before = len(seen)
                for card in cards:
                    try:
                        h = card.get_attribute("href") or ""
                        if h:
                            seen.add(h)
                    except Exception:
                        pass
                # 定位右箭头
                next_btn = None
                for sel in ["button[class*='search-pagination-arrow-container']",
                            "[class*='search-pagination-arrow-base']",
                            "button[class*='search-page-tiny-arrow-container']"]:
                    el = page.query_selector(sel)
                    if el:
                        next_btn = el
                        break
                before_url = page.url
                before_first = None
                try:
                    f = page.query_selector(_GOOFISH_CARD_SELECTOR)
                    before_first = f.get_attribute("href") if f else None
                except Exception:
                    pass
                page_progress.append({"page": i + 1, "this_page_cards": len(cards), "unique_accum": len(seen),
                                      "url": before_url[:60], "first": (before_first or "")[:40]})
                if not next_btn:
                    page_progress[-1]["no_arrow"] = True
                    break
                try:
                    cls = next_btn.get_attribute("class") or ""
                    if "disabled" in cls.lower():
                        page_progress[-1]["disabled"] = True
                        break
                except Exception:
                    pass
                # 直接点页码“下一页”更可靠：点第2页，或“下一页”页码按钮
                page_target = None
                try:
                    # 点“下一页”页数字（当前页+1），或底部箭头
                    # 用底部唯一箭头 next_btn (真实点击)
                    page_target = next_btn
                except Exception:
                    pass
                try:
                    # 方法A：点页码“下一页”（找当前页+1 的数字框）—— 比箭头更可靠
                    advanced = False
                    try:
                        # 从 mini 分页读当前页，如 “1/50” → 当前=1
                        cur_page = None
                        try:
                            tiny = page.query_selector("[class*='search-page-tiny-page']")
                            ti = tiny.inner_text() if tiny else ""
                            import re as _re
                            mm = _re.search(r"(\d+)\s*/\s*(\d+)", ti)
                            if mm:
                                cur_page = int(mm.group(1))
                        except Exception:
                            pass
                        target = (cur_page + 1) if cur_page else 2
                        next_box = None
                        for box in page.query_selector_all("[class*='search-pagination-page-box']"):
                            if box.inner_text().strip() == str(target):
                                next_box = box
                                break
                        if next_box:
                            try: next_box.scroll_into_view_if_needed()
                            except Exception: pass
                            try:
                                next_box.click(force=True)
                                page.wait_for_timeout(3500)
                                advanced = True
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # 若页码没点成，退回点底部箭头
                    if not advanced and next_btn:
                        try:
                            next_btn.scroll_into_view_if_needed()
                            page.wait_for_timeout(300)
                        except Exception:
                            pass
                        try:
                            next_btn.click(force=True)
                            page.wait_for_timeout(3500)
                            advanced = True
                        except Exception:
                            pass
                    page_progress[-1]["clicked"] = advanced
                    after_url = page.url
                    page_progress[-1]["after_url"] = after_url[:60]
                    after_first = None
                    try:
                        f2 = page.query_selector(_GOOFISH_CARD_SELECTOR)
                        after_first = f2.get_attribute("href") if f2 else None
                    except Exception:
                        pass
                    page_progress[-1]["after_first"] = (after_first or "")[:40]
                    page_progress[-1]["url_changed"] = before_url != after_url
                    page_progress[-1]["first_changed"] = before_first != after_first
                    if (before_first == after_first) or not advanced:
                        page_progress[-1]["no_advance"] = True
                        break
                except Exception as e:
                    page_progress[-1]["click_err"] = str(e)[:60]
                    break
                # 判断是否前进：首卡是否变化
                try:
                    first = page.query_selector(_GOOFISH_CARD_SELECTOR)
                    fh = first.get_attribute("href") if first else ""
                    if fh and fh in seen:
                        page_progress[-1]["stuck"] = True
                        break
                except Exception:
                    pass
        except Exception as e:
            info["err"] = str(e)[:120]
        info["total_unique"] = len(seen)
        info["pages_reached"] = page_progress
        try: page.close()
        except Exception: pass
        return True, info

    def _close_impl(self):
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._pw = None
        self.ready = False
        return True, {"msg": "浏览器已关闭"}

    # ---------- 对外同步接口（内部转成队列任务） ----------
    def start_browser(self, headless=None, minimized=False):
        with self._state_lock:
            if self.ready:
                return True, {"msg": "浏览器已在运行"}
            if self._starting:
                return False, {"msg": "浏览器正在启动，请稍候"}
            self._starting = True
            if headless is None:
                headless = self.headless
            self.minimized = minimized
            self._running = True
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._process_queue, daemon=True)
                self._thread.start()
        try:
            ok, res = self._submit(self._boot_impl, headless, minimized)
            self.ready = bool(ok)
            if not ok:
                self.last_error = str((res or {}).get("msg", "启动失败"))
                self._submit(self._close_impl)
                self._running = False
                self._queue.put(None)
            return ok, res
        finally:
            with self._state_lock:
                self._starting = False

    def open_login_page(self, platform):
        return self._submit(self._open_login_impl, platform)

    def check(self, pc):
        return self._submit(self._check_impl, pc)

    def fetch(self, pc, kw, on_batch=None):
        # 闲鱼完整翻页可能超过三分钟；增量结果会持续入库，整轮等待留足时间。
        return self._submit(self._fetch_impl, pc, kw, on_batch, timeout=420)

    def reboot(self, headless, minimized=False):
        """关闭当前浏览器并按指定模式重启（用于切换静默采集/可视化登录/最小化）。"""
        if self.ready:
            self._submit(self._close_impl)
        self._pw = self._browser = self._context = None
        self.ready = False
        self._running = True
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._process_queue, daemon=True)
            self._thread.start()
        self.headless = headless
        self.minimized = minimized
        ok, res = self._submit(self._boot_impl, headless, minimized)
        if ok:
            self.ready = True
        return ok, res

    def close(self):
        if self.ready:
            self._submit(self._close_impl)
        self._running = False
        self.ready = False
        self._queue.put(None)
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        if not thread or not thread.is_alive():
            self._thread = None
            self._queue = Queue()

    @property
    def is_ready(self):
        return self.ready and self._browser is not None


def _label_of(p):
    return {"goofish": "闲鱼", "jd": "京东", "taobao": "淘宝", "pdd": "拼多多"}.get(p, p)

# 全局浏览器 Worker 单例
manager = BrowserManager(headless=False)   # 默认：可视化+最小化采集（保证真实数据，窗口最小化不打扰）


# ======================================================================
# 总调度器（采集循环）
# ======================================================================
class Crawler:
    def __init__(self, headless=False):
        self._running = False
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._thread = None
        self.status = "stopped"
        self.last_error = ""
        self.last_run = ""
        self.rounds = 0
        self.items_found = 0
        self.headless = headless
        self.platforms = [GoofishCrawler(), JDCrawler(), TaobaoCrawler(), PDDCrawler()]
        self._context = None
        # 进度：当前轮次的完成度（0~1）
        self.progress = 0.0
        self._round_total = 0
        self._round_done = 0
        self.current_task = ""      # 当前正在采集的任务描述（用于日志）
        self.task_history = deque(maxlen=100)  # O(1) 追加并自动淘汰旧日志
        self._last_transport_note = ""

    # ---- 控制接口 ----
    def start(self):
        with self._state_lock:
            if self._running or (self._thread and self._thread.is_alive()):
                return False
            self._running = True
            self._stop_event.clear()
            self._pause_event.set()
            self.status = "running"
            self.last_error = ""
            self.items_found = 0
            self._last_transport_note = ""
            db.set_states({"last_error": "", "session_items_found": "0"})
            try:
                from gpus import get_all_models
                debug_states = {}
                for platform in self.platforms:
                    if platform.name not in ACTIVE_PLATFORM_NAMES:
                        continue
                    for model in get_all_models():
                        debug_states[f"debug_{platform.name}_{model['name'].replace(' ', '')}"] = ""
                db.set_states(debug_states)
            except Exception:
                # 清理诊断状态不应阻止正式采集启动。
                pass
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return True

    def pause(self):
        if self._running and self.status == "running":
            self._pause_event.clear()
            self.status = "paused"
            return True
        return False

    def resume(self):
        if self._running and self.status == "paused":
            self._pause_event.set()
            self.status = "running"
            return True
        return False

    def stop(self):
        with self._state_lock:
            self._running = False
            self._stop_event.set()
            self._pause_event.set()
            self.status = "stopped"
            thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._state_lock:
            if self._thread is thread and (not thread or not thread.is_alive()):
                self._thread = None
        return True

    # ---- 主循环 ----
    def _loop(self):
        try:
            if not manager.is_ready:
                cfg = get_settings()
                mode = cfg.get("browser_mode", "minimized")
                self._log("正在自动启动专用浏览器…")
                ok, result = manager.start_browser(
                    headless=(mode == "silent"), minimized=(mode == "minimized")
                )
                if not ok:
                    self.status = "error"
                    self.last_error = str((result or {}).get("msg", "浏览器启动失败"))
                    db.set_state("last_error", self.last_error)
                    return
            self._pause_event.wait()
            if self._running:
                self._one_round()
            if self._running:
                self.rounds += 1
                self.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.set_states({"last_run": self.last_run, "rounds": self.rounds})
                self.status = "completed"
                self._log("本次采集已完成：每个选中型号各采集一轮", "ok")
        except Exception:
            self.last_error = traceback.format_exc()
            self.status = "error"
            db.set_state("last_error", self.last_error)
        finally:
            try:
                manager.close()
            except Exception as exc:
                self._log(f"浏览器自动退出失败：{exc}", "warn")
            with self._state_lock:
                self._running = False
                if self._thread is threading.current_thread():
                    self._thread = None
            self.current_task = ""
            db.set_state("current_task", "")

    def _log(self, msg, level="info"):
        """记录一条采集日志（内存环形缓冲），供日志面板显示。"""
        from datetime import datetime as _dt
        entry = {"ts": _dt.now().strftime("%H:%M:%S"), "level": level, "msg": msg}
        self.task_history.append(entry)

    def _one_round(self):
        from gpus import get_all_models, infer_model
        all_models = get_all_models()
        cfg = get_settings()
        sel_platforms = set(ACTIVE_PLATFORM_NAMES)
        sel_models = {x.strip() for x in cfg["selected_models"].split(",") if x.strip()}
        # 计算本轮任务量，用于进度条
        tasks = 0
        for pc in self.platforms:
            if sel_platforms and pc.name not in sel_platforms:
                continue
            for model in all_models:
                if sel_models and model["name"] not in sel_models:
                    continue
                tasks += 1
        self._round_total = max(tasks, 1)
        self._round_done = 0
        self.progress = 0.0
        for pc in self.platforms:
            if sel_platforms and pc.name not in sel_platforms:
                continue
            if not self._running:
                return
            self._pause_event.wait()
            for model in all_models:
                if sel_models and model["name"] not in sel_models:
                    continue
                if not self._running:
                    return
                self._pause_event.wait()
                # 静默采集：只写日志，告知正在采集哪个平台哪个型号
                self.current_task = f"正在采集 {_PLATFORM_LABEL[pc.name]} {model['name']}"
                self._log(self.current_task)
                db.set_state("current_task", self.current_task)
                try:
                    self._fetch_model(pc, model, cfg)
                except Exception as e:
                    db.set_state("last_error", f"{pc.name}: {e}")
                self._round_done += 1
                self.progress = min(1.0, self._round_done / self._round_total)
        self.progress = 1.0

    def _transport_order(self, mode, pc):
        """当前产品唯一使用 Playwright 模拟浏览器通道。"""
        return ["browser"]

    def _collect_via(self, pc, kw, transport, on_batch=None):
        """通过指定通道采集单个关键词。返回 [(title,price,url)]。"""
        self._last_transport_note = ""
        if transport == "api":
            import api_panel
            res = api_panel.fetch_api_all(pc.name, kw)
            if res == "need_secret" or (isinstance(res, list) and res and res[0] == "need_secret"):
                db.set_state("last_error", f"{_PLATFORM_LABEL[pc.name]} API 缺少 secretKey，无法签名调用（请在后台API面板补全）")
                self._last_transport_note = "API 配置不完整，缺少 secretKey"
                return []
            if not isinstance(res, list):
                self._last_transport_note = "API 未返回商品列表"
                return []
            if not res:
                self._last_transport_note = "API 返回空结果"
            return res
        if transport == "http":
            import http_crawler
            fn = http_crawler.HTTP_PLATFORMS.get(pc.name)
            if not fn:
                self._last_transport_note = "HTTP 解析器尚未实现"
                return []
            res = fn(kw)
            if isinstance(res, str):
                labels = {
                    "need_login": f"{_PLATFORM_LABEL[pc.name]} HTTP 页面需要登录态",
                    "blocked": f"{_PLATFORM_LABEL[pc.name]} HTTP 页面返回访问验证，已停止请求（不会绕过验证）",
                    "not_implemented": f"{_PLATFORM_LABEL[pc.name]} HTTP 解析尚未实现",
                    "unavailable": f"{_PLATFORM_LABEL[pc.name]} HTTP 页面暂时不可用",
                }
                message = labels.get(res, f"{_PLATFORM_LABEL[pc.name]} HTTP 采集状态：{res}")
                db.set_state("last_error", message)
                self._log(message, "warn")
                self._last_transport_note = message
                return []
            if not isinstance(res, list):
                self._last_transport_note = "HTTP 未返回商品列表"
                return []
            if not res:
                self._last_transport_note = "HTTP 返回空页面或公开页面未包含商品数据"
            return res
        # browser
        if not manager.is_ready:
            self._last_transport_note = "浏览器 Worker 未启动"
            return []
        # 搜索页可能对未登录访客仍公开商品卡片；登录态未确认时也尝试一次，
        # 避免“浏览器已经正常打开但校验按钮未点过”直接被短路成空结果。
        if not pc.logged_in:
            self._last_transport_note = "平台登录态未确认，尝试公开搜索"
        ok, result = manager.fetch(pc, kw, on_batch=on_batch)
        if not ok:
            self._last_transport_note = str((result or {}).get("msg", "浏览器页面打开失败"))
            return []
        if not isinstance(result, list):
            self._last_transport_note = "浏览器解析器未返回商品列表"
            return []
        if not result:
            self._last_transport_note = "浏览器页面已打开，但未匹配到商品卡片/价格"
        return result

    def _filter_items(self, model, items, policy=None):
        """统一过滤型号错配、非整卡/引流/高风险标题和价格离群点。"""
        active_policy = policy or FilterPolicy.from_settings(get_settings())
        kept, stats = ListingFilter(active_policy).filter(model["name"], items)
        return kept, stats.as_dict()

    def _stream_store_batch(self, pc, model, batch, streamed_urls, policy=None):
        """整轮完成前逐页过滤并入库，让前端实时看到新商品。"""
        if not self._running:
            return False
        kept, _ = self._filter_items(model, batch, policy)
        fresh = []
        for title, price, url in kept:
            key = url or f"{title}\x00{price}"
            if key in streamed_urls:
                continue
            streamed_urls.add(key)
            fresh.append((title, price, url))
        self._store_items(pc, model, fresh)
        return self._running

    def _store_items(self, pc, model, items):
        """一次事务写入一个页面的商品，并逐条发布日志。"""
        if not items:
            return 0
        platform = _PLATFORM_LABEL[pc.name]
        records = [
            (model["name"], model.get("series", "其他"), model.get("generation", 0),
             platform, title, price, url)
            for title, price, url in items
        ]
        db.add_prices(records)
        self.items_found += len(records)
        db.set_state("session_items_found", str(self.items_found))
        for title, price, _ in items:
            self._log(f"{platform} {model['name']} 已入库：{title[:42]} ¥{price:g}", "ok")
        return len(records)

    def _store_item(self, pc, model, title, price, url):
        """Persist one accepted item immediately and publish its progress."""
        return self._store_items(pc, model, [(title, price, url)])

    def _fetch_model(self, pc, model, cfg=None):
        self._last_transport_note = ""
        kws = [model["name"].replace(" ", "")]
        cfg = cfg or get_settings()
        mode = cfg.get("crawl_mode", "browser")
        policy = FilterPolicy.from_settings(cfg)
        items = []
        seen = set()
        streamed_urls = set()
        def on_batch(batch):
            return self._stream_store_batch(pc, model, batch, streamed_urls, policy)
        for kw in kws:
            if not self._running:
                break
            self._pause_event.wait()
            order = self._transport_order(mode, pc)
            for transport in order:
                try:
                    if transport == "browser":
                        batch = self._collect_via(pc, kw, transport, on_batch=on_batch)
                    else:
                        batch = self._collect_via(pc, kw, transport)
                except Exception as e:
                    db.set_state("last_error", f"{pc.name} {transport} {kw}: {e}")
                    batch = []
                if batch:
                    for it in batch:
                        if not it or it[2] in seen:
                            continue
                        seen.add(it[2])
                        items.append(it)
                    break   # 命中一个通道就够，不再降级

        if items:
            kept_list, stats = self._filter_items(model, items, policy)
            fresh = []
            for title, price, url in kept_list:
                key = url or f"{title}\x00{price}"
                if key not in streamed_urls:
                    streamed_urls.add(key)
                    fresh.append((title, price, url))
            self._store_items(pc, model, fresh)
            db.set_state(f"debug_{pc.name}_{model['name'].replace(' ','')}",
                         f"raw={len(items)} matched={stats['matched']} valid={stats['valid']} "
                         f"stored={len(streamed_urls)} med={round(stats['median'],0)} "
                         f"filtered_content={stats['content']} filtered_price={stats['price']}")
            self._log(f"{_PLATFORM_LABEL[pc.name]} {model['name']} 采集完成："
                      f"抓取{len(items)} 入库{len(streamed_urls)} 过滤{len(items)-len(streamed_urls)}", "ok")
        elif streamed_urls:
            # 增量回调已成功写入时，整轮超时不能反向覆盖成“无数据”。
            reason = self._last_transport_note or "完整翻页结果未返回"
            db.set_state(f"debug_{pc.name}_{model['name'].replace(' ','')}",
                         f"stored={len(streamed_urls)} partial=1 note={reason}")
            self._log(f"{_PLATFORM_LABEL[pc.name]} {model['name']} 已完成增量入库："
                      f"{len(streamed_urls)}条（整轮提示：{reason}）", "ok")
        else:
            # 未登录或采集失败：不生成任何演示数据（宁缺毋滥，保证数据全部真实）
            reason = self._last_transport_note or "未返回商品"
            db.set_state(f"debug_{pc.name}_{model['name'].replace(' ','')}",
                         f"未采集到数据 mode={mode} reason={reason}")
            self._log(f"{_PLATFORM_LABEL[pc.name]} {model['name']} 无数据：{reason}", "warn")
        self._stop_event.wait(random.uniform(MIN_DELAY, MAX_DELAY))


from gpus import GPU_MODELS as _MODELS  # noqa: E402

crawler = Crawler(headless=True)   # 采集循环控制；浏览器 headless 由 manager/BrowserManager 决定
