const $ = (id) => document.getElementById(id);

const platformLabel = { goofish: "闲鱼" };
const platformIcon = { goofish: "XY" };
const seriesClass = {
  "RTX 50 系": "s50", "RTX 40 系": "s40", "RTX 30 系": "s30",
  "RX 9000 系": "srx", "RX 7000 系": "srx",
};

let allData = [];
let allModels = [];
let hiddenModels = [];
let currentStatus = { status: "stopped" };
let loginState = [];
let settingsCache = {};
let statsCache = {};
let watchedModels = new Set();
let selectedModels = new Set();
let selectedPlatforms = new Set();
let pricePage = 1;
let pricePageSize = 20;
let dataSignature = "";
let dataRevision = -1;
let fastBusy = false;
let slowBusy = false;
let modalPreviousFocus = null;

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function placeholder(message) {
  return node("div", "placeholder", message);
}

function safeHttpUrl(value) {
  try {
    const parsed = new URL(String(value || ""), window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch (_) {
    return null;
  }
}

function safeChartPath(value) {
  const path = String(value || "");
  return /^charts\/[A-Za-z0-9_.-]+\.png$/.test(path) ? path : null;
}

async function api(path, method = "GET", body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new Error("无法连接本地服务，请确认程序仍在运行");
  }
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    throw new Error(`服务返回了无法识别的响应（HTTP ${response.status}）`);
  }
  const payload = await response.json();
  if (!response.ok || payload?.ok === false) {
    const error = new Error(payload?.msg || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function toast(message, type = "info") {
  const item = node("div", `toast ${type}`, message);
  $("toast-wrap").appendChild(item);
  window.setTimeout(() => {
    item.style.opacity = "0";
    window.setTimeout(() => item.remove(), 220);
  }, 3600);
}

async function withBusy(buttonOrId, task) {
  const button = typeof buttonOrId === "string" ? $(buttonOrId) : buttonOrId;
  if (button?.classList.contains("is-busy")) return null;
  const oldDisabled = button?.disabled;
  if (button) { button.disabled = true; button.classList.add("is-busy"); }
  try {
    return await task();
  } finally {
    if (button) { button.disabled = Boolean(oldDisabled); button.classList.remove("is-busy"); }
    refreshStatusUI();
  }
}

function refreshStatusUI() {
  const status = currentStatus.status || "stopped";
  const map = {
    running: ["running", "采集中"], paused: ["paused", "已暂停"],
    stopped: ["stopped", "已停止"], error: ["error", "异常"],
    completed: ["running", "本轮完成"],
    browser_ready: ["stopped", "待采集"],
  };
  const [badgeClass, badgeText] = map[status] || map.stopped;
  const badge = $("stateBadge");
  badge.className = `badge ${badgeClass}`;
  badge.textContent = badgeText;

  $("btn-start").disabled = status === "running" || status === "paused";
  $("btn-pause").disabled = status !== "running";
  $("btn-resume").disabled = status !== "paused";
  $("btn-stop").disabled = status !== "running" && status !== "paused" && !currentStatus.browser_ready;
  $("statRounds").textContent = currentStatus.rounds ?? 0;
  $("error-hint").textContent = currentStatus.last_error ? `错误：${currentStatus.last_error}` : "";

  const active = status === "running" || status === "paused";
  const progress = active ? Math.max(0, Math.min(1, Number(currentStatus.progress) || 0)) : 0;
  $("progressFill").style.width = `${Math.round(progress * 100)}%`;
  $("progressTrack").classList.toggle("is-idle", !active);
  $("progressTrack").setAttribute("aria-valuenow", String(Math.round(progress * 100)));

  const live = $("liveBadge");
  const dot = live.querySelector(".dot");
  const label = live.querySelector(".label");
  const liveMap = {
    running: ["var(--green)", "实时采集中"], paused: ["var(--yellow)", "采集已暂停"],
    error: ["var(--red)", "采集异常"], stopped: ["var(--muted)", "本地待命"],
    completed: ["var(--green)", "本轮已完成"],
    browser_ready: ["var(--blue)", "浏览器就绪"],
  };
  const [color, text] = liveMap[status] || liveMap.stopped;
  dot.style.background = color;
  dot.style.boxShadow = status === "running" ? `0 0 10px ${color}` : "none";
  dot.style.animation = status === "running" ? "pulse 1.4s infinite" : "none";
  label.textContent = text;
  refreshLogin();
}

function refreshLogin() {
  const grid = $("loginGrid");
  grid.replaceChildren();
  for (const entry of loginState) {
    const item = node("div", `login-item${entry.logged_in ? " on" : ""}`);
    const info = node("div", "login-info");
    info.append(node("span", "login-icon", platformIcon[entry.platform] || "API"));
    const copy = node("div");
    copy.append(node("div", "login-name", entry.label || platformLabel[entry.platform] || entry.platform));
    copy.append(node("div", "login-status", entry.logged_in ? "已登录" : "未登录"));
    info.append(copy);
    const actions = node("div", "login-actions");
    const open = node("button", "btn secondary small", "登录");
    open.type = "button";
    open.addEventListener("click", () => withBusy(open, () => doOpenLogin(entry.platform)));
    const check = node("button", "btn ghost small", "校验");
    check.type = "button";
    check.addEventListener("click", () => withBusy(check, () => doCheckOne(entry.platform)));
    actions.append(open, check);
    item.append(info, actions);
    grid.append(item);
  }
  $("browserState").textContent = currentStatus.browser_ready ? "浏览器已启动" : "浏览器未启动";
  const mode = currentStatus.browser_mode || settingsCache.browser_mode || "minimized";
  $("browser-mode-label").textContent = mode === "visible" ? "可视化" : mode === "silent" ? "静默无头" : "最小化";
}

function openLoginModal(title, hint, imageSource) {
  closeModal();
  modalPreviousFocus = document.activeElement;
  const overlay = node("div", "modal-overlay");
  overlay.id = "loginModal";
  const dialog = node("div", "modal");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "loginModalTitle");
  const heading = node("h3", "", title);
  heading.id = "loginModalTitle";
  dialog.append(heading, node("p", "", hint));
  if (/^data:image\/(png|jpeg|webp);base64,/i.test(imageSource || "")) {
    const image = node("img");
    image.src = imageSource;
    image.alt = title;
    dialog.append(image);
  }
  const actions = node("div", "modal-actions");
  const close = node("button", "btn primary", "关闭并校验");
  close.type = "button";
  close.addEventListener("click", async () => { closeModal(); await doCheckAll(); });
  actions.append(close);
  dialog.append(actions);
  overlay.append(dialog);
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) closeModal(); });
  document.body.append(overlay);
  close.focus();
}

