import assert from 'node:assert/strict';
import {useBridge} from '../../ui-kit/window/server-bridge-config.ts';

const bridge={id:'fixture-proxy',name:'Existing tinyproxy',ports:{local_proxy_port:18889,remote_proxy_port:8888}};
const config={phone:{enabled:false,mode:'automatic'},network:{authored:'keep'},relay:{port:5017,instructions:'authored instructions',proxy:{mode:'direct'}},model:{key:'fixture-owner-value'}};
const ordinary=structuredClone(config);useBridge(ordinary,bridge);
assert.deepEqual(ordinary.phone,config.phone,'proxy-only does not require or enable HTTPS');
assert.deepEqual(ordinary.relay,config.relay,'relay does not silently inherit the Telegram proxy');
assert.equal(ordinary.network.proxy.port,'18889');assert.equal(ordinary.network.bridge_profile,bridge.id);
assert.equal(ordinary.network.authored,'keep');assert.deepEqual(ordinary.model,config.model);
const opted=structuredClone(config);useBridge(opted,bridge,{relay:true});
assert.equal(opted.relay.proxy.port,'18889');assert.equal(opted.relay.proxy.host,'127.0.0.1');
assert.equal(opted.relay.port,5017);assert.equal(opted.relay.instructions,'authored instructions');
const https=structuredClone(config);useBridge(https,{id:'fixture-https',name:'HTTPS',external:'https://fixture.example',ports:{remote_app_port:18766}},{relay:true});
assert.equal(https.phone.external,'https://fixture.example');assert.deepEqual(https.network,config.network);assert.deepEqual(https.relay,config.relay);
console.log('Proxy-only route, explicit relay opt-in and unrelated owner settings preserved PASS');
