import { action, templateReferenceCount } from "./group-lists.js";
import { platformPermissions } from "./platform-panel.js";
import { createGroupPicker } from "./target-forms.js";
import { element, platformListRow, selectControl, submitForm, submitOnEnter } from "../../shared/controls.js";

const FIXED = [["global", "全局设置"], ["private", "所有私聊"], ["templates", "模板管理"]];
const MODES = { off: "关闭", blacklist: "黑名单模式", whitelist: "白名单模式" };
const SCROLL_IDS = ["access-list", "access-platform-list", "access-platform-detail", "access-detail"];
const groupView = (umo) => `group:${umo}`;

export function createAccessPanel(state, submit, listGroups) {
  const panel = document.querySelector("#access-panel");
  const picker = document.querySelector("#access-picker");
  const list = document.querySelector("#access-list");
  const workspace = document.querySelector("#access-workspace");
  let view = "directory";
  let platformKey = "";
  let addTemplateId = "";
  let confirmTargetRemoval = false;
  let confirmTemplateRemoval = false;
  const groupPicker = createGroupPicker({ listGroups, add: addGroup });
  const settings = () => state.snapshot.access_rules;
  const templateOptions = (data) => Object.entries(data.templates).map(([id, item]) => [id, item.name]);
  const isTemplateView = () => view === "templates" || view.startsWith("template:") || view === "template-new";

  function navigate(next, focus, resetScroll = true) {
    if (state.busy) return;
    view = next;
    confirmTargetRemoval = confirmTemplateRemoval = false;
    if (next === "add") addTemplateId = settings().default_template_id;
    render(resetScroll);
    const node = document.getElementById(focus);
    const fallback = workspace.querySelector("#access-qq-instance, #access-umo") || workspace.querySelector(".access-back");
    (node?.getClientRects().length ? node : fallback)?.focus({ preventScroll: true });
  }

  function save(next, id, applied) {
    submit({ access_rules: next }, applied, id);
  }

  function addGroup(umo, label, applied) {
    const data = settings();
    if (Object.hasOwn(data.groups, umo)) {
      navigate(groupView(umo));
      return;
    }
    save({
      ...data,
      groups: {
        ...data.groups,
        [umo]: { label, template_id: Object.hasOwn(data.templates, addTemplateId) ? addTemplateId : data.default_template_id, rules: null },
      },
    }, "access-source-group", () => {
      applied();
      view = groupView(umo);
    });
  }

  function selectedRules(data, entry) {
    if (!entry) return [];
    return entry.template_id ? data.templates[entry.template_id]?.rules || [] : entry.rules;
  }

  function render(reset = false) {
    if (!state.snapshot?.access_rules) return;
    const scrolls = SCROLL_IDS.map((id) => [id, document.getElementById(id)?.scrollTop || 0]);
    const data = settings();
    const locked = state.busy || state.reloadRequired;
    const umo = view.startsWith("group:") ? view.slice("group:".length) : "";
    const group = Object.hasOwn(data.groups, umo) ? data.groups[umo] : null;
    const templateId = view.startsWith("template:") ? view.slice("template:".length) : "";
    const template = Object.hasOwn(data.templates, templateId) ? data.templates[templateId] : null;
    if (view.startsWith("group:") && !group) view = "directory";
    if (view.startsWith("template:") && !template) view = "templates";
    panel.dataset.view = view === "add" ? "add" : ["directory", "templates"].includes(view) ? "directory" : "detail";
    picker.replaceChildren();
    list.replaceChildren();
    workspace.replaceChildren();

    const nav = (key, label, id, subtitle) => platformListRow({
      id,
      name: label,
      subtitle,
      selected: view === key || (view === "directory" && key === "global"),
      onSelect: () => navigate(key, id),
    });
    for (const [key, label] of FIXED) picker.append(nav(key, label, `access-${key}`));
    const heading = element("div", { class: "access-list-heading" });
    const templatesActive = isTemplateView();
    const title = templatesActive ? "模板列表" : "群聊列表";
    heading.append(element("h2", {}, title));
    heading.append(action("access-add", templatesActive ? "新增模板" : "添加群聊", () => navigate(templatesActive ? "template-new" : "add", templatesActive ? "access-template-name" : "access-qq-instance"), locked));
    picker.append(heading);

    list.setAttribute("aria-label", title);
    if (templatesActive) {
      for (const [id, item] of Object.entries(data.templates)) {
        const badge = id === data.default_template_id ? "默认 · " : "";
        list.append(nav(`template:${id}`, item.name, `access-template-${id}`, `${badge}${templateReferenceCount(data, id)} 个名单项使用`));
      }
    } else {
      const groups = Object.entries(data.groups);
      groups.forEach(([umo, entry], index) => list.append(nav(groupView(umo), entry.label || umo, `access-target-${index}`, entry.label ? umo : "")));
      if (!groups.length) list.append(element("p", { class: "description access-empty" }, "暂无群聊。"));
    }

    if (panel.dataset.view === "detail" && view !== "add") {
      const back = action("access-back", "‹ 返回", () => navigate(templatesActive ? "templates" : "directory", "access-add"));
      back.classList.add("access-back");
      if (view === "template-new") back.classList.add("access-back-always");
      workspace.append(back);
    }

    if (["directory", "global", "add", "template-new", "templates"].includes(view)) {
      renderOverview(data, locked);
    } else if (template) {
      renderTemplate(data, template, templateId, locked);
    } else if (view === "private" && !data.private) {
      renderEmptyPrivate(data, locked);
    } else if (group || view === "private") {
      renderTarget(data, group, group ? umo : "private", locked);
    }

    for (const [id, position] of scrolls) {
      const area = document.getElementById(id);
      if (area) area.scrollTop = reset && id.endsWith("detail") ? 0 : position;
    }
  }

  function detail(title) {
    const area = element("div", { class: "detail scroll-area", id: "access-detail", tabindex: "0" });
    const content = element("div", { class: "detail-content" });
    const heading = element("header", { class: "detail-header" });
    heading.append(element("h2", {}, title));
    content.append(heading);
    area.append(content);
    workspace.append(area);
    return content;
  }

  function renderOverview(data, locked) {
    if (view === "global" || view === "directory") {
      const content = detail("全局设置");
      const card = element("section", { class: "card" });
      const mode = element("div", { class: "field-row settings-row" });
      mode.append(element("label", { for: "access-mode" }, "名单模式"),
        selectControl("access-mode", Object.entries(MODES), data.mode, (value) => save({ ...data, mode: value }, "access-mode"), locked));
      const modeDescription = {
        off: "名单规则暂停生效，已保存的群聊和私聊模板保持不变。",
        blacklist: "名单项勾选的解析类型将被禁止；名单外会话不受名单限制。",
        whitelist: "仅名单项勾选的解析类型允许解析；名单外会话全部禁止。",
      };
      const defaultRow = element("div", { class: "field-row settings-row" });
      defaultRow.append(element("label", { for: "access-default-template" }, "新增名单默认模板"));
      const selector = selectControl("access-default-template", templateOptions(data), data.default_template_id, (id) => save({ ...data, default_template_id: id }, "access-default-template"), locked);
      defaultRow.append(selector);
      card.append(mode, element("p", { class: "description" }, modeDescription[data.mode]), defaultRow);
      content.append(card);
      return;
    }
    if (view === "templates") {
      detail("模板管理");
      return;
    }
    if (view === "add") {
      const content = detail("添加群聊");
      const card = element("section", { class: "card" });
      const row = element("div", { class: "field-row settings-row" });
      row.append(element("label", { for: "access-add-template" }, "应用模板"));
      row.append(selectControl("access-add-template", templateOptions(data), Object.hasOwn(data.templates, addTemplateId) ? addTemplateId : data.default_template_id, (id) => { addTemplateId = id; }, locked));
      card.append(row);
      content.append(card, groupPicker(state.snapshot.adapters || [], data.groups, locked));
      return;
    }
    if (view === "template-new") {
      const content = detail("新增模板");
      const card = element("section", { class: "card" });
      card.append(nameForm("access-template-name", "", "创建", locked, (name) => {
        // AstrBot 的嵌入页也可以通过非 HTTPS 地址打开。
        const id = `t-${Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
        save({ ...data, templates: { ...data.templates, [id]: { name, rules: [] } } }, "access-template-rename", () => { view = `template:${id}`; });
      }));
      content.append(card);
    }
  }

  function renderEmptyPrivate(data, locked) {
    const content = detail("所有私聊");
    const status = data.mode === "whitelist" ? "所有私聊均禁止解析" : "私聊按平台配置解析";
    content.append(element("p", { class: "description" }, `未加入名单 · ${status}`),
      action("access-private-add", "加入名单", () => save({ ...data, private: { template_id: data.default_template_id, rules: null } }, "access-source-private"), locked));
  }

  function nameForm(id, value, buttonLabel, locked, update) {
    const form = element("form", { class: "access-name-form", autocomplete: "off" });
    const input = element("input", { id, class: "input", required: "", maxlength: "48" });
    input.value = value;
    input.disabled = locked;
    input.addEventListener("input", () => input.setCustomValidity(""));
    input.addEventListener("keydown", submitOnEnter(form));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const name = input.value.trim();
      if (!name) {
        input.setCustomValidity("请输入模板名称");
        input.reportValidity();
      } else if (name !== value) {
        update(name);
      }
    });
    form.append(element("label", { for: id, class: "description" }, "模板名称"), input,
      action(`${id}-submit`, buttonLabel, () => submitForm(form), locked));
    return form;
  }

  function renderPermissions(rules, locked, onChange, editingTemplate = false) {
    const platforms = state.snapshot.platforms;
    const current = platforms.find(({ key }) => key === platformKey) || platforms[0];
    if (!current) return;
    platformKey = current.key;
    workspace.append(platformPermissions({
      platforms,
      current,
      select(key) { platformKey = key; render(false); },
      selected: (key) => rules.includes(key),
      change(keys, enabled, controlId) {
        if (!onChange) return;
        const nextRules = new Set(rules);
        for (const key of keys) {
          if (enabled) nextRules.add(key);
          else nextRules.delete(key);
        }
        onChange([...nextRules], controlId);
      },
      locked: locked || !onChange,
      editingTemplate,
    }));
  }

  function renderTemplate(data, template, id, locked) {
    const header = element("header", { class: "access-editor-header" });
    const info = element("div");
    const references = templateReferenceCount(data, id);
    const isDefault = data.default_template_id === id;
    const badges = [template.builtin && "内置只读", isDefault && "默认", `${references} 个名单项使用`].filter(Boolean);
    info.append(element("h2", {}, template.name), element("p", { class: "description" }, badges.join(" · ")));
    header.append(info);
    if (!template.builtin && !references && !isDefault) {
      const buttons = element("div", { class: "access-editor-actions" });
      if (confirmTemplateRemoval) {
        buttons.append(action("access-template-delete-cancel", "取消", () => { confirmTemplateRemoval = false; render(); }, locked));
      }
      buttons.append(action("access-template-delete", confirmTemplateRemoval ? "确认删除" : "删除模板", () => {
        if (!confirmTemplateRemoval) {
          confirmTemplateRemoval = true;
          render();
          document.querySelector("#access-template-delete")?.focus({ preventScroll: true });
          return;
        }
        const templates = { ...data.templates };
        delete templates[id];
        save({ ...data, templates }, "access-add", () => { view = "templates"; confirmTemplateRemoval = false; });
      }, locked));
      header.append(buttons);
    }
    workspace.append(header);
    if (!template.builtin) {
      const metadata = element("div", { class: "access-metadata" });
      metadata.append(nameForm("access-template-rename", template.name, "保存名称", locked, (nextName) => {
        save({ ...data, templates: { ...data.templates, [id]: { ...template, name: nextName } } }, "access-template-rename");
      }));
      workspace.append(metadata);
    }
    const updateRules = template.builtin ? null : (rules, controlId) =>
      save({ ...data, templates: { ...data.templates, [id]: { ...template, rules } } }, controlId);
    renderPermissions(template.rules, locked, updateRules, true);
  }

  function renderTarget(data, group, target, locked) {
    const entry = target === "private" ? data.private : group;
    const isGroup = target !== "private";
    const heading = element("header", { class: "access-editor-header" });
    const identity = element("div");
    const title = isGroup ? group.label || target : "所有私聊";
    identity.append(element("h2", {}, title));
    if (isGroup && group.label) identity.append(element("p", { class: "description" }, target));
    const source = entry.template_id ? `跟随模板：${data.templates[entry.template_id].name}` : "自定义规则";
    identity.append(element("p", { class: "description" }, `${data.mode === "off" ? "名单已关闭" : MODES[data.mode]} · ${source}`));
    heading.append(identity);
    if (isGroup) {
      const remove = () => {
        if (!confirmTargetRemoval) {
          confirmTargetRemoval = true;
          render();
          document.querySelector("#access-remove")?.focus({ preventScroll: true });
          return;
        }
        const groups = { ...data.groups };
        delete groups[target];
        save({ ...data, groups }, "access-add", () => { view = "directory"; confirmTargetRemoval = false; });
      };
      const buttons = element("div", { class: "access-editor-actions" });
      if (confirmTargetRemoval) {
        identity.append(element("p", { role: "status" }, "移除后，该群会离开名单；QQ 群目录和模板本身不受影响。"));
        buttons.append(action("access-remove-cancel", "取消", () => { confirmTargetRemoval = false; render(); }, locked));
      }
      buttons.append(action("access-remove", confirmTargetRemoval ? "确认移除" : "移除群聊", remove, locked));
      heading.append(buttons);
    } else {
      const buttons = element("div", { class: "access-editor-actions" });
      if (confirmTargetRemoval) buttons.append(action("access-remove-cancel", "取消", () => { confirmTargetRemoval = false; render(); }, locked));
      buttons.append(action("access-remove", confirmTargetRemoval ? "确认移出" : "移出名单", () => {
        if (!confirmTargetRemoval) {
          confirmTargetRemoval = true;
          render();
          document.querySelector("#access-remove")?.focus({ preventScroll: true });
          return;
        }
        save({ ...data, private: null }, "access-private", () => { confirmTargetRemoval = false; view = "private"; });
      }, locked));
      heading.append(buttons);
    }
    workspace.append(heading);

    const selectorCard = element("section", { class: "card access-source-card" });
    const sourceRow = element("div", { class: "field-row" });
    const selectorId = `access-source-${isGroup ? "group" : "private"}`;
    sourceRow.append(element("label", { for: selectorId }, "规则来源"));
    const options = [["__custom__", "自定义"], ...templateOptions(data)];
    const selector = selectControl(selectorId, options, entry.template_id || "__custom__", (next) => {
      if (next === "__custom__" && entry.template_id) {
        saveTarget({ ...entry, template_id: null, rules: [...selectedRules(data, entry)] });
      } else if (next !== "__custom__" && next !== entry.template_id) {
        saveTarget({ ...entry, template_id: next, rules: null });
      }
    }, locked);
    sourceRow.append(selector);
    selectorCard.append(sourceRow);
    workspace.append(selectorCard);

    renderPermissions(selectedRules(data, entry), locked, (rules, controlId) => {
      const nextEntry = { ...entry, template_id: null, rules };
      saveTarget(nextEntry, controlId);
    });

    function saveTarget(nextEntry, controlId = selectorId) {
      save(isGroup
        ? { ...data, groups: { ...data.groups, [target]: nextEntry } }
        : { ...data, private: nextEntry }, controlId);
    }
  }

  return render;
}
