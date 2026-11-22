import { element, selectControl } from "../../shared/controls.js";
import { logButton, STATUS_LABELS } from "./rows.js";

export function createLogFilters(platformName, values, onChange) {
  const toolbar = element("section", { class: "card logs-toolbar", role: "search", "aria-label": "筛选解析日志" });
  const filters = element("div", { class: "logs-filters" });
  const dates = element("fieldset", { class: "logs-date-range" });
  const start = dateInput("开始日期", values.start);
  const end = dateInput("结束日期", values.end);
  const datesInputs = element("div", { class: "logs-dates" });
  datesInputs.append(start, element("span", { "aria-hidden": "true" }, "至"), end);
  dates.append(element("legend", {}, "日期范围"), datesInputs);

  const query = element("input", { id: "logs-query", class: "input", type: "search", placeholder: "群 ID、UMO、链接或错误" });
  query.value = values.query || "";
  const source = selectFilter("source", "来源", "全部来源", values, update);
  const platform = selectFilter("platform", "平台", "全部平台", values, update);
  const status = selectFilter("status", "结果", "全部结果", values, update);
  status.setChoices(Object.entries(STATUS_LABELS));
  filters.append(dates, labeled("关键词", query), source.field, platform.field, status.field);

  const count = element("div", { class: "logs-counts", role: "status" });
  const reset = logButton("rotate-ccw", "重置筛选");
  const summary = element("div", { class: "logs-toolbar-footer" });
  summary.append(count, reset);
  toolbar.append(filters, summary);

  const controls = { start, end, query, source: source.control, platform: platform.control, status: status.control };
  start.addEventListener("change", update);
  end.addEventListener("change", update);
  query.addEventListener("input", update);
  reset.addEventListener("click", () => {
    for (const control of Object.values(controls)) control.value = "";
    update();
  });

  function update(event) {
    for (const [key, control] of Object.entries(controls)) values[key] = control.value;
    reset.disabled = !Object.values(values).some(Boolean);
    onChange(event?.type === "input");
  }

  function setOptions(data) {
    source.setChoices(options(data.sources));
    platform.setChoices(options(data.platforms, platformName));
  }

  reset.disabled = !Object.values(values).some(Boolean);
  return { toolbar, count, setOptions };
}

function dateInput(label, value) {
  const input = element("input", { class: "input", type: "date", "aria-label": label, title: label });
  input.value = value || "";
  return input;
}

function selectFilter(key, label, placeholder, values, onChange) {
  const id = `logs-${key}`;
  const wrapper = selectControl(id, [], "", onChange);
  const control = wrapper.querySelector("select");
  function setChoices(choices) {
    const selected = values[key] || "";
    // 刷新期间保留已选择的条件，包括暂时没有匹配行的值。
    if (selected && !choices.some(([value]) => value === selected)) choices = [...choices, [selected, selected]];
    control.replaceChildren(...[["", placeholder], ...choices].map(([value, text]) => element("option", { value }, text)));
    control.value = selected;
  }
  setChoices([]);
  return { field: labeled(label, wrapper, id), control, setChoices };
}

function labeled(label, control, id = control.id) {
  const field = element("div", { class: "logs-field" });
  field.append(element("label", { for: id }, label), control);
  return field;
}

function options(values, label = (value) => value) {
  return values
    .map((value) => [value, label(value)])
    .sort((left, right) => left[1].localeCompare(right[1], "zh-CN"));
}
