import { element } from "../../shared/controls.js";

const exact = new Intl.NumberFormat("zh-CN");
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
const SVG_NS = "http://www.w3.org/2000/svg";
const HEIGHT = 218;
const PAD = { left: 42, right: 12, top: 14, bottom: 24 };
const number = (value) => Number(value) || 0;

export function formatNumber(value) {
  return exact.format(number(value));
}

export function trendChart({ title, id, unit, days, series, empty }) {
  const heading = element("h3", { id }, title);
  const legend = element("div", { class: "dashboard-chart-legend" });
  for (const [, label, color, dashed] of series) {
    const entry = element("span", { class: `dashboard-series dashboard-${color}` });
    entry.append(element("i", { class: dashed ? "is-dashed" : "", "aria-hidden": "true" }), label);
    legend.append(entry);
  }
  const plot = element("div", { class: "dashboard-plot" });
  const readout = element("div", { class: "dashboard-readout" });
  let peak = 0;
  for (const day of days) {
    for (const [key] of series) peak = Math.max(peak, number(day[key]));
  }
  if (!peak) {
    plot.append(element("p", { class: "description dashboard-empty" }, empty));
    readout.hidden = true;
    return { heading, legend, plot, readout, observe: () => () => {} };
  }

  plot.tabIndex = 0;
  plot.setAttribute("role", "group");
  plot.setAttribute("aria-label", `${title}，每日统计`);
  plot.setAttribute("aria-describedby", `${id}-readout`);
  readout.id = `${id}-readout`;
  readout.setAttribute("aria-live", "polite");
  const graphic = svg("svg", { "aria-hidden": "true" });
  const marker = svg("g");
  plot.append(graphic);

  const step = tickStep(peak);
  const ceiling = Math.ceil(peak / step) * step;
  let selected = days.length - 1;
  let width = 0;
  const multipleYears = days[0].date.slice(0, 4) !== days.at(-1).date.slice(0, 4);
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const plotWidth = () => width - PAD.left - PAD.right;
  const x = (index) => PAD.left + plotWidth() * (days.length === 1 ? 0.5 : index / (days.length - 1));
  const y = (value) => PAD.top + plotHeight * (1 - number(value) / ceiling);

  function draw(nextWidth) {
    if (nextWidth === width || nextWidth < 100) return;
    width = nextWidth;
    graphic.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
    const grid = svg("g", { class: "dashboard-grid" });
    for (let value = 0; value <= ceiling; value += step) {
      grid.append(line(PAD.left, y(value), width - PAD.right, y(value)));
      grid.append(text(PAD.left - 7, y(value) + 4, "end", compact.format(value)));
    }
    const ticks = Math.min(days.length, Math.max(2, Math.floor(plotWidth() / 86) + 1));
    for (let tick = 0; tick < ticks; tick += 1) {
      const index = ticks === 1 ? 0 : Math.round(tick * (days.length - 1) / (ticks - 1));
      const anchor = ticks === 1 ? "middle" : tick === 0 ? "start" : tick === ticks - 1 ? "end" : "middle";
      const date = days[index].date;
      grid.append(text(x(index), HEIGHT - 5, anchor, multipleYears ? date : date.slice(5)));
    }
    graphic.replaceChildren(grid);
    if (days.length > 1) {
      for (const [key, , color, dashed] of series) {
        const points = days.map((day, index) => `${x(index).toFixed(1)},${y(day[key]).toFixed(1)}`).join(" ");
        graphic.append(svg("polyline", { class: `dashboard-curve dashboard-${color}${dashed ? " is-dashed" : ""}`, points }));
      }
    }
    graphic.append(marker);
    updateMarker();
  }

  function updateMarker() {
    const at = x(selected);
    marker.replaceChildren(line(at, PAD.top, at, HEIGHT - PAD.bottom, "dashboard-guide"));
    // 同一天的系列可能重合；虚线和空心点保留至少一层轮廓。
    for (const [key, , color, dashed] of series) {
      marker.append(svg("circle", {
        cx: at, cy: y(days[selected][key]), r: dashed ? 5 : 3.5,
        class: `dashboard-point dashboard-${color}${dashed ? " is-dashed" : ""}`,
      }));
    }
  }

  function updateReadout() {
    const day = days[selected];
    readout.replaceChildren(element("time", { datetime: day.date }, day.date));
    for (const [key, label] of series) {
      const entry = element("span", { class: "description" }, `${label} `);
      entry.append(element("strong", {}, formatNumber(day[key])));
      if (key === series.at(-1)[0]) entry.append(` ${unit}`);
      readout.append(entry);
    }
  }

  function select(index) {
    const next = Math.max(0, Math.min(days.length - 1, index));
    if (next === selected) return;
    selected = next;
    updateMarker();
    updateReadout();
  }

  function pointToDay(event) {
    if (!width) return;
    const offset = event.clientX - plot.getBoundingClientRect().left - PAD.left;
    select(Math.round(offset / plotWidth() * (days.length - 1)));
  }

  plot.addEventListener("pointermove", (event) => { if (event.pointerType !== "touch") pointToDay(event); });
  plot.addEventListener("pointerup", pointToDay);
  plot.addEventListener("keydown", (event) => {
    const index = { ArrowLeft: selected - 1, ArrowRight: selected + 1, Home: 0, End: days.length - 1 }[event.key];
    if (typeof index !== "number") return;
    event.preventDefault();
    select(index);
  });
  updateReadout();

  return {
    heading, legend, plot, readout,
    observe() {
      draw(plot.clientWidth);
      const observer = new ResizeObserver(([entry]) => draw(Math.round(entry.contentRect.width)));
      observer.observe(plot);
      return () => observer.disconnect();
    },
  };
}

function tickStep(peak) {
  const target = Math.max(1, peak / 4);
  const magnitude = 10 ** Math.floor(Math.log10(target));
  return (target <= magnitude ? 1 : target <= 2 * magnitude ? 2 : target <= 5 * magnitude ? 5 : 10) * magnitude;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function line(x1, y1, x2, y2, className = "") {
  return svg("line", { x1, y1, x2, y2, class: className });
}

function text(x, y, anchor, content) {
  const label = svg("text", { x, y, "text-anchor": anchor });
  label.textContent = content;
  return label;
}