function closeModal() {
  $("loginModal")?.remove();
  if (modalPreviousFocus instanceof HTMLElement) modalPreviousFocus.focus();
  modalPreviousFocus = null;
}

async function doBoot() {
  toast("正在启动项目专用浏览器…");
  const result = await api("/api/control/start", "POST");
  toast(result.msg || "启动请求已提交", "success");
  window.setTimeout(refreshStatus, 1800);
}

async function doOpenLogin(platform) {
  const result = await api(`/api/login/${encodeURIComponent(platform)}`, "POST");
  if (result.qr || result.screenshot) {
    const label = platformLabel[platform] || platform;
    openLoginModal(`${label}扫码登录`, `使用手机 ${label} App 扫码，完成后关闭弹窗并校验。`, result.qr || result.screenshot);
  } else {
    toast(result.msg || "登录页已在浏览器中打开", "success");
  }
}

async function doCheckAll() {
  const result = await api("/api/control/check", "POST", {});
  if (result.platforms) {
    const map = new Map(result.platforms.map((item) => [item.name, item]));
    loginState = loginState.map((item) => map.get(item.platform) ? { platform: item.platform, ...map.get(item.platform) } : item);
  }
  await refreshStatus();
  const loggedIn = loginState.some((item) => item.logged_in);
  toast(`闲鱼：${loggedIn ? "已登录" : "未登录"}`, loggedIn ? "success" : "warn");
}

async function doCheckOne(platform) {
  await api("/api/control/check", "POST", { platform });
  await refreshStatus();
  const item = loginState.find((entry) => entry.platform === platform);
  toast(`${item?.label || platformLabel[platform]}：${item?.logged_in ? "已登录" : "未登录"}`, item?.logged_in ? "success" : "warn");
}

async function toggleBrowserMode() {
  const order = ["minimized", "visible", "silent"];
  const current = currentStatus.browser_mode || settingsCache.browser_mode || "minimized";
  const next = order[(order.indexOf(current) + 1) % order.length];
  const result = await api("/api/browser/mode", "POST", { mode: next });
  settingsCache.browser_mode = next;
  toast(result.msg || "浏览器模式已切换", "success");
  window.setTimeout(refreshStatus, 2200);
}

