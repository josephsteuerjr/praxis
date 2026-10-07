// Narrow UI fixture: CSP, real fixture bytes, local-native file bridge and save refusal.
import { extendFixture, ready } from './ui-session-server.mjs';
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const root = await mkdtemp(join(tmpdir(),'helene-paper-ui-'));
await mkdir(join(root,'Документы'));
const png = await readFile(new URL('../../../shell/icons/icon.png',import.meta.url));
const doc = Buffer.from('Рабочий документ\nФайлы выбираются на компьютере владельца.\n','utf8');
const saved = new Map();
const rows = [{name:'Документы',path:join(root,'Документы'),folder:true,size:0},
  {name:'пример.txt',path:join(root,'пример.txt'),folder:false,size:doc.length},
  {name:'картинка.png',path:join(root,'картинка.png'),folder:false,size:png.length},
  {name:'слишком большой.bin',path:join(root,'слишком большой.bin'),folder:false,size:65*1024*1024}];
extendFixture((cmd,a) => {
  if(cmd==='local_files_list') {
    if(String(a.path).includes('отказ')) throw Error('Нет доступа к папке');
    const path=a.path||root;
    return {path,parent:path===root?'':root,locations:[{name:'Домашняя папка',path:root},{name:'Документы',path:join(root,'Документы')}],entries:path===root?rows:[],total:path===root?rows.length:0};
  }
  if(cmd==='local_files_read') {const image=a.path.endsWith('.png'), bytes=image?png:doc;return {name:image?'картинка.png':'пример.txt',mime:image?'image/png':'text/plain',size:bytes.length,data:bytes.toString('base64')};}
  if(cmd==='local_files_save') return (async()=>{
    const path=join(a.folder,a.name);if(saved.has(path)&&!a.overwrite)throw Error('file_exists: файл с этим именем уже существует');
    const bytes=Buffer.from(a.data,'base64');await writeFile(path,bytes);saved.set(path,bytes);return {path,bytes:bytes.length};
  })();
},url=>{
  const q=new URL(url,'http://fixture'); if(q.pathname!=='/api/artifact')return;
  if(q.searchParams.get('path').includes('missing'))return {status:404,type:'text/plain',bytes:Buffer.from('not found')};
  return q.searchParams.get('path').endsWith('.png')?{type:'image/png',bytes:png}:{type:'text/plain; charset=utf-8',bytes:doc};
});
const url=await ready;
await fetch(url+'/__fixture/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({busy:false,appendMessages:[
  {timestamp:new Date().toISOString(),outgoing:true,text:'[файл] C:/private/path.png\nКартинка на бумаге',media_path:'media/artifacts/123456abcdef/image.png',media_kind:'image',media_name:'image.png',media_size:png.length},
  {timestamp:new Date().toISOString(),outgoing:true,text:'Рабочий документ',media_path:'media/artifacts/doc/example.txt',media_kind:'file',media_name:'пример.txt',media_size:doc.length},
  {timestamp:new Date().toISOString(),outgoing:true,text:'Проверка отказа',media_path:'media/artifacts/missing/file.png',media_kind:'image',media_name:'нет-файла.png'}]})});
console.log('PAPER_UI_URL='+url);console.log('PAPER_UI_SAVE_ROOT='+root);
