# Hélène 2.8.1 Beta

**Windows prerelease. Stable Latest remains 2.7.2.**

This hotfix corrects a Windows startup failure in 2.8.0. During an in-app clean upgrade, the standalone runner loaded first-start policy outside its package. Python raised `ImportError: attempted relative import with no known parent package`; the runner restarted without publishing its ownership heartbeat. After 120 seconds the updater retained the previous programme and reported that the new application did not open.

The runner now uses the package-owned first-start module. The owner-stop latch remains enforced. New regressions exercise both supported owner-stop import modes and prove that a selected Windows stop marker is still read. Existing Stop/Resume checks are retained.

All features introduced in 2.8.0 remain included: independent Russian/English/French/Chinese interface and agent context, resident skills and living prose, Praxis command/code tools and owned work control, optional Windows MCP, bundled relay quota failover and the Windows Forge/attachment/window corrections.

Only Windows assets are published. Physical Tim Windows 10 and owner-device acceptance, microphone/CUDA/public ACME/paid-provider acceptance remain separate. The inherited core failures recorded for 2.8.0 remain visible; this release does not claim an entirely green core suite.
