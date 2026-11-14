import './fixtures/ru-localization.mjs';
import assert from 'node:assert/strict';
const {setLocale,t}=await import('../../lang/lang.ts');
const providers=await import('../../ui-kit/providers.ts');
const tiers=await import('../../ui-kit/service-tier.ts');
const mount=await import('../src/mountdata.ts');
const {setupTips}=await import('../../setup/ui/src/tips.ts');
const {serverBridgeManual}=await import('../../ui-kit/window/server-bridge-manual.ts');
const raw='C:/AUTHORED_目录_日本語',savedTier='AUTHORED_tier_日本語';
for(const lang of ['ru','en','fr','zh']){
 setLocale(lang);
 const prose=[...providers.ANTHROPIC_PRESETS.map(p=>p.note),providers.BILLING_LABEL.sub,tiers.FAST_MODE_HINT(),...setupTips(),serverBridgeManual()];
 if(lang!=='ru')assert.ok(prose.every(s=>!/[А-Яа-яЁё]/.test(s)),'late owned captions follow '+lang);
 assert.equal(providers.BILLING_LABEL.sub,{ru:'подписка',en:'subscription',fr:'abonnement',zh:'订阅'}[lang]);
 assert.deepEqual(providers.effortPlan('anthropic','glm-5.3','https://api.z.ai/api/anthropic').chips.map(c=>c.value),['','low','high','max']);
 assert.equal(mount.mountAccess('чтение и запись'),'write');assert.equal(mount.mountAccess('запись'),'write');assert.equal(mount.mountAccess('AUTHORED_UNKNOWN'),'read');
 assert.equal(mount.ACCESS_WORDS.read,t('mount.access.read'));assert.equal(mount.ACCESS_WORDS.write,t('mount.access.write'));
 assert.deepEqual(mount.parseMounts([{path:raw,access:'write',why:'AUTHORED reason'}]),[{path:raw,access:'write',why:'AUTHORED reason'}]);
 const choices=tiers.serviceTierChoices(savedTier);assert.equal(choices.at(-1)[0],savedTier);assert.ok(choices.at(-1)[1].endsWith(savedTier));
 assert.ok(tiers.serviceTierReceipt(savedTier).endsWith(savedTier));
 const manual=serverBridgeManual();
 for(const command of ['ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub','reverse_proxy 127.0.0.1:18766','ForceCommand /bin/false','caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile','https://man.openbsd.org/sshd_config'])assert.ok(manual.includes(command));
 assert.equal(setupTips().length,4);assert.equal(mount.pathProblem(raw,'windows'),'');
}
setLocale('ru');console.log('Provider/effort/tier protocol, legacy mount aliases and raw fields remain stable under four independent UI languages; manual/tips resolve late');
