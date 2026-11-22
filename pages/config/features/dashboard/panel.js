import { element } from "../../shared/controls.js";
import { hydrateIcons } from "../../shared/icons.js";
import { formatNumber, trendChart } from "./trend-chart.js";

const RANGES = [["today", "今天"], ["7d", "近 7 天"], ["14d", "近 14 天"], ["30d", "近 30 天"], ["all", "全部时间"]];
let dashboard;

export function loadDashboard(bridge) {
  dashboard ??= mountDashboard(document.querySelector("#dashboard-content"), bridge);
  return dashboard.load();
}

function mountDashboard(root, bridge) {
  let period = "14d";
  let requestId = 0;
  let renderedPeriod;
  let disconnectCharts = [];

  const header = element("header", { class: "dashboard-header" });
  const heading = element("div");
  const dates = element("p", { class: "description dashboard-dates" });
  heading.append(element("h2", {}, "工作台"), dates);
  const actions = element("div", { class: "dashboard-actions" });
  const ranges = element("div", { class: "dashboard-ranges", role: "group", "aria-label": "统计时间范围" });
  const buttons = RANGES.map(([key, label]) => {
    const button = element("button", { type: "button", class: "button dashboard-range", "aria-pressed": String(key === period) }, label);
    button.addEventListener("click", () => {
      if (key !== period) {
        period = key;
        load();
      }
    });
    ranges.append(button);
    return [key, button];
  });
  const refresh = element("button", { type: "button", class: "button dashboard-refresh", title: "刷新统计", "aria-label": "刷新统计" });
  refresh.append(icon("refresh-cw"));
  refresh.addEventListener("click", () => {
    if (!refresh.hasAttribute("aria-disabled")) load();
  });
  actions.append(ranges, refresh);
  header.append(heading, actions);

  const message = element("p", { class: "description dashboard-state", role: "status", hidden: "" });
  const content = element("div", { class: "dashboard-data" });
  root.replaceChildren(header, message, content);
  hydrateIcons(header);

  async function load() {
    const current = ++requestId;
    const requestedPeriod = period;
    for (const [key, button] of buttons) button.setAttribute("aria-pressed", String(key === period));
    refresh.setAttribute("aria-disabled", "true");
    content.setAttribute("aria-busy", "true");
    content.hidden = dates.hidden = renderedPeriod !== period;
    showStatus("正在读取统计…");

    try {
      const data = await bridge.apiPost("dashboard", { period: requestedPeriod });
      if (current !== requestId) return;
      if (!data?.totals || !Array.isArray(data.days)) throw new Error("统计响应格式无效");
      const next = dashboardData(data);
      disconnectCharts.forEach((disconnect) => disconnect());
      content.replaceChildren(next.summary, next.trends);
      content.hidden = false;
      disconnectCharts = next.charts.map((chart) => chart.observe());
      dates.textContent = dateRange(data.days);
      dates.hidden = false;
      renderedPeriod = requestedPeriod;
      showStatus("");
    } catch {
      if (current === requestId) {
        showStatus(content.hidden ? "工作台数据读取失败，请刷新重试。" : "统计刷新失败，当前显示上次读取的数据。", true);
      }
    } finally {
      if (current === requestId) {
        refresh.removeAttribute("aria-disabled");
        content.removeAttribute("aria-busy");
      }
    }
  }

  function showStatus(text, error = false) {
    message.hidden = !text;
    message.dataset.error = String(error);
    message.textContent = text;
  }

  return { load };
}

function dashboardData({ totals, days, token_note: tokenNote }) {
  const summary = element("section", { class: "dashboard-summary", "aria-label": "统计概览" });
  summary.append(
    metric("解析成功", totals.parses, "file-check", "primary"),
    metric("发送解析", totals.sent, "send", "success"),
    metric("解析失败", totals.failures, "circle-alert", "danger"),
    metric("AI 总结 Token", totals.tokens, "brain", "amber", `${formatNumber(totals.ai_calls)} 次总结`),
  );

  const parseChart = trendChart({
    title: "解析与发送趋势", id: "dashboard-parse", unit: "次", days,
    series: [
      ["parses", "成功", "primary"], ["sent", "已发送", "success", true],
      ["failures", "失败", "danger"], ["skipped", "跳过", "muted", true],
    ],
    empty: "所选时间内没有解析记录",
  });
  const tokenChart = trendChart({
    title: "AI 总结用量", id: "dashboard-token", unit: "Token", days,
    series: [["input_tokens", "输入", "primary"], ["output_tokens", "输出", "amber"]],
    empty: totals.ai_calls ? "所选时间内 Token 用量为 0" : "所选时间内没有 AI 总结记录",
  });
  const accounting = element("p", { class: "description dashboard-accounting" });
  accounting.append(
    element("span", {}, `精确计量 ${formatNumber(totals.exact_token_calls)} 次`),
    element("span", { title: tokenNote || "估算不含图片 Token" }, `含估算 ${formatNumber(totals.estimated_token_calls)} 次（估算不含图片 Token）`),
  );
  const trends = element("div", { class: "dashboard-trends" });
  trends.append(chartPanel(parseChart), chartPanel(tokenChart, accounting));
  hydrateIcons(summary);
  return { summary, trends, charts: [parseChart, tokenChart] };
}

function chartPanel(chart, note) {
  const panel = element("section", { class: "card dashboard-chart", "aria-labelledby": chart.heading.id });
  panel.append(chart.heading, chart.legend, chart.plot, chart.readout);
  if (note) panel.append(note);
  return panel;
}

function metric(label, value, glyph, color, note = "") {
  const card = element("div", { class: `card dashboard-metric dashboard-${color}` });
  const header = element("div", { class: "dashboard-metric-header" });
  header.append(element("span", {}, label), icon(glyph));
  card.append(header, element("strong", {}, formatNumber(value)));
  if (note) card.append(element("span", { class: "description dashboard-metric-note" }, note));
  return card;
}

function icon(name) {
  return element("span", { class: "icon-slot", "data-icon": name, "aria-hidden": "true" });
}

function dateRange(days) {
  const start = days[0]?.date;
  const end = days.at(-1)?.date;
  if (!start || !end) return "";
  return start === end ? start : `${start} 至 ${end}`;
}
