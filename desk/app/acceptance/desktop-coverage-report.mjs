// Read retained audit outputs without reclassifying a failure as a pass.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const args=process.argv.slice(2),output=args.find(a=>a.startsWith('--out='))?.slice(6)||'desk/app/test-results/desktop-coverage-report';
const dirs=args.filter(a=>!a.startsWith('--'));
if(!dirs.length)throw Error('Supply one or more existing audit output directories');
const defaults=['now-populated','talk-populated','plans-populated','wakes-populated','frame-populated','files-populated','journal-populated','anatomy-populated','learn-populated',...['agent','brain','rights','app','look'].map(g=>'settings-'+g)];
const runs=[];
for(const dir of dirs){
 const report=JSON.parse(await readFile(join(dir,'ledger.json'),'utf8'));
 const scenes=report.ledger.map(scene=>({id:scene.id,matrix:scene.matrix,status:scene.status,rendered:scene.rendered&&scene.status!=='failed',measured:scene.measured,visuallyInspected:scene.visuallyInspected,screenshots:scene.screenshots,
  consoleErrors:scene.consoleErrors,httpFailures:scene.httpFailures||[],failedRequests:scene.failedRequests,
  fontsLoaded:scene.measurements.every(m=>m.fonts?.status==='loaded'),
  clippingCandidates:scene.measurements.reduce((n,m)=>n+m.clipping.length,0),
  nonEllipsisOverflowCandidates:scene.measurements.reduce((n,m)=>n+m.overflow.filter(r=>!r.ellipsis).length,0),
  smallTextCandidates:scene.measurements.reduce((n,m)=>n+m.smallText.length,0)}));
 runs.push({directory:resolve(dir),sources:report.sources,fixtureSources:report.fixtureSources||null,filters:report.filters||null,matrix:report.matrices,requirements:report.expected,omissions:report.omissions,scenes,
  missingDefaultDestinations:defaults.filter(id=>!scenes.some(s=>s.id===id&&s.rendered)),
  failedRequirements:report.expected.filter(s=>s.status==='failed'),incompleteRequirements:report.expected.filter(s=>s.status==='running'),
  note:'Candidate counts are not confirmed defects; intentional ellipsis, scroll boundary clipping and invisible opacity-zero shortcut hints require eye judgment.'});
}
await mkdir(output,{recursive:true});
await writeFile(join(output,'coverage-summary.json'),JSON.stringify({schema:1,generatedAt:new Date().toISOString(),runs,visualAcceptance:'Separate author/parent/critic inspections are not inferred here.'},null,2));
const lines=['# Retained production desktop coverage','','No image generation is visual acceptance. Earlier failed runs are preserved, not rewritten by a later successful branch.','', '| Run | Scenes | Screenshots | Missing default destinations | Failed requirements | Unfinished |','|---|---:|---:|---|---|---|'];
for(const run of runs)lines.push(`| ${run.directory.split(/[\\/]/).at(-1)} | ${run.scenes.length} | ${run.scenes.reduce((n,s)=>n+s.screenshots.length,0)} | ${run.missingDefaultDestinations.join(', ')||'none'} | ${run.failedRequirements.map(r=>r.id).join(', ')||'none'} | ${run.incompleteRequirements.map(r=>r.id).join(', ')||'none'} |`);
lines.push('','See coverage-summary.json for exact scene/matrix/screenshots, source hashes, console/network facts and declared omissions. A focused run naturally lists default scenes it did not request; this is not whole-app coverage.');
await writeFile(join(output,'coverage-summary.md'),lines.join('\n')+'\n');
console.log(JSON.stringify({output:resolve(output),runs:runs.length,failedRequirements:runs.reduce((n,r)=>n+r.failedRequirements.length,0)},null,2));
