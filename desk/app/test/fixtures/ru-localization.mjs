// Legacy behavioral fixtures assert Russian labels. Execute the real resolver
// and catalogs with an explicit locale; browser-language-controls covers all
// four languages independently. This is a dependency adapter, not a translation
// mock, and it does not replace application text or alter the source on disk.
import {readFileSync} from 'node:fs';
import {registerHooks,stripTypeScriptTypes} from 'node:module';
const languageURL=new URL('../../../lang/lang.ts',import.meta.url).href;
registerHooks({
  resolve(specifier,context,next){
    if(/(?:^|\/)lang\/lang(?:\.ts)?$/.test(specifier))return {url:languageURL,shortCircuit:true};
    return next(specifier,context);
  },
  load(url,context,next){
    if(url!==languageURL)return next(url,context);
    let source=readFileSync(new URL(url),'utf8');
    for(const lang of ['ru','en','fr','zh']){
      const catalog=readFileSync(new URL(`./${lang}.json`,url),'utf8');
      source=source.replace(`import ${lang} from "./${lang}.json";`,`const ${lang} = ${catalog};`);
    }
    return {format:'module',source:stripTypeScriptTypes(source),shortCircuit:true};
  },
});
const language=await import(languageURL);
language.setLocale('ru');
// Older tests deliberately execute extracted function bodies without imports.
// Supply their newly explicit language dependency from the production module.
Object.assign(globalThis,{uiText:language.t,locale:language.locale,localePreference:language.localePreference,saveLocale:language.saveLocale});

/** Existing source-structure assertions may still identify a static label.
 * Resolve only literal, parameter-free calls through the actual catalog;
 * dynamic expressions, branches, parameters and effects stay unmodified.
 */
export function resolvedUiLiterals(source){
  return source.replace(/\b(?:uiText|t)\(("(?:ui\.|language\.|shell\.)[^"\n]+")\)/g,
    (_,quoted)=>JSON.stringify(language.t(JSON.parse(quoted))));
}
