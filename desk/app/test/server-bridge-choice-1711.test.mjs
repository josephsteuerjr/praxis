import assert from 'node:assert/strict';
import {useBridge} from '../../ui-kit/window/server-bridge-config.ts';

// 2.8.12: a bridge is a proxy source; each route is bound only when chosen.
const bridge={id:'fixture-proxy',name:'Existing tinyproxy',ports:{local_proxy_port:18889,remote_proxy_port:8888}};
const config={phone:{enabled:false,mode:'automatic'},network:{authored:'keep'},relay:{port:5017,instructions:'authored instructions',proxy:{mode:'direct'}},model:{key:'fixture-owner-value'}};

const telegram=structuredClone(config);useBridge(telegram,bridge,{network:true});
assert.deepEqual(telegram.phone,config.phone,'proxy-only does not require or enable HTTPS');
assert.deepEqual(telegram.relay,config.relay,'relay keeps its own route unless chosen');
assert.equal(telegram.network.proxy.port,'18889');assert.equal(telegram.network.bridge_profile,bridge.id);
assert.equal(telegram.network.authored,'keep');assert.deepEqual(telegram.model,config.model);

const relayOnly=structuredClone(config);useBridge(relayOnly,bridge,{relay:true});
assert.deepEqual(relayOnly.network,config.network,'the relay alone does not move Telegram');
assert.equal(relayOnly.relay.proxy.port,'18889');assert.equal(relayOnly.relay.proxy.host,'127.0.0.1');
assert.equal(relayOnly.relay.bridge_profile,bridge.id,'the relay route keeps the bridge alive by itself');
assert.equal(relayOnly.relay.port,5017);assert.equal(relayOnly.relay.instructions,'authored instructions');

const nothing=structuredClone(config);useBridge(nothing,bridge,{});
assert.deepEqual(nothing,config,'no implied routes');

const https={id:'fixture-https',name:'HTTPS',external:'https://fixture.example',ports:{local_proxy_port:18890,remote_proxy_port:8888,remote_app_port:18766}};
const phone=structuredClone(config);useBridge(phone,https,{phone:true});
assert.equal(phone.phone.external,'https://fixture.example');assert.equal(phone.phone.bridge_profile,'fixture-https');
assert.deepEqual(phone.network,config.network,'choosing Mini App HTTPS does not reroute Telegram');assert.deepEqual(phone.relay,config.relay);
console.log('Explicit Telegram, relay and Mini App HTTPS bindings, nothing implied PASS');
