# -*- coding: utf-8 -*-
"""RTX 30/40/50 系显卡型号清单与归类。"""

import re
import threading

_COMPACT_RE = re.compile(r"[^a-z0-9]+")
_RTX_MODEL_RE = re.compile(r"rtx(\d{4})")
_RX_MODEL_RE = re.compile(r"rx(\d{4})")
_MOBILE_SUFFIX_RE = re.compile(r"\d{3,4}\s?m\b")
_ACCESSORY_ONLY_RE = re.compile(r"只出.{0,24}(散热器|风扇|背板|外壳|空盒|包装盒|支架)")

# 每个型号：名称、系列代号(用于归类)、常用关键词(用于匹配商品标题)
GPU_MODELS = [
    # ---------- 50 系 (RTX 50) ----------
    {"name": "RTX 5090 D V2", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5090dv2", "rtx 5090 d v2", "rtx5090 v2"]},
    {"name": "RTX 5090 D", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5090d", "rtx 5090 d"]},
    {"name": "RTX 5090", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5090", "rtx 5090"]},
    {"name": "RTX 5080", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5080", "rtx 5080"]},
    {"name": "RTX 5070 Ti", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5070ti", "rtx 5070 ti", "rtx5070 ti"]},
    {"name": "RTX 5070", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5070", "rtx 5070"]},
    {"name": "RTX 5060 Ti", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5060ti", "rtx 5060 ti"]},
    {"name": "RTX 5060", "series": "RTX 50 系", "generation": 50, "keywords": ["rtx5060", "rtx 5060"]},

    # ---------- 40 系 (RTX 40) ----------
    {"name": "RTX 4090", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4090", "rtx 4090"]},
    {"name": "RTX 4080 Super", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4080 super", "rtx 4080 super"]},
    {"name": "RTX 4080", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4080", "rtx 4080"]},
    {"name": "RTX 4070 Ti Super", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4070 ti super", "rtx4070tisuper"]},
    {"name": "RTX 4070 Ti", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4070 ti", "rtx4070ti"]},
    {"name": "RTX 4070 Super", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4070 super", "rtx4070super"]},
    {"name": "RTX 4070", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4070", "rtx 4070"]},
    {"name": "RTX 4060 Ti", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4060 ti", "rtx4060ti"]},
    {"name": "RTX 4060", "series": "RTX 40 系", "generation": 40, "keywords": ["rtx4060", "rtx 4060"]},

    # ---------- 30 系 (RTX 30) ----------
    {"name": "RTX 3090 Ti", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3090 ti", "rtx3090ti"]},
    {"name": "RTX 3090", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3090", "rtx 3090"]},
    {"name": "RTX 3080 Ti", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3080 ti", "rtx3080ti"]},
    {"name": "RTX 3080", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3080", "rtx 3080"]},
    {"name": "RTX 3070 Ti", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3070 ti", "rtx3070ti"]},
    {"name": "RTX 3070", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3070", "rtx 3070"]},
    {"name": "RTX 3060 Ti", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3060 ti", "rtx3060ti"]},
    {"name": "RTX 3060", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3060", "rtx 3060"]},
    {"name": "RTX 3050", "series": "RTX 30 系", "generation": 30, "keywords": ["rtx3050", "rtx 3050"]},
]

# 平台列表及对应的内置"演示数据价格档位"（仅当无 API key 时用于演示，避免高频直采）
PLATFORMS = ["闲鱼", "京东", "淘宝", "拼多多"]
PLATFORM_SEARCH_URL = {
    "闲鱼": "https://www.goofish.com/search?q={kw}",
    "京东": "https://search.jd.com/Search?keyword={kw}",
    "淘宝": "https://s.taobao.com/search?q={kw}",
    "拼多多": "https://mobile.yangkeduo.com/search_result.html?search_key={kw}",
}

# 每个平台的官方开放平台申请入口（合规路径）
PLATFORM_API_DOC = {
    "京东": "https://union.jd.com/ (京东联盟开放平台，申请 appkey)",
    "淘宝": "https://open.taobao.com/ (淘宝开放平台，申请 top appkey)",
    "拼多多": "https://open.pinduoduo.com/ (拼多多开放平台，申请 appkey)",
    "闲鱼": "https://open.taobao.com/ (闲鱼集成在阿里开放平台——与淘宝同属阿里巴巴 top-api 体系，申请闲鱼类目 appkey 与权限)",
}


def classify(name: str) -> dict:
    """按名称返回归类信息。"""
    normalized = _compact(name)
    variant = _detect_5090_variant(normalized)
    if variant:
        return {"name": variant, "series": "RTX 50 系", "generation": 50}
    matches = [gpu for token, gpu in _MODEL_TOKEN_ROWS if token in normalized]
    if matches:
        g = max(matches, key=lambda item: len(_compact(item["name"])))
        return {"name": g["name"], "series": g["series"], "generation": g["generation"]}
    return infer_model(name)


def _compact(value: str) -> str:
    """把型号文本规整为仅含字母数字的形式，兼容空格和连字符差异。"""
    return _COMPACT_RE.sub("", (value or "").casefold())


_MODEL_BY_FOLDED_NAME = {gpu["name"].casefold(): gpu for gpu in GPU_MODELS}
_MODEL_TOKEN_ROWS = tuple(
    sorted(((_compact(gpu["name"]), gpu) for gpu in GPU_MODELS),
           key=lambda row: len(row[0]), reverse=True)
)
_BUILTIN_NAMES = frozenset(gpu["name"] for gpu in GPU_MODELS)
_BUILTIN_FOLDED_NAMES = frozenset(name.casefold() for name in _BUILTIN_NAMES)


def _detect_5090_variant(compact_text: str):
    """从已压缩文本中识别互斥的 5090 / 5090D / 5090D V2 变体。"""
    if "5090" not in compact_text:
        return None
    # 市场标题常写成“5090D V2 / 5090DV2 / 5090 V2版”；V2 统一归入 D V2。
    if "v2" in compact_text:
        return "RTX 5090 D V2"
    if re.search(r"5090d(?![a-z0-9])", compact_text) or "rtx5090d" in compact_text:
        return "RTX 5090 D"
    if "rtx5090" in compact_text or re.search(r"(^|[^0-9])5090([^0-9]|$)", compact_text):
        return "RTX 5090"
    return None


def detect_5090_variant(value: str):
    """公开的 5090 变体识别接口，供历史数据迁移使用。"""
    return _detect_5090_variant(_compact(value))


def title_matches_model(title: str, model_name: str) -> bool:
    """检查商品标题是否属于目标型号。
    1) 标题必须命中目标型号关键词（或至少出现 RTX 系列字样）
    2) 标题不能出现与目标型号相冲突的其它显卡型号（避免混入，如搜5070混进5060Ti）
    """
    compact_title = _compact(title)
    if not compact_title:
        return False
    target = _MODEL_BY_FOLDED_NAME.get((model_name or "").casefold())
    if not target:
        # 自定义型号不能“默认放行”，必须真的出现在商品标题中。
        custom = _compact(model_name)
        return len(custom) >= 4 and custom in compact_title

    if target["name"] in {"RTX 5090", "RTX 5090 D", "RTX 5090 D V2"}:
        return _detect_5090_variant(compact_title) == target["name"]

    # 找出标题中所有内置型号，再移除被更具体变体包含的基础型号。
    candidates = []
    for token, gpu in _MODEL_TOKEN_ROWS:
        if token and token in compact_title:
            candidates.append((gpu, token))
    specific = []
    for gpu, token in candidates:
        if any(token != other and other.startswith(token) for _, other in candidates):
            continue
        specific.append(gpu["name"])
    return len(set(specific)) == 1 and specific[0] == target["name"]


_NON_PRODUCT_PATTERNS = (
    "空盒", "只卖盒", "单出盒", "显卡支架", "显卡模型", "水冷头", "冷头",
    "散热器单出", "风扇单出", "显卡风扇", "背板单出", "挡板", "延长线",
    "供电线", "转接线", "钥匙扣", "贴纸", "摆件", "核心单出", "显存颗粒",
)
_BAIT_PATTERNS = (
    "定金", "订金", "预付款", "租赁", "出租", "求购", "收购", "回收",
    "标价非售价", "标价不实", "价格随便标", "链接价", "咨询价", "价格私聊",
    "拍下不发", "拍下不卖", "勿拍", "仅展示", "不卖", "打价贴",
    "发不了", "无法发货", "不能发货", "不发货",
)
_RISK_PATTERNS = (
    "假卡", "仿品", "高仿", "魔改", "改卡", "刷bios", "移动芯片", "笔记本芯片",
    "工程样品", "es版", "故障卡", "坏卡", "尸体卡", "报废", "不能用", "无法使用",
    "维修过", "修过显存", "修过核心", "核心维修", "显存维修",
)


def listing_rejection_reason(title: str):
    """返回明显非整卡、低价引流或高风险商品的拒绝原因；可接受时返回空字符串。"""
    text = (title or "").casefold().replace(" ", "")
    if not text:
        return "标题为空"
    for marker in _NON_PRODUCT_PATTERNS:
        if marker in text:
            return f"非整卡商品：{marker}"
    if _ACCESSORY_ONLY_RE.search(text):
        return "非整卡商品：只出售配件"
    for marker in _BAIT_PATTERNS:
        if marker in text:
            return f"疑似引流标价：{marker}"
    for marker in _RISK_PATTERNS:
        if marker in text:
            return f"疑似赝品或故障商品：{marker}"
    return ""


MOBILE_MARKERS = [
    "mobile", "笔记本", "笔记本显卡", "laptop", "notebook",
    "gtx1650m", "rtx3060m", "rtx3070m", "rtx4060m", "rtx4070m", "rtx4080m", "rtx4090m",
    "笔记本用", "游戏本", "移动版", "移动端",
]


def is_desktop_gpu(title: str) -> bool:
    """判断商品是否为【桌面版】显卡（True=桌面，False=笔记本/Mobile/移动版）。
    标题中出现 "m 后缀"（如 4080m / 4090m）或笔记本/Mobile/移动版字样则判定为移动版。"""
    t = (title or "").lower()
    if not t:
        return True
    # 常见移动版后缀：型号数字后紧跟小写 m（如 4080m、4060 m）
    if _MOBILE_SUFFIX_RE.search(t):
        return False
    for marker in MOBILE_MARKERS:
        if marker in t:
            return False
    # 移动版特有显存/规格提示
    if "max-q" in t or "maxq" in t or "ti laptop" in t:
        return False
    return True


# ======================================================================
# 用户自定义型号：存于全局配置（state 表），与内置型号合并使用
# ======================================================================
import database as _db  # noqa: E402

CUSTOM_KEY = "custom_models"  # state 表键：存储 JSON 列表，如 ["RX 7900 XTX", "RTX 4090Ti"]
HIDDEN_KEY = "hidden_builtin_models"  # 仅隐藏内置型号，不删除内置定义
_model_config_lock = threading.Lock()
_model_config_cache = {}
_model_config_generation = 0


def _load_model_config():
    import json
    while True:
        identity = _db.DB_PATH
        with _model_config_lock:
            cached = _model_config_cache.get(identity)
            if cached is not None:
                return list(cached[0]), set(cached[1])
            generation = _model_config_generation
        raw = _db.get_states({CUSTOM_KEY: "[]", HIDDEN_KEY: "[]"})
        try:
            custom_values = json.loads(raw[CUSTOM_KEY])
            custom = [value for value in custom_values if isinstance(value, str) and value.strip()]
        except Exception:
            custom = []
        try:
            hidden_values = json.loads(raw[HIDDEN_KEY])
            hidden = {value for value in hidden_values
                      if isinstance(value, str) and value in _BUILTIN_NAMES}
        except Exception:
            hidden = set()
        with _model_config_lock:
            if generation != _model_config_generation:
                continue
            _model_config_cache[identity] = (tuple(custom), frozenset(hidden))
            return custom, hidden


def _invalidate_model_config():
    global _model_config_generation
    with _model_config_lock:
        _model_config_cache.pop(_db.DB_PATH, None)
        _model_config_generation += 1


def _load_custom():
    return _load_model_config()[0]


def _save_custom(lst):
    import json
    _db.set_state(CUSTOM_KEY, json.dumps(lst, ensure_ascii=False))
    _invalidate_model_config()


def _load_hidden():
    return _load_model_config()[1]


def _save_hidden(values):
    import json
    valid = sorted(set(values) & _BUILTIN_NAMES)
    _db.set_state(HIDDEN_KEY, json.dumps(valid, ensure_ascii=False))
    _invalidate_model_config()


def infer_model(name: str) -> dict:
    """根据型号名推断系列与代次（无法识别则归为"其他"）。"""
    clean = (name or "").strip()
    compact = _compact(clean)
    rtx = _RTX_MODEL_RE.search(compact)
    if rtx:
        generation = int(rtx.group(1)[:2])
        if generation in (30, 40, 50):
            return {"name": clean, "series": f"RTX {generation} 系", "generation": generation}
    rx = _RX_MODEL_RE.search(compact)
    if rx:
        family = int(rx.group(1)[0]) * 1000
        return {"name": clean, "series": f"RX {family} 系", "generation": family // 100}
    return {"name": clean, "series": "其他", "generation": 0}


def get_all_models():
    """返回当前可见的内置 + 用户自定义型号列表。隐藏内置型号由恢复列表单独返回。"""
    hidden = _load_hidden()
    builtin = [{"name": g["name"], "series": g["series"], "generation": g["generation"],
                "custom": False} for g in GPU_MODELS if g["name"] not in hidden]
    custom = [dict(infer_model(n), custom=True) for n in _load_custom()]
    # 去重（按名称，内置优先）
    seen = {g["name"] for g in builtin}
    for c in custom:
        if c["name"] not in seen:
            builtin.append(c)
            seen.add(c["name"])
    return builtin


def get_hidden_builtin_models():
    """返回可恢复的内置型号，不影响内置清单本身。"""
    hidden = _load_hidden()
    return [{"name": g["name"], "series": g["series"], "generation": g["generation"],
             "custom": False, "hidden": True} for g in GPU_MODELS if g["name"] in hidden]


def hide_builtin_model(name: str):
    name = (name or "").strip()
    gpu = _MODEL_BY_FOLDED_NAME.get(name.casefold())
    match = gpu["name"] if gpu else None
    if not match:
        return False, "只能隐藏内置型号"
    hidden = _load_hidden()
    if match in hidden:
        return False, "该型号已经隐藏"
    hidden.add(match)
    _save_hidden(hidden)
    return True, f"已隐藏型号 {match}"


def restore_builtin_model(name: str):
    name = (name or "").strip()
    hidden = _load_hidden()
    match = next((value for value in hidden if value.casefold() == name.casefold()), None)
    if not match:
        return False, "该型号未隐藏或不存在"
    hidden.remove(match)
    _save_hidden(hidden)
    return True, f"已恢复型号 {match}"


def add_custom_model(name: str):
    """添加一个自定义型号。返回 (ok, msg)。"""
    name = (name or "").strip()
    if not name:
        return False, "型号名不能为空"
    if len(name) > 40:
        return False, "型号名过长"
    custom = _load_custom()
    folded = name.casefold()
    if any(existing.casefold() == folded for existing in custom):
        return False, "型号已存在"
    if folded in _BUILTIN_FOLDED_NAMES:
        return False, "该型号已在内置列表中"
    custom.append(name)
    _save_custom(custom)
    return True, f"已添加型号 {name}"


def remove_custom_model(name: str):
    custom = _load_custom()
    folded = (name or "").strip().casefold()
    match = next((value for value in custom if value.casefold() == folded), None)
    if not match:
        return False, "该型号不是自定义型号或不存在"
    custom.remove(match)
    _save_custom(custom)
    return True, f"已删除型号 {match}"
