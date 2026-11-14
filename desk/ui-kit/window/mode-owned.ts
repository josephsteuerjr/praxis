import { t } from '../../lang/lang';
import type {ModeState} from './mode';
const KEYS: Record<string,string> = {
  "Песочница": "mode.sandbox",
  "Интерактивный": "mode.interactive",
  "Tools — в контейнере, доступ к папкам за пределами песочницы — по одобрению. Смонтировать папки самостоятельно можно в настройках.": "mode.sandbox.standard",
  "Агент с правами пользователя, права администратора выдаются согласно UAC.": "mode.interactive.windows",
  "Агент с правами пользователя, права администратора выдаются через polkit.": "mode.interactive.linux",
  "Агент заперт в своей папке: файлы и команды дальше дома не идут, наружу — только те папки, что ты смонтировал. Команды идут под оградой seatbelt самой системы, ключи и файл настроек закрыты от них. Снаружи ограды — Forge: он работает в worktree задачи, а тот лежит там, куда ты его завёл. Поимённо, какой тул накрыт, а какой нет, — на экране «Система».": "mode.sandbox.macos",
  "Агент работает с твоими правами: файлы и процессы — те же, что доступны тебе самому, не больше. Файловые тулы видят то же, что и shell, монтировать ничего не нужно. Если учётка ограничена, агент ограничен так же. Прав администратора он не получает: всё, что требует пароля, остаётся за тобой.": "mode.interactive.macos"
};
export function modeOwnedText(value: string): string {return Object.hasOwn(KEYS,value) ? t(KEYS[value]) : value;}
export function localizeMode(state: ModeState | null): ModeState | null {
  return state ? {...state,title:modeOwnedText(state.title || ''),text:modeOwnedText(state.text || ''),
    ladder_title:state.ladder_title ? modeOwnedText(state.ladder_title) : state.ladder_title,
    choices:(state.choices || []).map(choice=>({...choice,title:modeOwnedText(choice.title),text:modeOwnedText(choice.text)}))} : state;
}
