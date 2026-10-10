// Preserve the actual counter across a receipt; resolve an exact Telegram reply.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const load = async (name) => import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/' + name, import.meta.url), 'utf8'))).toString('base64'));
const { foldWords, deliveryWords } = await load('memory-fold.ts');
const state = { room: '-1', hot: 921, keep: 250, offer_at: 400, hard_at: 500, offer_mode: true, backstop_hours: 26, offer: {hard: true}, pending: null, receipt: null };
const idle = foldWords(state);
assert.match(idle.counter, /921/);
assert.match(idle.explanation, /26 ч/);
assert.match(idle.explanation, /Решение принимает агент/);
assert.doesNotMatch(idle.explanation, /сворачивает сама|она |её словами/);
assert.ok(idle.canFold);
const running = foldWords({...state, pending: {id: 'x'}});
assert.equal(running.counter, idle.counter);
assert.equal(running.canFold, false);
assert.equal(running.state, 'running');
const done = foldWords({...state, hot: 731, receipt: {state: 'done', folded: 190, at: new Date().toISOString(), note: 'старое в сводке её словами'}});
assert.match(done.counter, /731/);
assert.match(done.note, /190/);
assert.doesNotMatch(done.note, /её|она/);
assert.match(foldWords({...state, hot: 0, offer: null}).counter, /0 сообщений/);
assert.equal(foldWords({...state, hot: 0, offer: null}).canFold, false);
assert.match(foldWords({...state, hot: null}).counter, /неизвестно/);
for (const [hot, noun] of [[1,'сообщение'],[2,'сообщения'],[5,'сообщений'],[11,'сообщений'],[21,'сообщение'],[22,'сообщения']]) {
  assert.ok(foldWords({...state,hot}).counter.endsWith(`${hot} ${noun}`));
}
assert.match(foldWords({...state, offer_mode: false}).explanation, /начинается автоматически/);
assert.match(deliveryWords({...state, delivery:{state:'waiting',retry_at:150,retries:2,at:100}},100000), /50 с/);
assert.match(deliveryWords({...state, delivery:{state:'waiting',retry_at:99,retries:2,at:90}},100000), /повторяется автоматически/);
assert.match(deliveryWords({...state, delivery:{state:'accepted',retry_at:0,retries:2,at:100}},100000), /принял/);
assert.match(deliveryWords({...state, delivery:{state:'cancelled',retry_at:0,retries:2,at:100}},100000), /остановлена/);

const { replyPreview } = await load('reply-preview.ts');
const loaded = new Map([['23', {sender_name: 'Участник', text: 'Исходная\nреплика'}]]);
assert.deepEqual(replyPreview({reply_to_message_id: 23, reply_to_sender_name: 'Старое имя'}, loaded), {id:'23', author:'Участник', text:'Исходная реплика', loaded:true});
assert.equal(replyPreview({reply_to_message_id: 24, reply_to_text: 'Сохранённая цитата'}, loaded).text, 'Сохранённая цитата');
assert.equal(replyPreview({reply_to_message_id: 24, reply_to_media: 'photo'}, loaded).text, 'Вложение: фото');
assert.match(replyPreview({reply_to_message_id: 24}, loaded).text, /не загружено/);
assert.equal(replyPreview({}, loaded), null);
console.log('Counter during running/done, actual offer policy, zero/unknown and reply originals/snapshots/media — OK');
