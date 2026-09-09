// Мелкие общие вещи окна. Сами обёртки живут в ui-kit (одна копия на окно,
// установщик, телефон и мини-апп); здесь — только реэкспорт, чтобы экраны
// импортировали из одного места.
export { esc, fmtAge, fmtDay, fmtDur, fmtK, fmtN, fmtTime, fmtTimeSec, fmtTs, humanError, md, plural } from "../../ui-kit/text";
export type { HumanError } from "../../ui-kit/text";
export { bindFail, el, failHTML, icon, q, safeRender, toast } from "../../ui-kit/dom";