function applySettings(settings) {
  settingsCache = { ...settings };
  $("cfg-abs_min").value = settings.abs_min ?? 500;
  $("cfg-low_ratio").value = settings.low_ratio ?? 0.55;
  $("cfg-high_ratio").value = settings.high_ratio ?? 3;
  $("cfg-keep_min").checked = (settings.keep_min ?? "1") === "1";
  selectedPlatforms = new Set(["goofish"]);
  const visibleNames = new Set(allModels.map((model) => model.name));
  const savedModels = (settings.selected_models || "").split(",").filter((name) => visibleNames.has(name));
  selectedModels = new Set(savedModels.length ? savedModels : allModels.map((model) => model.name));
  watchedModels = new Set((settings.watched_models || "").split(",").filter(Boolean));
}

async function saveThresholds() {
  const body = {
    abs_min: Number($("cfg-abs_min").value),
    low_ratio: Number($("cfg-low_ratio").value),
    high_ratio: Number($("cfg-high_ratio").value),
    keep_min: $("cfg-keep_min").checked ? "1" : "0",
  };
  const result = await api("/api/settings", "POST", body);
  settingsCache = { ...settingsCache, ...result.settings };
  $("cfg-abs_min").value = settingsCache.abs_min;
  $("cfg-low_ratio").value = settingsCache.low_ratio;
  $("cfg-high_ratio").value = settingsCache.high_ratio;
  $("cfg-keep_min").checked = settingsCache.keep_min === "1";
  toast("过滤阈值已保存", "success");
}

async function resetThresholds() {
  $("cfg-abs_min").value = 500;
  $("cfg-low_ratio").value = 0.55;
  $("cfg-high_ratio").value = 3;
  $("cfg-keep_min").checked = true;
  await saveThresholds();
}

function bindPlatformCards() {
  // 平台范围固定为闲鱼；保留函数名以兼容旧页面初始化顺序。
  selectedPlatforms = new Set(["goofish"]);
}

function bindSectionSpy() {
  const links = [...document.querySelectorAll(".quick-nav a")];
  const sections = links
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if (!("IntersectionObserver" in window) || !sections.length) return;
  links[0]?.classList.add("active");
  links[0]?.setAttribute("aria-current", "location");
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    for (const link of links) {
      const active = link.getAttribute("href") === `#${visible.target.id}`;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current");
    }
  }, { rootMargin: "-18% 0px -68% 0px", threshold: [0.1, 0.35, 0.7] });
  sections.forEach((section) => observer.observe(section));
}

function modelBadge(model) {
  if (model.series === "RTX 50 系") return ["g50", "50"];
  if (model.series === "RTX 40 系") return ["g40", "40"];
  if (model.series === "RTX 30 系") return ["g30", "30"];
  if (String(model.series).startsWith("RX ")) return ["g-rx", "RX"];
  return ["g-o", "--"];
}

