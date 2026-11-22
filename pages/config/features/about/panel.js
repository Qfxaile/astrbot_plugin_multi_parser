import { element } from "../../shared/controls.js";
import { hydrateIcons } from "../../shared/icons.js";

let pending;

export function loadAbout(bridge) {
  pending ??= readAbout(bridge).finally(() => { pending = undefined; });
  return pending;
}

async function readAbout(bridge) {
  const root = document.querySelector("#about-content");
  const state = element("div", { class: "about-state" });
  const status = element("p", { class: "description", role: "status" }, "正在读取插件信息…");
  state.append(status);
  root.replaceChildren(state);
  try {
    const info = await bridge.apiGet("about");
    if (!info || !Array.isArray(info.updates)) throw new Error("插件信息格式无效");
    root.replaceChildren(pluginIdentity(info), updatesPanel(info.updates, info.version));
    hydrateIcons(root);
  } catch {
    status.setAttribute("role", "alert");
    status.textContent = "插件信息读取失败。";
    const retry = element("button", { class: "button", type: "button" }, "重试");
    retry.prepend(icon("refresh-cw"));
    retry.addEventListener("click", () => loadAbout(bridge));
    state.append(retry);
    hydrateIcons(state);
  }
}

function pluginIdentity(info) {
  const name = info.name || "多平台内容解析";
  const section = element("section", { class: "about-identity", "aria-labelledby": "about-plugin-name" });
  const brand = element("div", { class: "about-brand" });
  const fallback = icon("layers");
  brand.append(fallback);
  if (info.logo?.startsWith("data:image/png;base64,")) {
    const logo = element("img", { class: "about-logo", alt: "", width: "64", height: "64" });
    logo.addEventListener("error", () => {
      logo.remove();
      fallback.hidden = false;
    }, { once: true });
    fallback.hidden = true;
    logo.src = info.logo;
    brand.append(logo);
  }
  const meta = element("dl", { class: "about-meta" });
  meta.append(metaItem("当前版本", info.version), metaItem("作者", info.author));
  section.append(brand, element("h2", { id: "about-plugin-name" }, name), meta);
  const repo = repositoryAction(info.repo);
  if (repo) section.append(repo);
  return section;
}

function metaItem(label, value) {
  const item = element("div");
  item.append(element("dt", {}, label), element("dd", {}, value || "未知"));
  return item;
}

function repositoryAction(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password) return null;

  // AstrBot 插件页的 iframe 禁止弹窗，桥接接口不提供外链导航。
  if (window.parent !== window) return repositoryCopy(url.href);

  const label = url.hostname === "github.com" ? "GitHub 仓库" : "项目仓库";
  const link = element("a", { class: "button about-repo", href: url.href, target: "_blank", rel: "noopener noreferrer" }, label);
  link.append(icon("external-link"));
  return link;
}

function repositoryCopy(url) {
  const group = element("div", { class: "about-repo-copy" });
  const button = element("button", { class: "button about-repo", type: "button" }, "复制仓库地址");
  button.prepend(icon("copy"));
  const address = element("input", { class: "input", type: "text", readonly: "", hidden: "", "aria-label": "仓库地址" });
  address.value = url;
  const status = element("span", { class: "description", role: "status", hidden: "" });
  group.append(button, address, status);

  button.addEventListener("click", async () => {
    let copied = false;
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch {
      // 不透明来源或 HTTP 部署可能无法使用 Clipboard API。
      address.hidden = false;
      address.focus();
      address.select();
      try {
        copied = document.execCommand("copy");
      } catch {
        // 浏览器拒绝自动复制时，保留选中文本。
      }
    }
    if (copied) {
      if (document.activeElement === address) button.focus();
      address.hidden = true;
    }
    status.textContent = copied ? "仓库地址已复制" : "无法自动复制，地址已选中。";
    status.hidden = false;
  });
  return group;
}

function updatesPanel(updates, installedVersion) {
  const section = element("section", { class: "about-updates", "aria-labelledby": "about-updates-title" });
  section.append(element("h3", { id: "about-updates-title" }, "更新日志"));
  if (!updates.length) {
    section.append(element("p", { class: "description about-empty" }, "暂无更新记录。"));
    return section;
  }

  const history = element("div", { class: "about-history" });
  updates.forEach(({ version, date, notes }, index) => {
    const entry = element("details", { class: "about-release" });
    entry.open = index === 0;
    const header = element("summary");
    const title = element("span", { class: "about-release-title" }, version);
    if (version === installedVersion) title.append(element("span", { class: "about-installed" }, "当前安装"));
    header.append(icon("chevron-down"), title);
    if (date) header.append(element("time", { class: "description", datetime: date }, date));
    const list = element("ul", { class: "about-notes" });
    for (const note of notes) list.append(element("li", {}, note));
    entry.append(header, list);
    history.append(entry);
  });
  section.append(history);
  return section;
}

function icon(name) {
  return element("span", { class: "icon-slot", "data-icon": name, "aria-hidden": "true" });
}
