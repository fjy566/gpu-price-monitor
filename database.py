# -*- coding: utf-8 -*-
"""SQLite 存储层：价格记录 + 爬取状态。"""
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.db")
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, closing(_conn()) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                series TEXT NOT NULL,
                generation INTEGER,
                platform TEXT NOT NULL,
                title TEXT,
                price REAL NOT NULL,
                url TEXT,
                ts TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                series TEXT NOT NULL,
                generation INTEGER,
                platform TEXT NOT NULL,
                title TEXT,
                price REAL NOT NULL,
                url TEXT,
                ts TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # price 表按 url 唯一索引，保证入库去重。
        # 先清理历史遗留的重复行（保留每个 url 最新一条，迁移到 price_history），否则唯一索引会创建失败。
        try:
            c.execute("""
                INSERT INTO price_history(model,series,generation,platform,title,price,url,ts)
                SELECT model,series,generation,platform,title,price,url,ts FROM prices
                WHERE id IN (
                    SELECT id FROM prices p WHERE url!='' AND id NOT IN (
                        SELECT MAX(id) FROM prices WHERE url!='' GROUP BY url
                    )
                )
            """)
            c.execute("""
                DELETE FROM prices WHERE id IN (
                    SELECT id FROM prices p WHERE url!='' AND id NOT IN (
                        SELECT MAX(id) FROM prices WHERE url!='' GROUP BY url
                    )
                )
            """)
        except Exception:
            pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_prices_url ON prices(url) WHERE url != ''")
        # 历史表按“商品链接 + 自然日”保留一个快照；先清理旧版本在同一天写入的重复记录。
        c.execute("""
            DELETE FROM price_history
            WHERE url != '' AND id NOT IN (
                SELECT MAX(id) FROM price_history
                WHERE url != '' GROUP BY url, substr(ts, 1, 10)
            )
        """)
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_history_url_day
            ON price_history(url, substr(ts, 1, 10)) WHERE url != ''
        """)
        c.commit()


def set_state(key, value):
    with _lock, closing(_conn()) as c:
        c.execute("INSERT INTO state(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        c.commit()


def get_state(key, default=None):
    with _lock, closing(_conn()) as c:
        r = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def add_price(model, series, generation, platform, title, price, url):
    """写入价格。去重策略（UPSERT）：
    - prices 表：按 url 唯一，同一商品重复采集时【更新】价格/标题/时间，不新增行，保证不冗余。
    - price_history 表：同一商品同一天只保留一个最新快照；跨天则新增日快照。
    """
    # 微秒精度保证同一秒内多次刷新也能正确判断“最新”。旧的秒级时间戳仍可排序/解析。
    ts = datetime.now().isoformat(sep=" ", timespec="microseconds")
    with _lock, closing(_conn()) as c:
        old = None
        if url:
            row = c.execute("SELECT id, price FROM prices WHERE url=?", (url,)).fetchone()
            if row:
                old = {"id": row["id"], "price": row["price"]}
        if url and old:
            c.execute("""UPDATE prices SET model=?, series=?, generation=?, platform=?,
                        title=?, price=?, ts=? WHERE id=?""",
                      (model, series, generation, platform, title, price, ts, old["id"]))
        else:
            c.execute(
                "INSERT INTO prices(model,series,generation,platform,title,price,url,ts) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (model, series, generation, platform, title, price, url, ts),
            )
        day = ts[:10]
        if url:
            daily = c.execute(
                "SELECT id FROM price_history WHERE url=? AND substr(ts,1,10)=? "
                "ORDER BY ts DESC LIMIT 1", (url, day),
            ).fetchone()
        else:
            daily = c.execute(
                "SELECT id FROM price_history WHERE model=? AND platform=? AND title=? "
                "AND substr(ts,1,10)=? ORDER BY ts DESC LIMIT 1",
                (model, platform, title, day),
            ).fetchone()
        if daily:
            c.execute("""UPDATE price_history SET model=?, series=?, generation=?, platform=?,
                        title=?, price=?, url=?, ts=? WHERE id=?""",
                      (model, series, generation, platform, title, price, url, ts, daily["id"]))
        else:
            c.execute(
                "INSERT INTO price_history(model,series,generation,platform,title,price,url,ts) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (model, series, generation, platform, title, price, url, ts),
            )
        c.commit()


def reclassify_5090_rows(resolver):
    """把旧版本误归入 RTX 5090 的 D / D V2 商品迁移到精确型号。"""
    with _lock, closing(_conn()) as c:
        changed = 0
        for table in ("prices", "price_history"):
            rows = c.execute(
                f"SELECT id,title,model FROM {table} WHERE lower(title) LIKE '%5090%'"
            ).fetchall()
            for row in rows:
                precise = resolver(row["title"] or "")
                if precise and precise != row["model"]:
                    c.execute(
                        f"UPDATE {table} SET model=?, series='RTX 50 系', generation=50 WHERE id=?",
                        (precise, row["id"]),
                    )
                    changed += 1
        c.commit()
        return changed


def purge_legacy_noise(rejector, abs_min, low_ratio, high_ratio):
    """一次性清理旧版本已入库的明显引流/风险商品及极端价格离群项。"""
    migration_key = "migration_catalog_filter_v3"
    if get_state(migration_key):
        return 0
    import statistics
    with _lock, closing(_conn()) as c:
        rows = [dict(row) for row in c.execute("SELECT * FROM prices").fetchall()]
        groups = {}
        for row in rows:
            if rejector(row.get("title") or "") or float(row["price"]) < abs_min:
                continue
            groups.setdefault((row["model"], row["platform"]), []).append(float(row["price"]))
        rejected = []
        for row in rows:
            reason = rejector(row.get("title") or "")
            prices = groups.get((row["model"], row["platform"]), [])
            median = statistics.median(prices) if prices else 0
            price = float(row["price"])
            price_noise = price < abs_min or (
                median > 0 and (price < max(abs_min, median * low_ratio) or price > median * high_ratio)
            )
            if reason or price_noise:
                rejected.append(row)
        for row in rejected:
            if row.get("url"):
                c.execute("DELETE FROM price_history WHERE url=?", (row["url"],))
            else:
                c.execute("DELETE FROM price_history WHERE model=? AND platform=? AND title=?",
                          (row["model"], row["platform"], row.get("title") or ""))
            c.execute("DELETE FROM prices WHERE id=?", (row["id"],))
        c.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (migration_key, str(len(rejected))))
        c.commit()
        return len(rejected)


def latest_prices(limit_groups=True, platform=None):
    """返回每个 (model, platform) 的最新一条价格。"""
    with _lock, closing(_conn()) as c:
        sql = """
            SELECT p.* FROM prices p
            WHERE NOT EXISTS (
                SELECT 1 FROM prices newer
                WHERE newer.model = p.model AND newer.platform = p.platform
                  AND (newer.ts > p.ts OR (newer.ts = p.ts AND newer.id > p.id))
            )
        """
        params = []
        if platform:
            sql += " AND p.platform = ?"
            params.append(platform)
        sql += " ORDER BY p.generation DESC, p.model, p.platform"
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def distinct_items(include_demo=False, platform=None):
    """返回所有【去重后的真实商品】按型号归类（同一条链接只保留最新一条），
    而非仅一个平台一条。用于展示"价格合适的都保留，按型号归类"。"""
    with _lock, closing(_conn()) as c:
        clauses = []
        params = []
        if not include_demo:
            clauses.append("title NOT LIKE '%演示%'")
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = c.execute(f"""
            SELECT * FROM prices
            {where}
            ORDER BY generation DESC, model, price ASC
        """, params).fetchall()
        return [dict(r) for r in rows]


def all_prices(platform=None):
    with _lock, closing(_conn()) as c:
        if platform:
            rows = c.execute("SELECT * FROM prices WHERE platform=? ORDER BY ts", (platform,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM prices ORDER BY ts").fetchall()
        return [dict(r) for r in rows]


def current_price_count(platform=None):
    """返回当前去重商品数，供状态卡片使用。"""
    with _lock, closing(_conn()) as c:
        sql = "SELECT COUNT(*) AS n FROM prices WHERE title NOT LIKE '%演示%'"
        params = []
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        row = c.execute(sql, params).fetchone()
        return int(row["n"] if row else 0)


def clear_price_data():
    """原子清理当前价格与历史快照，保留 state 中的配置。"""
    with _lock, closing(_conn()) as c:
        c.execute("DELETE FROM prices")
        c.execute("DELETE FROM price_history")
        c.commit()


def history(model, platform=None):
    """返回某型号的价格历史（含每次价格变化的快照），用于走势图。"""
    with _lock, closing(_conn()) as c:
        if platform:
            rows = c.execute(
                "SELECT * FROM price_history WHERE model=? AND platform=? ORDER BY ts",
                (model, platform),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM price_history WHERE model=? ORDER BY ts", (model,)
            ).fetchall()
        return [dict(r) for r in rows]
