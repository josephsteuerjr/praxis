import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { collectConnection } from '../../ui-kit/window/server-connection.ts';

test('unfinished remote settings refuse saving rather than quietly starting another local agent', () => {
  for (const draft of [{mode:'remote'}, {mode:'remote',base:'https://example.com'}, {mode:'remote',base:'nonsense',key:'key'}]) {
    const out={marker:'preserved'};
    assert.ok(collectConnection(out,draft));
    assert.deepEqual(out,{marker:'preserved'});
  }
});
test('remote and return to local preserve unrelated settings and the remembered connection', () => {
  const out={marker:'preserved'};
  assert.equal(collectConnection(out,{mode:'remote',base:' https://example.com/helene/ ',key:' key '}),'');
  assert.deepEqual(out,{marker:'preserved',mode:'remote',base:'https://example.com/helene',key:'key'});
  assert.equal(collectConnection(out,{...out,mode:'local'}),'');
  assert.equal(out.mode,'local');assert.equal(out.key,'key');
});
