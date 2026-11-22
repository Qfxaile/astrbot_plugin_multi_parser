import { element } from "../../shared/controls.js";

export const STATUS_LABELS = { success: "成功", failure: "失败", skipped: "跳过" };
const ADAPTER_LABELS = { aiocqhttp: "OneBot", qq_official: "QQ 官方" };

export function logButton(icon, label) {
  const button = element("button", { class: "button logs-tool", type: "button", title: label, "aria-label": label });
  button.append(element("span", { class: "icon-slot", "data-icon": icon, "aria-hidden": "true" }));
  return button;
}

export function logRow(row, platformName, showDetails) {
  const tr = element("tr", { class: "logs-row" });
  const { date, time } = formatTime(row.time);
  const { primary, secondary } = sourceLabels(row);
  const source = element("div", { title: row.umo || secondary });
  source.append(line(primary), line(secondary, "logs-secondary"));
  const timestamp = element("time", { datetime: row.time || "", title: row.time || "" });
  timestamp.append(line(date), line(time, "logs-secondary"));

  const links = element("div");
  const [firstLink, ...remainingLinks] = row.links || [];
  links.append(firstLink ? logLink(firstLink) : line("—", "logs-secondary"));
  if (remainingLinks.length) links.append(line(`另有 ${remainingLinks.length} 条`, "logs-secondary"));

  const outcome = element("div");
  outcome.append(badge(row.status));
  const error = row.error_reason || row.error_type;
  if (error) outcome.append(line(error, "logs-secondary"));
  const details = logButton("ellipsis", "查看日志详情");
  details.setAttribute("aria-haspopup", "dialog");
  details.addEventListener("click", () => showDetails(row));

  const cells = {
    time: timestamp,
    source,
    platform: line(platformName(row.platform) || "—"),
    link: links,
    status: outcome,
    duration: line(formatDuration(row.duration_ms), "logs-duration", `${row.duration_ms ?? 0} ms`),
    detail: details,
  };
  for (const [name, content] of Object.entries(cells)) {
    const cell = element("td", { class: `logs-cell-${name}` });
    cell.append(content);
    tr.append(cell);
  }
  return tr;
}

export function createLogDetails(platformName) {
  const dialog = element("dialog", { class: "card logs-dialog", "aria-labelledby": "logs-detail-title" });
  const header = element("header", { class: "logs-header logs-dialog-header" });
  const close = logButton("x", "关闭详情");
  close.addEventListener("click", () => dialog.close());
  header.append(element("h3", { id: "logs-detail-title" }, "解析详情"), close);
  const fields = element("dl", { class: "logs-detail-fields" });
  dialog.append(header, fields);

  function show(row) {
    const { date, time } = formatTime(row.time);
    const { primary, secondary } = sourceLabels(row);
    const values = [
      ["时间", [date, time].filter(Boolean).join(" ")],
      ["来源会话", `${primary} · ${secondary}`],
      ["会话标识 (UMO)", row.umo || "未记录"],
      ["解析平台", platformName(row.platform) || "—"],
      ["结果", row.result || STATUS_LABELS[row.status] || "未知"],
      ["耗时", `${row.duration_ms ?? 0} ms`],
      ["解析链接", (row.links || []).map(logLink)],
      ["错误原因", row.error_reason || row.error_type || "—"],
    ];
    fields.replaceChildren();
    for (const [label, value] of values) {
      const detail = element("dd");
      if (Array.isArray(value) && value.length) detail.append(...value);
      else detail.textContent = Array.isArray(value) ? "—" : value;
      fields.append(element("dt", {}, label), detail);
    }
    dialog.showModal();
  }

  return { dialog, show };
}

function sourceLabels(row) {
  const [adapter, type] = (row.source || "").split(" · ", 2);
  const [, instance, sessionId] = (row.umo || "").match(/^([^:]+):[^:]+:(.+)$/) || [];
  const adapterName = Object.hasOwn(ADAPTER_LABELS, adapter) ? ADAPTER_LABELS[adapter] : adapter;
  const identifier = row.group_id || sessionId;
  return {
    primary: [type || "未知会话", identifier].filter(Boolean).join(" · "),
    secondary: [adapterName, instance && instance !== adapter ? instance : ""].filter(Boolean).join(" · ") || "未记录",
  };
}

function badge(status) {
  const key = Object.hasOwn(STATUS_LABELS, status) ? status : "unknown";
  return element("span", { class: `logs-badge log-${key}` }, STATUS_LABELS[key] || "未知");
}

function line(value, style = "", title = value) {
  return element("span", { class: `logs-line ${style}`, title }, value);
}

function logLink(href) {
  if (window.parent !== window) return line(href);
  try {
    const url = new URL(href);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password) {
      return element("a", { class: "log-link", href, target: "_blank", rel: "noopener noreferrer", title: href }, href);
    }
  } catch {
    // 无效历史链接只展示文本。
  }
  return line(href);
}

function formatTime(value) {
  // 使用记录时的服务器日期，与数据库日期筛选保持一致。
  const [, date, time] = (value || "").match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/) || [];
  return { date: date || value || "—", time: time || "" };
}

function formatDuration(ms = 0) {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.floor(ms / 60_000)} 分 ${Math.floor(ms / 1000) % 60} 秒`;
}
