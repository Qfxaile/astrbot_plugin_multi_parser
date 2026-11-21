import { element } from "../../shared/controls.js";

export function action(id, label, onClick, disabled = false) {
  const button = element("button", { id, type: "button", class: "button" }, label);
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  return button;
}

export function templateReferenceCount(data, id) {
  return Object.values(data.groups).filter((entry) => entry.template_id === id).length
    + Number(data.private?.template_id === id);
}
