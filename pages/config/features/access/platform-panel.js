import { element, platformListRow, toggle } from "../../shared/controls.js";

export function platformPermissions({ platforms, current, select, selected, change, locked, editingTemplate }) {
  const pane = element("div", { class: "platform-panel access-platform-panel" });
  const sidebar = element("aside", { class: "sidebar", "aria-label": "平台列表" });
  const list = element("div", { class: "platform-list scroll-area", id: "access-platform-list", tabindex: "0" });
  sidebar.append(element("h2", {}, "平台列表"), list);
  for (const platform of platforms) {
    const keys = platform.parse_types.map(({ key }) => `${platform.key}/${key}`);
    const selectedCount = keys.filter(selected).length;
    const platformToggle = toggle(
      `access-switch-${platform.key}`,
      `启用${platform.name}的平台规则`,
      selectedCount > 0,
      (value) => change(keys, value, `access-switch-${platform.key}`),
      locked || (!editingTemplate && !platform.enabled) || !platform.parse_type_controls_ready || !keys.length,
    );
    const subtitle = selectedCount === keys.length && keys.length
      ? "全部类型"
      : selectedCount
        ? `已选 ${selectedCount}/${keys.length} 种类型`
        : "未纳入规则";
    list.append(platformListRow({
      id: `access-platform-${platform.key}`, name: platform.name,
      subtitle,
      selected: platform.key === current.key,
      onSelect: () => select(platform.key),
      control: platformToggle,
    }));
  }

  const detail = element("div", { class: "detail scroll-area", id: "access-platform-detail", tabindex: "0", "aria-label": "平台权限详情" });
  const content = element("div", { class: "detail-content" });
  const header = element("header", { class: "detail-header" });
  header.append(element("h2", {}, current.name));
  if (!current.enabled) header.append(element("p", { class: "description" }, "该平台已在「平台配置」中关闭。"));
  content.append(header);
  const card = element("section", { class: "card" });
  card.append(element("h3", {}, "解析类型"));
  const rows = element("div", { class: "field-list" });
  for (const type of current.parse_types) {
    const key = `${current.key}/${type.key}`;
    const id = `access-switch-${key}`;
    const row = element("div", { class: "field-row" });
    row.append(element("label", { for: id }, type.name), toggle(
      id, type.name, selected(key), (value) => change([key], value, id),
      locked || (!editingTemplate && (!current.enabled || !type.enabled)) || !current.parse_type_controls_ready,
    ));
    rows.append(row);
  }
  card.append(rows);
  content.append(card);
  detail.append(content);
  pane.append(sidebar, detail);
  return pane;
}
