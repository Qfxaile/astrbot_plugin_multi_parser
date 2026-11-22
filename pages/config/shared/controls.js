export function element(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}

export function toggle(id, label, enabled, onChange, unavailable = false) {
  const control = element("input", {
    id, class: "switch", type: "checkbox", role: "switch", "aria-label": label,
  });
  control.checked = enabled;
  control.disabled = unavailable;
  control.addEventListener("change", () => onChange(control.checked));
  return control;
}

export function submitOnEnter(form) {
  return (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submitForm(form);
  };
}

export function submitForm(form) {
  // 插件页面的沙箱可以禁止原生表单提交。保存由设置桥接 API 处理。
  if (form.reportValidity()) {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }
}

export function selectControl(id, options, value, onChange, disabled = false) {
  const wrapper = element("div", { class: "settings-select" });
  const control = element("select", { id, class: "input settings-input" });
  for (const [key, label] of options) control.append(element("option", { value: key }, label));
  control.value = value;
  control.disabled = disabled;
  control.addEventListener("change", () => onChange(control.value));
  wrapper.append(control);
  return wrapper;
}

export function platformListRow({ id, name, selected, onSelect, control, subtitle }) {
  const row = element("div", { class: "platform-row" });
  const button = element("button", {
    id, class: "select-platform", type: "button", "aria-current": String(selected),
  }, name);
  if (subtitle) button.append(element("span", { class: "platform-row-subtitle description" }, subtitle));
  button.addEventListener("click", onSelect);
  row.append(button);
  if (control) row.append(control);
  return row;
}
