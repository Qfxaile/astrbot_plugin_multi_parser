import { element, platformListRow, submitOnEnter, toggle } from "../../shared/controls.js";

export function createPlatformPanel(state, submit) {
  const drafts = new Map();
  const list = document.querySelector("#platform-list");
  const detail = document.querySelector("#detail");
  const scroll = document.querySelector("#detail-scroll");
  const locked = () => state.busy || state.reloadRequired;

  function render() {
    const platforms = state.snapshot?.platforms || [];
    const listScroll = list.scrollTop;
    list.replaceChildren();

    for (const platform of platforms) {
      list.append(platformListRow({
        id: `select-${platform.key}`,
        name: platform.name,
        selected: platform.key === state.selected,
        onSelect: () => {
          state.selected = platform.key;
          if (scroll) scroll.scrollTop = 0;
          render();
          document.getElementById(`select-${platform.key}`)?.focus();
        },
        control: toggle(`platform-${platform.key}`, `启用${platform.name}`, platform.enabled,
          (enabled) => submit({ platform_switches: { [platform.key]: enabled } }), locked()),
      }));
    }
    list.scrollTop = listScroll;

    const platform = platforms.find((item) => item.key === state.selected);
    if (platform) renderDetail(platform);
  }

  function createCredentialField({ key, label, value, maxlength, placeholder, multiline = false }) {
    const input = element(multiline ? "textarea" : "input", {
      class: multiline ? "input credential-input credential-textarea" : "input credential-input",
      ...(multiline ? { rows: "4" } : { type: "text" }),
      id: key,
      autocomplete: "off",
      spellcheck: "false",
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
        !ready || !platform.enabled || locked(),
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
    input.disabled = locked();
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
    input.disabled = locked();
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

  return render;
}
