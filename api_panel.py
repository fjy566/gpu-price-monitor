# -*- coding: utf-8 -*-
"""
官方开放平台 API 集成框架（合规、稳定、高效、零ban风险）。

【说明】
- 这是各平台官方 API 的接入框架。官方 API 是最合规高效的数据源。
- 需要你到各平台开放平台申请 appkey/secret 后填入（见下）。
- 各平台的真实签名规则较复杂，这里提供清晰的接入点，
  按开放平台文档把签名/请求补全即可（我可以在你提供文档后帮你写具体调用）。
- 一律【不绕过任何反爬】；官方 API 本身就是合规通道。

申请入口（对应 gpus.PLATFORM_API_DOC，已更新：闲鱼走阿里开放平台）：
  京东:   https://union.jd.com/                       （京东联盟，个人可申请）
  淘宝:   https://open.taobao.com/                    （淘宝开放平台 top api）
  拼多多: https://open.pinduoduo.com/                 （拼多多开放平台）
  闲鱼:   阿里巴巴开放平台 open.taobao.com / access.taobao.com（阿里系开放平台，闲鱼相关接口需申请对应类目权限）

  说明：闲鱼作为阿里系产品，其开放能力通过【阿里巴巴/淘宝开放平台】体系提供（同一套 top-api/oauth 体系）。
  具体可调用的闲鱼接口（如闲鱼商品/交易/ISV 应用接口）需在开放平台申请对应 APP_KEY 与类目权限，
  不同类目开放的商品搜索/详情能力不同，请以你申请到的权限为准。
"""

import time

import requests

import database as db

# 平台 key -> 配置字段名（存储键统一用字段名，如 jd_appkey）
API_FIELDS = [
    "goofish_appkey", "goofish_secret",
    "jd_appkey", "jd_secret",
    "taobao_appkey", "taobao_secret",
    "pdd_client_id", "pdd_secret",
]
SECRET_FIELDS = {"goofish_secret", "jd_secret", "taobao_secret", "pdd_secret"}
PLATFORM_FIELDS = {
    "goofish": ("goofish_appkey", "goofish_secret"),
    "jd": ("jd_appkey", "jd_secret"),
    "taobao": ("taobao_appkey", "taobao_secret"),
    "pdd": ("pdd_client_id", "pdd_secret"),
}
# 其余三个入口仍是明确的占位实现，不能把“保存了凭据”误报为“API 可用”。
API_IMPLEMENTED = {"jd"}


def save_api_config(cfg: dict) -> dict:
    """保存各平台 API key/secret 配置（存于 state 表，键为字段名）。"""
    for k, v in (cfg or {}).items():
        if k in API_FIELDS:
            if v is None:
                continue
            value = str(v).strip()
            # 前端不会回显凭据；空白提交表示保留现值，避免一次保存擦掉 secret。
            if value:
                db.set_state("api_" + k, value)
    return public_api_config()


def get_api_config() -> dict:
    """读取已保存的 API 配置。"""
    out = {}
    for k in API_FIELDS:
        out[k] = db.get_state("api_" + k, "")
    return out


def public_api_config() -> dict:
    """只返回配置状态，不把 appkey/secret 明文送回浏览器。"""
    raw = get_api_config()
    return {
        "config": {field: "" for field in API_FIELDS},
        "configured_fields": sorted(field for field, value in raw.items() if value),
        "saved": sorted(
            platform for platform, fields in PLATFORM_FIELDS.items()
            if any(raw.get(field) for field in fields)
        ),
        "ready": sorted(configured_platforms()),
        "implemented": sorted(API_IMPLEMENTED),
    }


def configured_platforms() -> set:
    """返回已配置好 appkey、可走官方API的平台集合。"""
    cfg = get_api_config()
    ready = set()
    for plat, fields in PLATFORM_FIELDS.items():
        if plat in API_IMPLEMENTED and all(cfg.get(field) for field in fields):
            ready.add(plat)
    return ready


