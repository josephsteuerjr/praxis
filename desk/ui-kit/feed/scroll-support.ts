import { t as uiText } from "../../lang/lang";
export interface ScrollSupport { hold: "unverified" | "unavailable"; source: "macos" | "wayland" | "x11"; }

export function scrollSupportText(support?: ScrollSupport): string {
  if (!support) return "";
  if (support.hold === "unavailable") return uiText("ui.ui.kit.feed.scroll.support.f9dbb70ebe");
  return uiText("ui.ui.kit.feed.scroll.support.2019c0c170");
}