function renderModelCards() {
  const grid = $("scope-models");
  grid.replaceChildren();
  const order = { "RTX 50 系": 0, "RTX 40 系": 1, "RTX 30 系": 2, "RX 9000 系": 3, "RX 7000 系": 4, "其他": 9 };
  const sorted = allModels.slice().sort((a, b) => (order[a.series] ?? 8) - (order[b.series] ?? 8) || a.name.localeCompare(b.name, "zh-CN"));
  for (const model of sorted) {
    const card = node("div", `pick-card model${selectedModels.has(model.name) ? " on" : ""}`);
    card.dataset.model = model.name;
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.value = model.name;
    checkbox.checked = selectedModels.has(model.name);
    checkbox.setAttribute("aria-label", `采集 ${model.name}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedModels.add(model.name); else selectedModels.delete(model.name);
      card.classList.toggle("on", checkbox.checked);
    });
    const [badgeClass, badgeText] = modelBadge(model);
    card.append(checkbox, node("span", `pc-badge ${badgeClass}`, badgeText), node("span", "pc-name", model.name));
    card.append(node("span", "pc-meta", model.has_data ? `${model.has_data} 个在售` : "暂无数据"));
    const actions = node("div", "model-actions");
    const star = node("button", `pc-star${watchedModels.has(model.name) ? " on" : ""}`, watchedModels.has(model.name) ? "★" : "☆");
    star.type = "button";
    star.title = watchedModels.has(model.name) ? `取消关注 ${model.name}` : `关注 ${model.name}`;
    star.setAttribute("aria-label", star.title);
    star.addEventListener("click", () => toggleWatch(model.name).catch(reportActionError));
    actions.append(star);
    if (model.custom) {
      const remove = node("button", "pc-remove model-action-label", "删除");
      remove.type = "button";
      remove.title = `删除 ${model.name}`;
      remove.setAttribute("aria-label", remove.title);
      remove.addEventListener("click", () => removeModel(model.name).catch(reportActionError));
      actions.append(remove);
      card.append(node("span", "pc-custom", "CUSTOM"));
    } else {
      const hide = node("button", "pc-remove model-action-label pc-hide", "隐藏");
      hide.type = "button";
      hide.title = `隐藏 ${model.name}`;
      hide.setAttribute("aria-label", hide.title);
      hide.addEventListener("click", () => hideModel(model.name).catch(reportActionError));
      actions.append(hide);
    }
    card.append(actions);
    grid.append(card);
  }
  renderHiddenModels();
}

function renderHiddenModels() {
  const panel = $("hidden-models-panel");
  const list = $("hidden-models");
  if (!panel || !list) return;
  list.replaceChildren();
  panel.hidden = !hiddenModels.length;
  for (const model of hiddenModels) {
    const item = node("div", "hidden-model-item");
    item.append(node("span", `hidden-model-badge ${modelBadge(model)[0]}`, modelBadge(model)[1]));
    item.append(node("span", "hidden-model-name", model.name));
    const restore = node("button", "btn ghost tiny", "恢复");
    restore.type = "button";
    restore.title = `恢复 ${model.name}`;
    restore.addEventListener("click", () => restoreModel(model.name).catch(reportActionError));
    item.append(restore);
    list.append(item);
  }
}

async function persistScope(showToast = false, allowEmptyModels = false) {
  if (!selectedPlatforms.size || (!allowEmptyModels && !selectedModels.size)) throw new Error("请至少选择一个平台和一个型号");
  const result = await api("/api/settings", "POST", {
    selected_platforms: [...selectedPlatforms].join(","),
    selected_models: [...selectedModels].join(","),
    watched_models: [...watchedModels].join(","),
  });
  settingsCache = { ...settingsCache, ...result.settings };
  if (showToast) toast("采集范围已保存", "success");
}

async function toggleWatch(modelName) {
  if (watchedModels.has(modelName)) watchedModels.delete(modelName); else watchedModels.add(modelName);
  renderModelCards();
  renderPrices();
  renderWatchSnapshot(statsCache);
  await persistScope(false);
  await refreshRecommendations();
}

async function addModel() {
  const name = $("new-model").value.trim();
  if (!name) throw new Error("请输入型号名");
  const result = await api("/api/models", "POST", { name });
  toast(result.msg || "型号已添加", "success");
  $("new-model").value = "";
  selectedModels.add(name);
  await refreshModels();
  await persistScope(false);
}

async function removeModel(name) {
  if (!window.confirm(`确定删除自定义型号“${name}”？`)) return;
  const result = await api("/api/models", "DELETE", { name });
  selectedModels.delete(name);
  watchedModels.delete(name);
  toast(result.msg || "型号已删除", "success");
  await refreshModels();
  await persistScope(false, true);
}

async function hideModel(name) {
  if (!window.confirm(`隐藏内置型号“${name}”？之后可在“已隐藏内置型号”中恢复。`)) return;
  const result = await api("/api/models", "PATCH", { name, action: "hide" });
  selectedModels.delete(name);
  watchedModels.delete(name);
  toast(result.msg || "型号已隐藏", "success");
  await refreshModels();
  await persistScope(false, true);
}

async function restoreModel(name) {
  const result = await api("/api/models", "PATCH", { name, action: "restore" });
  selectedModels.add(name);
  toast(result.msg || "型号已恢复", "success");
  await refreshModels();
  await persistScope(false, true);
}

async function refreshModels() {
  const result = await api("/api/models");
  allModels = result.models || [];
  hiddenModels = result.hidden_models || [];
  const visibleNames = new Set(allModels.map((model) => model.name));
  selectedModels = new Set([...selectedModels].filter((name) => visibleNames.has(name)));
  watchedModels = new Set([...watchedModels].filter((name) => visibleNames.has(name)));
  renderModelCards();
}

function applyPriceFilters() {
  let rows = [...allData];
  const series = $("filter-series").value;
  const query = $("filter-search").value.trim().toLocaleLowerCase("zh-CN");
  const min = Number.parseFloat($("filter-min").value);
  const max = Number.parseFloat($("filter-max").value);
  if (series) rows = rows.filter((row) => row.series === series);
  if (query) rows = rows.filter((row) => `${row.model || ""} ${row.title || ""}`.toLocaleLowerCase("zh-CN").includes(query));
  if (Number.isFinite(min)) rows = rows.filter((row) => Number(row.price) >= min);
  if (Number.isFinite(max)) rows = rows.filter((row) => Number(row.price) <= max);
  if ($("filter-watched").checked) rows = rows.filter((row) => watchedModels.has(row.model));
  const sort = $("filter-sort").value;
  if (sort === "price-asc") rows.sort((a, b) => Number(a.price) - Number(b.price));
  else if (sort === "price-desc") rows.sort((a, b) => Number(b.price) - Number(a.price));
  else rows.sort((a, b) => String(a.model).localeCompare(String(b.model), "zh-CN"));
  return rows;
}

function renderPrices() {
  const rows = applyPriceFilters();
  $("price-count").textContent = `${rows.length} 条`;
  $("statItems").textContent = allData.length;
  const covered = new Set(allData.map((item) => item.model));
  $("statModels").textContent = covered.size;
  const pageCount = Math.max(1, Math.ceil(rows.length / pricePageSize));
  pricePage = Math.max(1, Math.min(pricePage, pageCount));
  const pageRows = rows.slice((pricePage - 1) * pricePageSize, pricePage * pricePageSize);
  const wrap = $("price-table-wrap");
  wrap.replaceChildren();
  if (!pageRows.length) {
    wrap.append(placeholder("暂无匹配数据，请调整筛选或开始采集"));
  } else {
    const table = node("table");
    table.append(node("caption", "", `当前筛选共 ${rows.length} 条商品`));
    const head = node("thead");
    const headRow = node("tr");
    for (const title of ["型号", "系列", "平台", "价格", "商品标题", "链接", "更新时间"]) {
      const th = node("th", "", title); th.scope = "col"; headRow.append(th);
    }
    head.append(headRow); table.append(head);
    const body = node("tbody");
    for (const row of pageRows) {
      const tr = node("tr");
      const modelCell = node("td", "", row.model || "–");
      if (watchedModels.has(row.model)) modelCell.append(node("span", "star-s", " ★"));
      const seriesCell = node("td");
      seriesCell.append(node("span", `series-chip ${seriesClass[row.series] || ""}`, row.series || "其他"));
      tr.append(modelCell, seriesCell, node("td", "", row.platform || "–"));
      const price = Number(row.price);
      tr.append(node("td", "price-tag", Number.isFinite(price) ? `¥${price.toFixed(0)}` : "–"));
      const title = node("td", "title-cell", row.title || ""); title.title = row.title || ""; tr.append(title);
      const linkCell = node("td");
      const href = safeHttpUrl(row.url);
      if (href) {
        const link = node("a", "", "查看"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; linkCell.append(link);
      }
      tr.append(linkCell, node("td", "", String(row.ts || "").slice(5, 19)));
      body.append(tr);
    }
    table.append(body); wrap.append(table);
  }
  renderPagination(pageCount);
  updateTrendModels();
}

function renderPagination(pageCount) {
  const pagination = $("pagination");
  pagination.replaceChildren();
  if (pageCount <= 1) return;
  const makePageButton = (text, page, disabled = false, active = false) => {
    const button = node("button", `pg-btn${active ? " active" : ""}`, text);
    button.type = "button"; button.disabled = disabled;
    button.addEventListener("click", () => { pricePage = page; renderPrices(); $("price-table-wrap").scrollIntoView({ behavior: "smooth", block: "start" }); });
    return button;
  };
  pagination.append(makePageButton("上一页", pricePage - 1, pricePage === 1));
  let start = Math.max(1, pricePage - 2);
  let end = Math.min(pageCount, start + 4);
  start = Math.max(1, end - 4);
  for (let page = start; page <= end; page += 1) pagination.append(makePageButton(String(page), page, false, page === pricePage));
  pagination.append(makePageButton("下一页", pricePage + 1, pricePage === pageCount));
  const info = node("span", "pg-info", `第 ${pricePage}/${pageCount} 页 · 每页`);
  const size = node("select", "pg-size");
  size.setAttribute("aria-label", "每页数量");
  for (const amount of [10, 20, 50, 100]) {
    const option = node("option", "", amount); option.value = amount; option.selected = amount === pricePageSize; size.append(option);
  }
  size.addEventListener("change", () => { pricePageSize = Number(size.value); pricePage = 1; renderPrices(); });
  info.append(size, document.createTextNode("条"));
  pagination.append(info);
}

function updateTrendModels() {
  const select = $("trend-model");
  const previous = select.value;
  const models = [...new Set(allData.map((item) => item.model).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const currentOptions = [...select.options].map((option) => option.value);
  if (models.length === currentOptions.length && models.every((value, index) => value === currentOptions[index])) return;
  select.replaceChildren();
  for (const model of models) { const option = node("option", "", model); option.value = model; select.append(option); }
  if (models.includes(previous)) select.value = previous;
}

function setChart(containerId, chart, alt) {
  const container = $(containerId);
  container.replaceChildren();
  const path = safeChartPath(chart);
  if (!path) { container.append(placeholder("暂无足够数据")); return; }
  const image = node("img");
  image.src = `/static/${path}?v=${encodeURIComponent(dataSignature)}`;
  image.alt = alt;
  image.loading = "lazy";
  container.append(image);
}

function renderRecommendations(result) {
  const recommendations = result.recommendations || [];
  const box = $("cheapest-box");
  box.replaceChildren();
  if (!recommendations.length) {
    box.append(placeholder("暂无达到可信度要求的推荐"));
    $("statCheapest").textContent = "–";
    return;
  }
  const ranks = ["01", "02", "03", "04", "05"];
  for (const [index, recommendation] of recommendations.entries()) {
    const item = node("article", "rec-item");
    const head = node("div", "rec-head");
    head.append(node("span", "", ranks[index] || String(index + 1)), node("span", "big", recommendation.model), node("span", "", `@ ${recommendation.platform || "–"}`));
    if (recommendation.watched) head.append(node("span", "tag", "关注"));
    item.append(head, node("div", "rec-price", `¥${Number(recommendation.price).toFixed(0)}`));
    item.append(node("div", "rec-reason", recommendation.reason || ""), node("div", "meta", recommendation.title || ""));
    const href = safeHttpUrl(recommendation.url);
    if (href) {
      const meta = node("div", "meta url");
      const link = node("a", "", "前往商品详情"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; meta.append(link); item.append(meta);
    }
    box.append(item);
  }
  $("statCheapest").textContent = `¥${Number(recommendations[0].price).toFixed(0)}`;
}

async function refreshRecommendations() {
  const result = await api("/api/recommend");
  renderRecommendations(result);
}

function renderStats(stats) {
  statsCache = stats || {};
  $("st-count").textContent = stats.real_count ?? 0;
  $("st-mean").textContent = stats.mean ? `¥${Number(stats.mean).toFixed(0)}` : "–";
  $("st-median").textContent = stats.median ? `¥${Number(stats.median).toFixed(0)}` : "–";
  $("st-models").textContent = `${stats.models_covered ?? 0}/${stats.models_total ?? 0}`;
  setChart("stats-chart", stats.chart, "型号平均价格分布");
  const detail = $("stats-detail"); detail.replaceChildren();
  for (const [platform, count] of Object.entries(stats.platform_dist || {})) {
    const chip = node("span", "model-stat"); chip.append(document.createTextNode(`${platform} `), node("b", "", count), document.createTextNode(" 条")); detail.append(chip);
  }
  for (const [model, values] of Object.entries(stats.per_model || {})) {
    const chip = node("span", "model-stat");
    chip.append(node("b", "", model), document.createTextNode(" "), node("span", "msc", `均价¥${values.mean} · 中位¥${values.median} · 最低¥${values.min} · ${values.count}条`));
    detail.append(chip);
  }
  renderWatchSnapshot(stats);
}

function renderWatchSnapshot(stats) {
  const container = $("watch-snapshot"); container.replaceChildren();
  if (!watchedModels.size) { container.append(placeholder("在采集范围中为型号添加星标关注")); return; }
  const perModel = stats?.per_model || {};
  let count = 0;
  for (const model of watchedModels) {
    const values = perModel[model];
    if (!values) continue;
    count += 1;
    const card = node("article", "ws-card");
    card.append(node("div", "ws-model", model));
    const minimum = node("div", "ws-row", "最低 ");
    minimum.append(node("b", values.min <= values.median * 0.9 ? "down" : "", `¥${Number(values.min).toFixed(0)}`));
    card.append(minimum, node("div", "ws-row muted", `均价 ¥${Number(values.mean).toFixed(0)} · 中位 ¥${Number(values.median).toFixed(0)}`));
    card.append(node("div", "ws-row muted", `${values.count} 个在售商品`));
    container.append(card);
  }
  if (!count) container.append(placeholder("关注型号暂时没有价格数据"));
}

async function loadTrend() {
  const model = $("trend-model").value;
  const platform = $("trend-platform").value;
  if (!model) throw new Error("请先选择有数据的型号");
  $("trend-chart").replaceChildren(placeholder("正在生成走势图…"));
  const result = await api(`/api/trend?model=${encodeURIComponent(model)}&platform=${encodeURIComponent(platform)}`);
  setChart("trend-chart", result.chart, `${model} 价格走势`);
  if (!result.chart) toast("历史数据不足，暂时无法生成走势", "warn");
}

function renderLog(result) {
  const container = $("crawl-log"); container.replaceChildren();
  if (result.current_task) container.append(node("div", "log-current", result.current_task));
  const tasks = result.task_log || [];
  if (tasks.length) {
    container.append(node("div", "log-task-title", "运行日志"));
    const list = node("div", "log-list");
    for (const entry of [...tasks].reverse()) {
      const row = node("div", "log-row");
      row.append(node("span", "log-ts", entry.ts || ""), node("span", `log-msg ${entry.level === "ok" ? "ok" : entry.level === "warn" ? "warn" : ""}`, entry.msg || ""));
      list.append(row);
    }
    container.append(list);
  }
  const stats = result.logs || [];
  if (stats.length) {
    container.append(node("div", "log-task-title", "型号采集统计"));
    const list = node("div", "log-list");
    for (const entry of stats) {
      const row = node("div", "log-row log-stats-row");
      row.append(node("span", "log-model", entry.model || ""), node("span", "log-plat", entry.platform || ""), node("span", "log-info", entry.info || ""));
      list.append(row);
    }
    container.append(list);
  }
  if (!container.children.length) container.append(placeholder("开始采集后将在这里显示实时日志"));
}

async function refreshLog() {
  renderLog(await api("/api/log"));
}

async function refreshStatus() {
  const result = await api("/api/status");
  currentStatus = result;
  loginState = result.login_state || [];
  refreshStatusUI();
}

function signatureFor(rows) {
  const newest = rows.reduce((value, row) => String(row.ts || "") > value ? String(row.ts || "") : value, "");
  const idTotal = rows.reduce((total, row) => total + (Number(row.id) || 0), 0);
  const priceTotal = rows.reduce((total, row) => total + (Number(row.price) || 0), 0);
  return `${rows.length}-${idTotal}-${Math.round(priceTotal)}-${newest}`;
}

async function refreshDataBundle(force = false) {
  const suffix = !force && dataRevision >= 0 ? `?since=${encodeURIComponent(dataRevision)}` : "";
  const priceResult = await api(`/api/prices${suffix}`);
  if (priceResult.unchanged) return;
  const rows = priceResult.data || [];
  if (Number.isFinite(Number(priceResult.revision))) {
    dataRevision = Number(priceResult.revision);
  } else {
    const nextSignature = signatureFor(rows);
    if (!force && nextSignature === dataSignature) return;
    dataSignature = nextSignature;
  }
  allData = rows;
  renderPrices();
  const [stats, recommendations, series, models] = await Promise.all([
    api("/api/stats"), api("/api/recommend"), api("/api/series_chart"), api("/api/models"),
  ]);
  renderStats(stats);
  renderRecommendations(recommendations);
  setChart("series-chart", series.chart, "各型号价格概览");
  allModels = models.models || allModels;
  hiddenModels = models.hidden_models || hiddenModels;
  renderModelCards();
}

async function doStart() {
  const result = await api("/api/control/start_crawl", "POST");
  toast(result.ok ? "浏览器将自动启动；每个型号只采集一轮" : result.msg, result.ok ? "success" : "error");
  await refreshStatus();
}

async function doControl(path) {
  const result = await api(path, "POST");
  toast(result.msg || "操作已完成", "success");
  await refreshStatus();
}

function doExport() {
  const link = node("a");
  link.href = "/api/export";
  link.download = "gpu_prices.csv";
  document.body.append(link);
  link.click();
  link.remove();
  toast("CSV 下载已开始", "success");
}

async function doClear() {
  if (!window.confirm("确定清空当前价格和全部历史走势？此操作不可恢复。")) return;
  const result = await api("/api/clear", "POST");
  toast(result.msg || "价格数据已清空", "success");
  dataSignature = "__force__";
  dataRevision = -1;
  await refreshDataBundle(true);
}

function reportActionError(error) {
  toast(error?.message || "操作失败", "error");
}

function bindStaticEvents() {
  bindPlatformCards();
  bindSectionSpy();
  $("btn-start").addEventListener("click", () => withBusy("btn-start", doStart).catch(reportActionError));
  $("btn-pause").addEventListener("click", () => withBusy("btn-pause", () => doControl("/api/control/pause")).catch(reportActionError));
  $("btn-resume").addEventListener("click", () => withBusy("btn-resume", () => doControl("/api/control/resume")).catch(reportActionError));
  $("btn-stop").addEventListener("click", () => withBusy("btn-stop", () => doControl("/api/control/stop")).catch(reportActionError));
  $("btn-boot").addEventListener("click", () => withBusy("btn-boot", doBoot).catch(reportActionError));
  $("btn-check").addEventListener("click", () => withBusy("btn-check", doCheckAll).catch(reportActionError));
  $("btn-mode").addEventListener("click", () => withBusy("btn-mode", toggleBrowserMode).catch(reportActionError));
  $("btn-save-settings").addEventListener("click", () => withBusy("btn-save-settings", saveThresholds).catch(reportActionError));
  $("btn-reset-settings").addEventListener("click", () => withBusy("btn-reset-settings", resetThresholds).catch(reportActionError));
  $("btn-save-scope").addEventListener("click", () => withBusy("btn-save-scope", () => persistScope(true)).catch(reportActionError));
  $("btn-add-model").addEventListener("click", () => withBusy("btn-add-model", addModel).catch(reportActionError));
  $("new-model").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); withBusy("btn-add-model", addModel).catch(reportActionError); } });
  for (const id of ["filter-search", "filter-min", "filter-max"]) $(id).addEventListener("input", () => { pricePage = 1; renderPrices(); });
  for (const id of ["filter-series", "filter-sort", "filter-watched"]) $(id).addEventListener("change", () => { pricePage = 1; renderPrices(); });
  $("btn-trend").addEventListener("click", () => withBusy("btn-trend", loadTrend).catch(reportActionError));
  $("trend-model").addEventListener("change", () => { if ($("trend-model").value) loadTrend().catch(reportActionError); });
  $("trend-platform").addEventListener("change", () => { if ($("trend-model").value) loadTrend().catch(reportActionError); });
  $("btn-refresh-log").addEventListener("click", () => withBusy("btn-refresh-log", refreshLog).catch(reportActionError));
  $("btn-export").addEventListener("click", doExport);
  $("btn-clear").addEventListener("click", () => withBusy("btn-clear", doClear).catch(reportActionError));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModal(); });
}

async function fastLoop() {
  if (!document.hidden && !fastBusy) {
    fastBusy = true;
    try { await Promise.all([refreshStatus(), refreshLog()]); }
    catch (error) { console.warn(error.message); }
    finally { fastBusy = false; }
  }
  // 采集器按页面批量提交、逐条记录日志；高频轻量状态轮询及时反映进度。
  window.setTimeout(fastLoop, 2000);
}

async function slowLoop() {
  if (!document.hidden && !slowBusy) {
    slowBusy = true;
    try { await refreshDataBundle(false); }
    catch (error) { console.warn(error.message); }
    finally { slowBusy = false; }
  }
  // 仅轮询轻量数据版本；版本未变化时服务端不再序列化完整价格列表。
  window.setTimeout(slowLoop, 2500);
}

async function initialize() {
  bindStaticEvents();
  try {
    const [status, settings, models] = await Promise.all([
      api("/api/status"), api("/api/settings"), api("/api/models"),
    ]);
    currentStatus = status;
    loginState = status.login_state || [];
    allModels = models.models || [];
    hiddenModels = models.hidden_models || [];
    applySettings(settings.settings || {});
    renderModelCards();
    refreshStatusUI();
    await Promise.all([refreshDataBundle(true), refreshLog()]);
  } catch (error) {
    reportActionError(error);
    $("error-hint").textContent = error.message;
  }
  fastLoop();
  slowLoop();
}

initialize();