# ----------------------------------------------------------------------
# 各平台官方 API 调用入口（需按开放平台文档补全签名/请求）
# 返回 [(title, price, url)]，与浏览器/常规爬虫同一格式
# ----------------------------------------------------------------------
def _jd_sign(params, secret):
    """京东开放平台签名（统一规范）：
    sign = MD5(app_key + secretKey + 按key字典序拼接的业务参数 + timestamp + v) 转大写。
    注：京东/各联盟的真实签名拼接规则以开放平台最新文档为准，这里为框架实现。"""
    import hashlib
    biz = params.get("360buy_param_json", "{}")
    try:
        import json as _json
        biz_obj = _json.loads(biz)
        biz_str = "".join(f"{k}{biz_obj[k]}" for k in sorted(biz_obj.keys()))
    except Exception:
        biz_str = biz
    raw = params.get("app_key", "")
    to_sign = raw + secret + biz_str + params.get("timestamp", "") + params.get("v", "2.0")
    return hashlib.md5(to_sign.encode("utf-8")).hexdigest().upper()


def fetch_jd_api(kw, appkey, secret):
    """京东联盟商品搜索接口（jd.union.open.goods.query，open-api.jd.com）。
    说明：需要 appKey + secretKey（联盟后台可见）。若 secret 为空则无法签名，返回空并提示。
    """
    import json as _json
    if not secret:
        return ["need_secret"]   # 缺 secretKey 标记，供上层提示
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    method = "jd.union.open.goods.query"
    biz = _json.dumps({"eliteId": 1, "pageIndex": 1, "pageSize": 20, "goodsReqDTO": {"keyword": kw}}, ensure_ascii=False)
    params = {
        "app_key": appkey,
        "method": method,
        "360buy_param_json": biz,
        "timestamp": timestamp,
        "v": "2.0",
        "format": "json",
    }
    try:
        params["sign"] = _jd_sign(params, secret)
    except Exception:
        return []
    try:
        r = requests.post("https://open-api.jd.com/routerjson", data=params, timeout=20,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    # 解析京东联盟返回结构
    items = []
    try:
        resp = data.get("jd_union_open_goods_query_responce") or {}
        result_json = resp.get("queryResult", "")
        import json as _j
        result = _j.loads(result_json) if isinstance(result_json, str) else result_json
        for g in (result.get("data", []) or []):
            title = g.get("skuName", "")
            price = g.get("price", 0) or g.get("wlCommissionPrice", 0)
            sku = g.get("skuId", "")
            if title and price and sku:
                items.append((title, float(price), f"https://item.jd.com/{sku}.html"))
    except Exception:
        return []
    return items


def fetch_taobao_api(kw, appkey, secret):
    """淘宝 top 商品搜索接口（taobao.tbk.item.get / 淘宝客）。
    TODO：按淘宝开放平台文档签名（MD5+HmacSHA256），调用后解析。"""
    return []


def fetch_pdd_api(kw, client_id, secret):
    """拼多多商品搜索接口（pdd.goods.search）。
    TODO：按拼多多开放平台文档签名（MD5），调用后解析。"""
    return []


def fetch_goofish_api(kw, appkey, secret):
    """闲鱼商品接口（通过阿里巴巴开放平台 top-api 体系）。
    真实调用：按阿里开放平台文档走 top 协议（appkey/secret 签名 + oauth），
    调用你申请的闲鱼/淘宝开放类目中可用的商品搜索接口（具体方法名以你申请到的权限为准），
    解析返回的商品列表。
    TODO(你申请到 appkey 与类目权限后，我按你权限对应的文档写具体调用)：这里先返回空。"""
    return []


def fetch_api_all(platform, kw):
    """统一入口：根据平台读取API配置并调用。返回商品列表或空。"""
    cfg = get_api_config()
    if platform == "jd" and cfg.get("jd_appkey"):
        return fetch_jd_api(kw, cfg["jd_appkey"], cfg.get("jd_secret", ""))
    if platform == "taobao" and cfg.get("taobao_appkey"):
        return fetch_taobao_api(kw, cfg["taobao_appkey"], cfg.get("taobao_secret", ""))
    if platform == "goofish" and cfg.get("goofish_appkey"):
        return fetch_goofish_api(kw, cfg["goofish_appkey"], cfg.get("goofish_secret", ""))
    if platform == "pdd" and cfg.get("pdd_client_id"):
        return fetch_pdd_api(kw, cfg["pdd_client_id"], cfg.get("pdd_secret", ""))
    return []
