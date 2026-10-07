import {readFileSync,writeFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const out=new URL('../../dist/',import.meta.url);
const source=readFileSync(new URL('../../../ui-kit/feed/scroller.ts',import.meta.url),'utf8');
writeFileSync(new URL('scroll-port-scroller.js',out),stripTypeScriptTypes(source).replace(/^export /gm,'')+'\nwindow.Scroller=Scroller;');
writeFileSync(new URL('scroll-port-probe.js',out),readFileSync(new URL('scroll-port-native-page.js',import.meta.url)));
writeFileSync(new URL('scroll-port-probe.html',out),'<meta charset="utf-8"><style>.view{height:688px;width:800px;overflow:auto}.inner{height:3000px;display:flow-root}</style><main id="lab"></main><script src="scroll-port-scroller.js"></script><script src="scroll-port-probe.js"></script>');
