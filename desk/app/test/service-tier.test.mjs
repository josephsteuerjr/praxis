import assert from 'node:assert/strict';
import {collectServiceTier, serviceTierChoices, serviceTierLabel, serviceTierReceipt} from '../../ui-kit/service-tier.ts';
import {effortPlan, fastModeAvailability} from '../../ui-kit/providers.ts';

const absent = {reasoning_effort:'high',custom:{preserve:true}};
collectServiceTier(absent,'',false);
assert.equal(Object.hasOwn(absent,'service_tier'),false);
assert.equal(absent.reasoning_effort,'high');
assert.deepEqual(absent.custom,{preserve:true});

const legacy = {service_tier:'provider-custom',reasoning_effort:'future-effort'};
collectServiceTier(legacy,'provider-custom',false);
assert.equal(legacy.service_tier,'provider-custom');
assert.equal(legacy.reasoning_effort,'future-effort');
assert.ok(serviceTierChoices(legacy.service_tier).some(([value])=>value==='provider-custom'));

const current = {service_tier:'fast',reasoning_effort:'xhigh'};
assert.ok(serviceTierChoices(current.service_tier).some(([value,title])=>value==='fast'&&title==='Fast mode'));
collectServiceTier(current,'fast',false);assert.equal(current.service_tier,'fast');
collectServiceTier(current,'fast',true);assert.equal(current.service_tier,'priority');
collectServiceTier(current,'default',true);assert.equal(current.service_tier,'default');
collectServiceTier(current,'',true);assert.equal(Object.hasOwn(current,'service_tier'),false);
assert.equal(current.reasoning_effort,'xhigh');

assert.deepEqual(serviceTierChoices('').map(([value])=>value),['','default','priority']);
assert.equal(serviceTierLabel('priority'),'Fast mode');
assert.equal(serviceTierLabel('fast'),'Fast mode');
assert.equal(serviceTierLabel('default'),'обычный режим');
assert.ok(!serviceTierReceipt(null).includes('Fast mode'));
assert.ok(!serviceTierReceipt('default').includes('Fast mode'));
assert.ok(serviceTierReceipt('priority').includes('Fast mode'));
assert.ok(!serviceTierChoices('priority','По настройке провайдера',false).some(([value])=>value==='priority'));
assert.equal(fastModeAvailability({id:'unknown'}),null);
assert.equal(fastModeAvailability({id:'known',service_tiers:[]}),false);
assert.equal(fastModeAvailability({id:'known',service_tiers:[],additional_speed_tiers:['fast']}),false);
assert.equal(fastModeAvailability({id:'known',additional_speed_tiers:['fast']}),true);
assert.equal(fastModeAvailability({id:'known',service_tiers:[{id:'priority'}]}),true);
assert.equal(fastModeAvailability({id:'known',service_tiers:[{id:'ultrafast'}]}),false);
const first=effortPlan('chatgpt','catalog-model','',['low','high','none','ultra','future-unsupported']);
assert.deepEqual(first.chips.map(row=>row.value),['','low','high','ultra']);
assert.equal(first.chips[0].label,'По умолчанию');
const second=effortPlan('chatgpt','other-model','',['minimal','medium']);
assert.deepEqual(second.chips.map(row=>row.value),['','minimal','medium']);
assert.deepEqual(effortPlan('chatgpt','unknown-model').chips.map(row=>row.value),['']);
console.log('PASS service tier opt-in, explicit off/reset, legacy retention, independent effort and actual-tier wording');
