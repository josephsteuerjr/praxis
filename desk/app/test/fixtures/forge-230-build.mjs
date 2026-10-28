// Reuse the product's existing Vite dependency tree, never copy/install one.
// node desk/app/test/fixtures/forge-230-build.mjs <node_modules directory>
import {createRequire} from 'node:module';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mkdirSync,readFileSync,writeFileSync,renameSync,unlinkSync} from 'node:fs';
const here=dirname(fileURLToPath(import.meta.url));
const modules=resolve(process.argv[2] || 'desk/app/node_modules');
const require=createRequire(resolve(modules,'../package.json'));
const {build}=await import(pathToFileURL(require.resolve('vite')).href);
const out=resolve(here,'forge-230-built');mkdirSync(out,{recursive:true});
const entry=resolve(here,'forge-230-entry.html');
writeFileSync(entry,readFileSync(resolve(here,'forge-230.html'),'utf8').replace('<link rel="stylesheet" href="forge-230.css">','').replace('src="forge-230.js"','src="forge-230.ts"'));
try {
  await build({configFile:false,root:here,base:'./',resolve:{alias:{'@tauri-apps/api':resolve(modules,'@tauri-apps/api')}},build:{outDir:out,emptyOutDir:true,target:'chrome120',rollupOptions:{input:entry}}});
  renameSync(resolve(out,'forge-230-entry.html'),resolve(out,'index.html'));
} finally {unlinkSync(entry);}
console.log(out);
