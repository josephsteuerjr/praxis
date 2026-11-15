import { t as uiText, UiError } from '../lang/lang';

const fields:Record<string,readonly string[]>={
  "channel_missing": [],
  "call_unavailable": [],
  "restore_confirm": [],
  "restore_session": [],
  "linux_migration": [],
  "build_tools": [],
  "agent_missing": [],
  "components_first": [],
  "migration_identity": [],
  "shutdown_incomplete": [
    "detail"
  ],
  "session_admission": [],
  "root_mismatch": [],
  "window_assets": [
    "path"
  ],
  "interrupted_migration": [],
  "layout_seed": [
    "path",
    "detail"
  ],
  "layout_candidate": [
    "path",
    "detail"
  ],
  "layout_marker_link": [
    "path"
  ],
  "layout_marker_tree": [
    "path"
  ],
  "layout_birth_incomplete": [
    "path"
  ],
  "layout_marker_file": [
    "path"
  ],
  "layout_birth_pending": [
    "path"
  ],
  "layout_birth_state": [
    "path",
    "detail"
  ],
  "layout_folder": [
    "path",
    "detail"
  ],
  "layout_constitution": [
    "path",
    "detail"
  ],
  "layout_skills": [
    "path",
    "detail"
  ],
  "layout_transfer_unreadable": [],
  "layout_transfer_active": [],
  "layout_custody_held": [],
  "layout_custody_previous": [],
  "layout_custody_unconfirmed": [],
  "first_ready": [],
  "first_start_unconfirmed": [],
  "first_action": []
};
const record=(v:unknown):Record<string,unknown>|null=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:null;
const owns=(v:object,key:string)=>Object.prototype.hasOwnProperty.call(v,key);

/** Translate only explicit native-owned failures, keeping all other errors literal. */
export function nativeError(error:unknown):unknown {
  const caption=record(record(error)?.native_error);
  if(!caption||caption.schema!=='helene.native-error.v1'||typeof caption.code!=='string'||!owns(fields,caption.code))return error;
  const values=record(caption.values),expected=fields[caption.code];
  if(!values||Object.keys(values).length!==expected.length||expected.some(key=>!owns(values,key)||!['string','number'].includes(typeof values[key])))return error;
  const params:Record<string,string|number>={};
  for(const key of expected)params[key]=values[key] as string|number;
  return new UiError(uiText('native.app.error.'+caption.code,params));
}
