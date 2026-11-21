import { element } from "./shared/controls.js";
import { hydrateIcons } from "./shared/icons.js";
import { createPlatformPanel } from "./features/platforms/panel.js";
import { createSettingsPanels } from "./features/settings/panels.js";
import { createAccessPanel } from "./features/access/panel.js";

const bridge = window.AstrBotPluginPage;
const state = { snapshot: null, selected: "", busy: false, reloadRequired: false };
const status = document.querySelector("#status");
const retry = document.querySelector("#retry");
const tabs = [...document.querySelectorAll('[role="tab"]:not(:disabled)')];
const renderPlatforms = createPlatformPanel(state, submit);
const renderSettings = createSettingsPanels(state, submit);
const renderAccess = createAccessPanel(state, submit, (instance) => bridge.apiPost("qq-groups", { instance }));

function setStatus(message, error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}

function applySnapshot(snapshot) {
  if (!Array.isArray(snapshot?.platforms)) throw new Error("设置响应格式无效");
  state.snapshot = snapshot;
  if (!snapshot.platforms.some((item) => item.key === state.selected)) {
    state.selected = snapshot.platforms[0]?.key || "";
  }
  document.querySelector("#scope").textContent = snapshot.scope;
}

function activate(tab) {
  for (const item of tabs) {
    const selected = item === tab;
    item.setAttribute("aria-selected", selected);
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
  }
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => activate(tab));
  tab.addEventListener("keydown", (event) => {
    const nextIndex = {
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      ArrowRight: (index + 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    activate(tabs[nextIndex]);
    tabs[nextIndex].focus();
  });
}

function render() {
  const activeId = document.activeElement.id;
  renderPlatforms();
  renderSettings();
  renderAccess();
  if (activeId) document.getElementById(activeId)?.focus({ preventScroll: true });
}

async function submit(change, onApplied, controlId) {
  if (!state.snapshot || state.busy || state.reloadRequired) return;
  const activeId = controlId || document.activeElement.id;
  state.busy = true;
  document.querySelectorAll("main input, main textarea, main select, main form button, #access-panel button").forEach((control) => { control.disabled = true; });
  setStatus("正在应用…");
  try {
    const settings = await bridge.apiPost("settings", { revision: state.snapshot.revision, ...change });
    applySnapshot({ ...settings, providers: state.snapshot.providers, adapters: state.snapshot.adapters });
    onApplied?.();
    setStatus("已生效");
  } catch {
    await refreshAfterFailure();
  } finally {
    state.busy = false;
    render();
    if (document.activeElement === document.body && activeId) {
      document.getElementById(activeId)?.focus({ preventScroll: true });
    }
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
    if (!state.snapshot) document.querySelector("#detail").replaceChildren(element("p", { class: "description" }, error.message));
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
