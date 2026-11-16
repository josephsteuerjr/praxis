# Hélène 2.8.1 Beta

**Windows prerelease. Stable Latest remains 2.7.2.**

This hotfix corrects a Windows startup failure in 2.8.0. During an in-app clean upgrade, the standalone runner loaded first-start policy outside its package. Python raised `ImportError: attempted relative import with no known parent package`; the runner restarted without publishing its ownership heartbeat. After 120 seconds the updater retained the previous programme and reported that the new application did not open.

The runner now uses the package-owned first-start module. The owner-stop latch remains enforced. New regressions exercise both supported owner-stop import modes and prove that a selected Windows stop marker is still read. Existing Stop/Resume checks are retained.

All features introduced in 2.8.0 remain included: independent Russian/English/French/Chinese interface and agent context, resident skills and living prose, Praxis command/code tools and owned work control, optional Windows MCP, bundled relay quota failover and the Windows Forge/attachment/window corrections.

Only Windows assets are published. Physical Tim Windows 10 and owner-device acceptance, microphone/CUDA/public ACME/paid-provider acceptance remain separate. The inherited core failures recorded for 2.8.0 remain visible; this release does not claim an entirely green core suite.

## Release checks

Actual packaged Windows code passed nine owner-latch/Stop/Resume cases, including both import modes and Windows Known Folder resolution. Native bootstrap readiness: four kernel tests. A real restricted-token 2.7.2 upgrade retained history, authored files and a locked old file. Packaged MCP, relay sign-in and component transfer checks passed. All five frontends rebuilt; 176 Node tests and eight targeted desk modules passed. Source privacy scanned all 3734 files against actual local/server secret values.

The unchanged core, runtime executables, relay and all 127 compiled frontend outputs match 2.8.0 exactly. Previous qualification is retained for those identical inputs: the last full core run had 8003 tests, 163 inherited failures and zero new failures against 2.7.2; the last full desk gate covered 491 modules and pytest. These full gates were not rerun for this one-import hotfix. Physical owner upgrade acceptance is still separate.
