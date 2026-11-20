const bridge = window.AstrBotPluginPage;
const state = { snapshot: null, selected: "", busy: false, reloadRequired: false };
const drafts = new Map();
const list = document.querySelector("#platform-list");
const detail = document.querySelector("#detail");
const status = document.querySelector("#status");
const retry = document.querySelector("#retry");

const ICON_PATHS = {
  settings: '<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.64 5.64l2.12 2.12m8.48 8.48 2.12 2.12m0-12.72-2.12 2.12M7.76 16.24l-2.12 2.12"/><circle cx="12" cy="12" r="3.5"/>',
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  brain: '<path d="M9.5 4.5A3.5 3.5 0 0 0 6 8v.5A3.5 3.5 0 0 0 4 15a3.5 3.5 0 0 0 5.5 2.8V4.5ZM14.5 4.5A3.5 3.5 0 0 1 18 8v.5a3.5 3.5 0 0 1 2 6.5 3.5 3.5 0 0 1-5.5 2.8V4.5ZM12 4v16M8 9h4m4 0h-4m-4 5h4m4 0h-4"/>',
  shield: '<path d="M12 3 20 6v5c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-3Z"/><path d="m9 12 2 2 4-4"/>',
  network: '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4m-6 4v-2h12v2"/>',
  "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
};

function hydrateIcons() {
  document.querySelectorAll("[data-icon]").forEach((slot) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICON_PATHS[slot.dataset.icon] || ICON_PATHS.globe;
    slot.replaceChildren(svg);
  });
}

function element(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}

function setStatus(message, error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}

function applySnapshot(snapshot) {
  state.snapshot = snapshot;
  if (!snapshot.platforms.some((item) => item.key === state.selected)) {
    state.selected = snapshot.platforms[0]?.key || "";
  }
  document.querySelector("#scope").textContent = snapshot.scope;
}

function render() {
  const platforms = state.snapshot?.platforms || [];
  const activeId = document.activeElement.id;
  const listScroll = list.scrollTop;
  list.replaceChildren();

  for (const platform of platforms) {
    const row = element("div", { class: "platform-row" });
    const select = element("button", {
      class: "select-platform", type: "button", id: `select-${platform.key}`,
      "aria-current": String(platform.key === state.selected),
    });
    select.append(element("span", {}, platform.name));
    select.addEventListener("click", () => {
      state.selected = platform.key;
      render();
    });
    row.append(select, toggle(`platform-${platform.key}`, `启用${platform.name}`, platform.enabled,
      (enabled) => submit({ platform_switches: { [platform.key]: enabled } })));
    list.append(row);
  }
  list.scrollTop = listScroll;

  const platform = platforms.find((item) => item.key === state.selected);
  if (platform) renderDetail(platform);
  if (activeId && !state.busy) document.getElementById(activeId)?.focus({ preventScroll: true });
}

function createCredentialField({ key, label, value, maxlength, placeholder, multiline = false }) {
  const input = element(multiline ? "textarea" : "input", {
    class: multiline ? "input credential-input credential-textarea" : "input credential-input",
    ...(multiline ? { rows: "4" } : { type: "text" }),
    autocomplete: "off",
    maxlength,
    "aria-label": label,
    placeholder,
  });
  const draft = drafts.get(key);
  input.value = draft ?? value;
  input.addEventListener("input", () => {
    drafts.set(key, input.value);
  });
  return input;
}

function toggle(id, label, enabled, onChange, unavailable = false) {
  const control = element("input", {
    id, class: "switch", type: "checkbox", role: "switch", "aria-label": label,
  });
  control.checked = enabled;
  control.disabled = unavailable || state.busy || state.reloadRequired;
  control.addEventListener("change", () => onChange(control.checked));
  return control;
}

function renderDetail(platform) {
  const heading = element("div", { class: "detail-header" });
  heading.append(element("h2", {}, platform.name));
  const ready = platform.parse_type_controls_ready === true;
  if (!ready || !platform.enabled) {
    heading.append(element("p", { class: "description" }, ready
      ? "平台已停用，重新启用后会恢复解析类型设置。"
      : "此平台的细分开关尚未接入解析流程。"));
  }

  const features = element("section", { class: "card" });
  features.append(element("h3", {}, "解析类型"));
  const rows = element("div", { class: "field-list" });
  for (const feature of platform.parse_types) {
    const row = element("div", { class: "field-row" });
    const id = `feature-${platform.key}-${feature.key}`;
    row.append(element("label", { for: id }, feature.name), toggle(
      id, `启用${platform.name}${feature.name}`, feature.enabled,
      (enabled) => submit({ parse_types: { [platform.key]: { [feature.key]: enabled } } }),
      !ready || !platform.enabled,
    ));
    rows.append(row);
  }
  features.append(rows);
  detail.replaceChildren(heading, features);
  if (platform.cookie.supported) detail.append(cookieForm(platform));
  if (platform.token) detail.append(tokenForm(platform));
}

