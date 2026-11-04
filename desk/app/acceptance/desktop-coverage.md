# Production-entry desktop coverage (test equipment)

This runner serves **built app/dist/index.html**, never installs or launches Hélène/Jarvis. All API, native bridge and attachment data are synthetic. The fixture binds a random loopback port; no installed profile is read. The runner blocks off-origin requests and deliberately exercises the HTTP API transport. The existing generic UI fixture can also run its WebSocket transport; WebSocket-specific error-code parity is not asserted here.

## Reproduce

Parent owns the build; first install existing locked app and remote dependencies and run app's normal `npm run build`. No product dependency was added for this audit. Use an isolated developer Playwright runtime, pinned for this run to **playwright@1.64.1**, and install its matching Chromium. One installation recipe, outside the product:

```text
npm install --no-save --package-lock=false --prefix <audit-runtime> playwright@1.64.1
node <audit-runtime>/node_modules/playwright/cli.js install chromium
```

Set `PLAYWRIGHT_BROWSERS_PATH` to the actual browser cache you installed (this installation used module-local `playwright-core/.local-browsers`). Start services with that variable too: a foreground command and a service may have different environment baselines.

```text
node desk/app/acceptance/desktop-coverage.mjs --playwright=<audit-runtime>/node_modules/playwright/index.mjs --out=<new-output-dir>
node desk/app/acceptance/desktop-coverage.mjs --playwright=<...> --theme=dark --out=<another-new-dir>
node desk/app/acceptance/desktop-coverage.mjs --playwright=<...> --baseline --width=900 --height=600 --scale=1 --font=sans --out=<new-dir>
node desk/app/acceptance/desktop-coverage.mjs --playwright=<...> --only=settings --scenes=settings-expanded,settings-remote-edition --out=<new-dir>
node --test desk/app/test/desktop-coverage-fixture.test.mjs
```

`--only` limits page families; `--scenes` limits exact requirement IDs. `--baseline` captures all requested default destinations and their full vertical slices, without expanded/error variants. `--quick` skips empty/error/provider variants. Other options: theme, scale, width, height, font, text, density, shape. `--matrix=all` is an optional broader sweep, not a required cross-product; representative supported boundaries usually suffice. Always use a **new output directory**, retaining earlier failed evidence.

## Human/critic entry

```text
node desk/app/acceptance/desktop-coverage-server.mjs
```

It prints `DESKTOP_COVERAGE_URL=http://127.0.0.1:<random-port>`. Append `?page=learn&previewTheme=light&scale=.9` (or page now/talk/plans/wakes/frame/files/journal/anatomy/settings; dark theme; font hand/sans/serif/mono). The real app entry mounts before the query selects the requested route. `scene=empty` or `scene=remote` selects synthetic state. The service belongs to its starter and must be stopped after inspection; no keep-alive is needed. Browser inspection and screenshots do **not** invoke native OS actions: every bridge command is mocked.

## What the ledger means

- Asset SHA256s bind every run to exact built JS/CSS; filters and matrix are explicit.
- A required missing selector/state fails its scene and exits nonzero, preserving a failure screenshot. There is no `clickIf` silent skip.
- `rendered`, `measured`, and `visuallyInspected` are separate. Automated images remain **not visually inspected**. Keep a separate signed/readable visual inspection record or the parent's critic result; do not promote image generation to taste acceptance.
- Each PNG is one deviceScaleFactor1 viewport at actual scale, never a shrunken montage. Long content has overlapping slices; lower onboarding section/category captures have explicit anchors.
- Measurements retain actual font readiness, text-range rectangles, clipping ancestors, scroll overflow, small effective text, targets and shared-control geometry. These are candidates for eye judgment; intended ellipsis, offscreen scroll content and invisible opacity-zero keyboard hints are not automatically product defects.
- Expected fault injection appears alongside console/HTTP failures. A fixture defect is not a product error; retain and explain its earlier run, then rerun the affected branch with corrected data.

## Boundaries

The denominator is ten routes and five local Settings groups (14 default destinations), plus legitimate remote Settings, lower onboarding sections, selected expanded/read-error/empty/loading/conflict/provider/overlay states. The prior source census put Forge under Plans; the current source places it under Now and the runner follows that mount.

Auxiliary first-start/migration/update HTML, native WinForms tray, Linux Qt wizard, installed WebView/OS permissions, real media devices, pixel contrast over textures, all possible dynamic strings and arbitrary palette combinations are **not certified** by this app runner. Their status must remain separately named. Named overlay scenes exercise image zoom, text preview, HTML save chooser and overwrite confirmation, saved-file receipt, browser save with inert byte-write callback, keyboard text context menu and upstream restore confirmation. Physical right-click menu timing was not qualified (the keyboard consumer path passed); OS-owned attachment picker remains outside HTML proof.

## Release acceptance versus offline update checks

The browser consumers and long-running audit server live in `desk/app/acceptance`.
They retain the complete real-browser assertions and are run by the publisher
against the final source and built UI. They need the explicitly provisioned
Playwright/Chromium runtime above. Run all browser regressions with:

```text
node --test desk/app/acceptance/*.test.mjs
```

The automatic source-preserve updater discovers `desk/app/test/*.mjs`. That
contains self-contained Node tests, including the synthetic transport fixture
tests; it must not start an indefinitely running audit server or assume an
external browser SDK exists on the owner's machine. Both directories remain
in the exact verification source inventory. This separation supplies no test
waiver, browser PASS, physical-device qualification or independent review.
