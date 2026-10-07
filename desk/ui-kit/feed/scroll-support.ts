export interface ScrollSupport { hold: "unverified" | "unavailable"; source: "macos" | "wayland" | "x11"; }

export function scrollSupportText(support?: ScrollSupport): string {
  if (!support) return "";
  if (support.hold === "unavailable") return "Удержание оттяжки неподвижными пальцами недоступно в этом окружении.";
  return "Удержание оттяжки неподвижными пальцами не проверено на реальном тачпаде этой платформы.";
}