function cookieForm(platform) {
  const section = element("section", { class: "card" });
  const title = platform.key === "wechat" ? "腾讯元宝 Cookies" : "Cookies";
  section.append(element("h3", {}, title));
  section.append(element("p", { class: "description" }, platform.cookie.value
    ? "已配置明文 Cookies。修改后按 Enter 立即替换。"
    : "选填，用于需要登录态的平台内容解析；输入后按 Enter 应用。"));

  const form = element("form", { autocomplete: "off" });
  const input = createCredentialField({
    key: `cookie:${platform.key}`,
    label: `${platform.name} ${title}`,
    value: platform.cookie.value,
    maxlength: "8192",
    placeholder: "输入完整 Cookies 后按 Enter 应用",
    multiline: true,
  });
  input.disabled = state.busy || state.reloadRequired;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (value) {
      submit({ cookies: { [platform.key]: value } }, () => drafts.delete(`cookie:${platform.key}`));
    } else if (platform.cookie.value) {
      submit({ clear_cookies: [platform.key] }, () => drafts.delete(`cookie:${platform.key}`));
    }
  });
  input.addEventListener("keydown", submitOnEnter(form));
  form.append(input);
  section.append(form);
  return section;
}

function tokenForm(platform) {
  const section = element("section", { class: "card" });
  section.append(element("h3", {}, "GitHub Token"));
  section.append(element("p", { class: "description" }, platform.token.value
    ? "已配置明文 Token；修改后按 Enter 应用。"
    : "选填，用于提高 GitHub 请求速率限制；输入后按 Enter 应用。"));

  const form = element("form", { autocomplete: "off" });
  const input = createCredentialField({
    key: "github_token",
    label: "GitHub Token",
    value: platform.token.value,
    maxlength: "512",
    placeholder: "输入 Token 后按 Enter 应用",
  });
  input.disabled = state.busy || state.reloadRequired;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (value || platform.token.value) {
      submit({ github_token: value }, () => drafts.delete("github_token"));
    }
  });
  input.addEventListener("keydown", submitOnEnter(form));
  form.append(input);
  section.append(form);
  return section;
}

function submitOnEnter(form) {
  return (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!state.busy && !state.reloadRequired) form.requestSubmit();
  };
}

async function submit(change, onApplied) {
  if (state.busy || state.reloadRequired) return;
  const activeId = document.activeElement.id;
  state.busy = true;
  document.querySelectorAll("main input, main textarea, main form button").forEach((control) => { control.disabled = true; });
  setStatus("正在应用…");
  try {
    applySnapshot(await bridge.apiPost("settings", { revision: state.snapshot.revision, ...change }));
    onApplied?.();
    setStatus("已生效");
  } catch {
    await refreshAfterFailure();
  } finally {
    state.busy = false;
    const restoreFocus = document.activeElement === document.body;
    render();
    if (restoreFocus && activeId) document.getElementById(activeId)?.focus({ preventScroll: true });
  }
}

async function refreshAfterFailure() {
  try {
    applySnapshot(await bridge.apiGet("settings"));
    setStatus("提交未确认，已同步当前设置。请核对后重试。", true);
  } catch {
    state.reloadRequired = true;
    retry.hidden = false;
    setStatus("提交未确认，请重新读取设置。", true);
  }
}

async function load() {
  retry.hidden = true;
  setStatus("正在读取…");
  try {
    if (!bridge) throw new Error("请从 AstrBot 插件管理打开此页面。");
    await bridge.ready();
    applySnapshot(await bridge.apiGet("settings"));
    state.reloadRequired = false;
    render();
    setStatus("更改即时生效");
  } catch (error) {
    if (!state.snapshot) detail.replaceChildren(element("p", { class: "description" }, error.message));
    setStatus("无法连接设置服务", true);
    retry.hidden = false;
  }
}

retry.addEventListener("click", load);
hydrateIcons();
bridge?.onContext?.((context) => {
  if (typeof context?.isDark === "boolean") {
    document.documentElement.dataset.theme = context.isDark ? "dark" : "light";
  }
});
load();
