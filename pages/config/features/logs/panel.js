import { element } from "../../shared/controls.js";
import { hydrateIcons } from "../../shared/icons.js";
import { logButton, logRow, createLogDetails, STATUS_LABELS } from "./rows.js";
import { createLogFilters } from "./filters.js";

const filterValues = {};
let panel;

export function loadLogs(bridge, platforms = []) {
  panel ??= mountLogs(document.querySelector("#logs-content"), bridge);
  return panel.load(platforms);
}

function mountLogs(root, bridge) {
  let names = new Map();
  let requestId = 0;
  let page = 1;
  let searchTimer;
  const platformName = (key) => names.get(key) || key;
  const header = element("header", { class: "logs-header" });
  const refresh = logButton("refresh-cw", "刷新日志");
  refresh.addEventListener("click", () => requestPage(1));
  header.append(element("h2", {}, "插件日志"), refresh);
  const view = element("div", { class: "logs-view" });
  const { dialog, show } = createLogDetails(platformName);
  const { toolbar, count, setOptions } = createLogFilters(platformName, filterValues, changeFilters);
  const frame = element("section", { class: "card logs-frame", "aria-label": "解析记录" });
  const scroll = element("div", { class: "logs-table-scroll", tabindex: "0", role: "region", "aria-label": "解析日志表格" });
  const table = element("table", { class: "runtime-log-table", "aria-label": "解析日志" });
  const headings = element("tr");
  const columns = ["时间", "来源会话", "解析平台", "解析链接", "结果", "耗时", "详情"];
  for (const name of columns) headings.append(element("th", { scope: "col" }, name));
  const thead = element("thead");
  thead.append(headings);
  const body = element("tbody");
  table.append(thead, body);
  scroll.append(table);

  const footer = element("footer", { class: "logs-footer" });
  const pageStatus = element("span", { class: "logs-page description", role: "status" });
  const paging = element("div", { class: "logs-paging", role: "group", "aria-label": "日志分页" });
  const prev = logButton("chevron-left", "上一页");
  const next = logButton("chevron-right", "下一页");
  paging.append(prev, next);
  footer.append(pageStatus, paging);
  frame.append(scroll, footer);
  view.append(toolbar, frame, dialog);
  root.replaceChildren(header, view);
  hydrateIcons(root);

  prev.addEventListener("click", () => requestPage(page - 1));
  next.addEventListener("click", () => requestPage(page + 1));

  function changeFilters(debounce) {
    clearTimeout(searchTimer);
    if (debounce) {
      requestId += 1;
      prev.disabled = next.disabled = true;
      searchTimer = setTimeout(() => requestPage(1), 250);
    } else {
      requestPage(1);
    }
  }

  async function requestPage(targetPage) {
    clearTimeout(searchTimer);
    const current = ++requestId;
    refresh.disabled = prev.disabled = next.disabled = true;
    frame.setAttribute("aria-busy", "true");
    count.textContent = "正在读取插件解析日志…";
    try {
      const data = await bridge.apiPost("logs", { ...filterValues, page: targetPage });
      if (current !== requestId) return;
      if (!Array.isArray(data?.rows)) throw new Error("插件解析日志响应格式无效。");
      renderPage(data);
    } catch (error) {
      if (current !== requestId) return;
      count.textContent = "";
      pageStatus.textContent = "";
      showEmpty(error?.message || "插件解析日志读取失败，请刷新重试。", true);
    } finally {
      if (current === requestId) {
        refresh.disabled = false;
        frame.removeAttribute("aria-busy");
      }
    }
  }

  function renderPage({ rows, counts, total, matched, page: currentPage, page_size: pageSize, ...choices }) {
    page = currentPage;
    setOptions(choices);
    count.replaceChildren(element("span", { class: "logs-count" }, `匹配 ${matched.toLocaleString()} / ${total.toLocaleString()} 条`));
    for (const [key, label] of Object.entries(STATUS_LABELS)) {
      count.append(element("span", { class: `logs-count-item log-${key}` }, `${label} ${counts[key].toLocaleString()}`));
    }
    const start = (page - 1) * pageSize;
    const end = start + rows.length;
    body.replaceChildren(...rows.map((row) => logRow(row, platformName, show)));
    hydrateIcons(body);
    if (!rows.length) showEmpty(total ? "没有符合条件的记录" : "暂无解析日志");
    scroll.scrollTop = 0;
    pageStatus.textContent = matched ? `第 ${start + 1}–${end} 条，共 ${matched.toLocaleString()} 条` : "0 条记录";
    prev.disabled = page === 1;
    next.disabled = end >= matched;
  }

  function showEmpty(text, error = false) {
    const empty = element("tr", { class: "logs-empty-row" });
    empty.append(element("td", { colspan: String(columns.length), class: "description logs-state" },
      element("span", { role: error ? "alert" : "status" }, text)));
    body.replaceChildren(empty);
  }

  return {
    load(platforms) {
      names = new Map(platforms.map(({ key, name }) => [key, name]));
      return requestPage(1);
    },
  };
}
