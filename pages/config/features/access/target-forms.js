import { element, selectControl, submitForm, submitOnEnter } from "../../shared/controls.js";

export function createGroupPicker({ listGroups, add }) {
  let adapterId = "";
  let pending;
  let directory;
  let error;
  let draft = "";
  let aliasDraft = "";
  let selected = "";
  let renderResult;

  async function load(id) {
    const request = {};
    pending = request;
    directory = error = undefined;
    selected = "";
    renderResult();
    try {
      const data = await listGroups(id);
      if (pending === request) directory = data;
    } catch {
      if (pending === request) error = "群列表读取失败。请重试，或直接填写群聊 UMO。";
    } finally {
      if (pending === request) {
        pending = undefined;
        renderResult();
      }
    }
  }

  return function render(adapters, entries, locked, toList = false) {
    const fragment = document.createDocumentFragment();
    if (adapters.length) {
      const autoLoad = adapters.length === 1 && !adapterId;
      if (autoLoad) adapterId = adapters[0].id;
      const card = element("section", { class: "card" });
      card.append(element("h3", {}, "从 QQ 群列表选择"));
      const controls = element("div", { class: "group-picker-controls" });
      const adapter = selectControl("access-qq-instance", [["", "选择 QQ 适配器"], ...adapters.map(({ id, name }) => [id, `${name} · ${id}`])], adapterId, (id) => {
        adapterId = id;
        if (id) load(id);
        else {
          pending = directory = error = undefined;
          renderResult();
        }
      }, locked);
      adapter.firstElementChild.setAttribute("aria-label", "QQ群适配器");
      const retry = element("button", { type: "button", class: "button" }, "刷新");
      retry.addEventListener("click", () => load(adapterId));
      controls.append(adapter, retry);
      const results = element("div", { class: "group-picker-results" });
      renderResult = () => {
        retry.disabled = locked || !adapterId || Boolean(pending);
        results.replaceChildren();
        if (pending || error || !directory) {
          if (pending || error) results.append(element("p", { class: "description", role: "status" }, pending ? "正在读取群列表…" : error));
          return;
        }
        if (directory.source === "observed") results.append(element("p", { class: "description" }, "QQ 官方仅列出当前适配器已收到过消息的群。"));
        const available = directory.groups.filter(({ umo }) => !Object.hasOwn(entries, umo));
        if (!available.length) {
          results.append(element("p", { class: "description" }, "暂无可添加的群聊。"));
          return;
        }
        const actions = element("div", { class: "group-picker-controls" });
        const submit = element("button", { type: "button", class: "button primary" }, toList ? "加入名单" : "添加群聊");
        submit.disabled = locked || !selected;
        submit.addEventListener("click", () => {
          const entry = available.find(({ umo }) => umo === selected);
          if (entry) add(entry.umo, entry.label, () => { selected = ""; });
        });
        const groups = selectControl("access-qq-group", [["", "选择群聊"], ...available.map(({ umo, id, label }) => [umo, label ? `${label} · ${id}` : id])], selected, (value) => {
          selected = value;
          submit.disabled = !value;
        }, locked);
        groups.firstElementChild.setAttribute("aria-label", "QQ 群聊");
        actions.append(groups, submit);
        results.append(actions);
      };
      card.append(controls, results);
      if (autoLoad) load(adapterId);
      else renderResult();
      fragment.append(card);
    }

    const manual = element("section", { class: "card" });
    const form = element("form", { class: "manual-group-form", autocomplete: "off" });
    const input = element("input", { id: "access-umo", class: "input", required: "", maxlength: "384", placeholder: "实例ID:GroupMessage:会话ID", spellcheck: "false" });
    input.value = draft;
    input.disabled = locked;
    input.addEventListener("input", () => { draft = input.value; input.setCustomValidity(""); });
    input.addEventListener("keydown", submitOnEnter(form));
    const alias = element("input", { id: "access-alias", class: "input", maxlength: "80", placeholder: "可选" });
    alias.value = aliasDraft;
    alias.disabled = locked;
    alias.addEventListener("input", () => { aliasDraft = alias.value; });
    alias.addEventListener("keydown", submitOnEnter(form));
    const submit = element("button", { type: "button", class: "button primary" }, toList ? "加入名单" : "添加群聊");
    submit.disabled = locked;
    submit.addEventListener("click", () => submitForm(form));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const umo = input.value.trim();
      const match = /^([^:\s]+):GroupMessage:(\S+)$/u.exec(umo);
      if (!match) {
        input.setCustomValidity("请粘贴完整群聊 UMO，不支持添加单独的私聊会话。");
        input.reportValidity();
        return;
      }
      const adapter = adapters.find(({ id }) => id === match[1]);
      const qqSession = adapter?.type === "aiocqhttp" ? /^\d+(?:_\d+)?$/ : /^(?:[a-z0-9]+_)?[a-z0-9]+$/i;
      const key = adapter && qqSession.test(match[2]) ? `${match[1]}:GroupMessage:${match[2].split("_").at(-1)}` : umo;
      add(key, alias.value.trim(), () => { draft = aliasDraft = ""; });
    });
    manual.append(element("h3", {}, "手动添加群聊"), element("p", { class: "description" }, "适用于其他平台，也可直接填写未出现在列表中的群聊。"));
    const fields = element("div", { class: "manual-group-fields" });
    const umoField = element("div", { class: "manual-group-field" });
    umoField.append(element("label", { for: input.id }, "群聊 UMO"), input);
    const aliasField = element("div", { class: "manual-group-field" });
    aliasField.append(element("label", { for: alias.id }, "自定义别名"), alias);
    fields.append(umoField, aliasField);
    form.append(fields, submit);
    manual.append(form);
    fragment.append(manual);
    return fragment;
  };
}
