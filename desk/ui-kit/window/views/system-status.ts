import { t as uiText } from "../../../lang/lang";
import type { ComputerLive } from "../mode";

/** A startup anatomy snapshot cannot establish current driver availability. */
export function computerOverview(config: { enabled?: boolean; scopes?: string[] } | undefined,
                                 live: ComputerLive | undefined, now = Date.now()) {
  const scopes = Array.isArray(config?.scopes) ? config.scopes.filter((v) => typeof v === "string") : [];
  if (config?.enabled === false) return { status: uiText("ui.ui.kit.window.views.system.status.aa566c9416"), scopes };
  if (!live || !Object.keys(live).length) return { status: uiText("ui.ui.kit.window.views.system.status.aceba1cb71"), scopes };
  const written = Date.parse(live.written_at || "");
  if (!Number.isFinite(written) || now - written > 30_000 || written > now + 5_000) return { status: uiText("ui.ui.kit.window.views.system.status.fee7e7b989"), scopes };
  if (live.enabled === false) return { status: uiText("ui.ui.kit.window.views.system.status.aa566c9416"), scopes };
  if (live.available === false) return { status: uiText("ui.ui.kit.window.views.system.status.944c4d18cb"), scopes };
  const checked = Date.parse(live.checked_at || "");
  if (!Number.isFinite(checked) || now - checked > 30_000 || checked > now + 5_000) return { status: uiText("ui.ui.kit.window.views.system.status.92b3c146e0"), scopes };
  if (live.platform === "linux" && live.connected === true) {
    if (live.console === false) return { status: uiText("ui.ui.kit.window.views.system.status.2cd314741b"), scopes };
    if (live.console !== true) return { status: uiText("ui.ui.kit.window.views.system.status.bb9f3dc154"), scopes };
  }
  return { status: live.connected === true ? uiText("ui.ui.kit.window.views.system.status.e6ae02f6d3") : live.connected === false ? uiText("ui.ui.kit.window.views.system.status.b22388d12b") : uiText("ui.ui.kit.window.views.system.status.92b3c146e0"), scopes };
}
