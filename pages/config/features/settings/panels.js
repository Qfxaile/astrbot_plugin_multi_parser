import { element, submitOnEnter, toggle } from "../../shared/controls.js";

const TITLES = { delivery: "发送方式", ai: "AI 与会话", network: "网络设置" };

const DEPENDENCIES = {
  forward_image_threshold: { forward_mode: "threshold" },
  forward_text_threshold: { forward_mode: "threshold" },
  limit_private_chat_messages: { non_forward_message_limit: (value) => value !== -1 },
  max_video_size_mb: { send_video_by_url: true },
  video_over_limit_action: { send_video_by_url: true },
  allow_unknown_video_size: { send_video_by_url: true },
  size_check_timeout_seconds: { send_video_by_url: true },
  filtered_link_text: { filter_output_links: true },
  enable_conversation_history: { parse_mode: "auto" },
  conversation_history_mode: { parse_mode: "auto", enable_conversation_history: true },
  enable_ai_summary: { parse_mode: "auto" },
  ai_summary_mode: { parse_mode: "auto", enable_ai_summary: true },
  ai_summary_text_provider_id: { parse_mode: "auto", enable_ai_summary: true },
  ai_summary_vision_provider_id: { parse_mode: "auto", enable_ai_summary: true, ai_summary_mode: "text_and_images" },
  ai_summary_prompt: { parse_mode: "auto", enable_ai_summary: true },
  ai_summary_timeout_seconds: { parse_mode: "auto", enable_ai_summary: true },
  ai_summary_max_input_chars: { parse_mode: "auto", enable_ai_summary: true },
  ai_summary_max_images: { parse_mode: "auto", enable_ai_summary: true, ai_summary_mode: "text_and_images" },
};

export function createSettingsPanels(state, submit) {
  const drafts = new Map();
  const locked = () => state.busy || state.reloadRequired;

  function save(field, value, controlId) {
    if (value === field.value) {
      drafts.delete(field.key);
      return;
    }
    submit({ settings: { [field.key]: value } }, () => drafts.delete(field.key), controlId);
  }

  function selectField(field, attrs, disabled) {
    const wrapper = element("div", { class: "settings-select" });
    const control = element("select", { ...attrs, class: "input settings-input" });
    const options = field.type === "provider"
      ? [
        ["", field.key === "ai_summary_vision_provider_id" ? "跟随文本总结模型" : "跟随当前会话模型"],
        ...(state.snapshot.providers || []).map(({ id, model }) => [id, model && model !== id ? `${model} · ${id}` : id]),
      ]
      : field.options.map((value, index) => [value, field.labels[index] || value]);
    if (!options.some(([value]) => value === field.value)) {
      options.push([field.value, `${field.value}（当前不可用）`]);
    }
    for (const [value, label] of options) control.append(element("option", { value }, label));
    control.value = field.value;
    control.disabled = disabled;
    control.addEventListener("change", () => save(field, control.value, control.id));
    wrapper.append(control);
    return wrapper;
  }

  function inputField(field, attrs, disabled) {
    const isPrompt = field.key === "ai_summary_prompt";
    const isNumber = field.type === "int" || field.type === "float";
    const form = element("form", { class: "settings-form", autocomplete: "off" });
    const input = element(isPrompt ? "textarea" : "input", {
      ...attrs,
      class: isPrompt ? "input settings-input prompt-input" : "input settings-input",
      ...(isPrompt ? { rows: 6 } : { type: isNumber ? "number" : "text" }),
      ...(isNumber ? { step: field.type === "int" ? "1" : "any", required: "" } : {}),
      ...(field.min != null ? { min: field.min } : {}),
      ...(field.max != null ? { max: field.max } : {}),
      ...(field.key === "proxy_url" ? { spellcheck: "false" } : {}),
    });
    input.value = drafts.get(field.key) ?? field.value;
    input.disabled = disabled;
    input.addEventListener("input", () => drafts.set(field.key, input.value));
    const handleEnter = submitOnEnter(form);
    input.addEventListener("keydown", (event) => {
      if (isPrompt && event.shiftKey) return;
      handleEnter(event);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (locked()) return;
      const value = isNumber ? input.valueAsNumber : isPrompt ? input.value : input.value.trim();
      save(field, value);
    });
    form.append(input);
    return form;
  }

  function row(field, values) {
    const id = `config-${field.key}`;
    const attrs = { id, "aria-describedby": `${id}-hint` };
    const label = element("div", { class: "field-label" });
    label.append(
      element("label", { for: id }, field.label),
      element("p", { id: `${id}-hint`, class: "description" }, field.hint),
    );
    const disabled = locked() || Object.entries(DEPENDENCIES[field.key] || {}).some(
      ([key, expected]) => typeof expected === "function" ? !expected(values[key]) : values[key] !== expected,
    );
    const stacked = field.key === "ai_summary_prompt" || field.key === "proxy_url";
    const container = element("div", { class: `field-row settings-row${stacked ? " settings-row-stacked" : ""}` });
    let control;
    if (field.type === "bool") {
      control = toggle(id, field.label, field.value, (value) => save(field, value), disabled);
      control.setAttribute("aria-describedby", attrs["aria-describedby"]);
    } else if (field.type === "provider" || field.options.length) {
      control = selectField(field, attrs, disabled);
    } else {
      control = inputField(field, attrs, disabled);
    }
    container.append(label, control);
    return container;
  }

  function proxyCard(platforms, values) {
    const card = element("section", { class: "card" });
    card.append(element("h3", {}, "平台代理"), element("p", { class: "description" },
      values.proxy_url ? "选择使用代理的内容平台。" : "请先填写代理地址并按 Enter 应用。"));
    const rows = element("div", { class: "proxy-fields" });
    for (const platform of platforms) {
      const id = `proxy-${platform.key}`;
      const container = element("div", { class: "field-row proxy-row" });
      container.append(
        element("label", { for: id }, platform.name),
        toggle(id, `${platform.name}代理`, platform.proxy_enabled,
          (enabled) => submit({ proxy_switches: { [platform.key]: enabled } }),
          locked() || (!values.proxy_url && !platform.proxy_enabled)),
      );
      rows.append(container);
    }
    card.append(rows);
    return card;
  }

  return () => {
    const groups = state.snapshot?.groups;
    if (!groups) return;
    const values = Object.fromEntries(Object.values(groups).flat().flatMap(
      ({ fields }) => fields.map(({ key, value }) => [key, value]),
    ));
    for (const [name, title] of Object.entries(TITLES)) {
      const panel = document.getElementById(`${name}-content`);
      const content = document.createDocumentFragment();
      const header = element("div", { class: "detail-header" });
      header.append(element("h2", {}, title), element("p", { class: "description" },
        name === "ai"
          ? "开关和下拉框即时应用，输入框按 Enter 应用。提示词可用 Shift + Enter 换行。"
          : "开关和下拉框即时应用，输入框按 Enter 应用。"));
      content.append(header);
      for (const group of groups[name] || []) {
        const card = element("section", { class: "card" });
        card.append(element("h3", {}, group.title));
        const fields = element("div", { class: "field-list" });
        fields.append(...group.fields.map((field) => row(field, values)));
        card.append(fields);
        content.append(card);
      }
      if (name === "network") content.append(proxyCard(state.snapshot.platforms, values));
      panel.replaceChildren(content);
    }
  };
}
