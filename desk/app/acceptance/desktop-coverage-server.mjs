// Test-only production fixture. Binds loopback, never reads an installed profile.
import {ready,close} from '../test/fixtures/desktop-coverage-data.mjs';
console.log('DESKTOP_COVERAGE_URL='+await ready);
for (const signal of ['SIGINT','SIGTERM']) process.on(signal,()=>void close().then(()=>process.exit(0)));
