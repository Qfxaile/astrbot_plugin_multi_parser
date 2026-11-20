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
    form.requestSubmit();
  };
}
