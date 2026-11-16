# Hélène 2.8.4 Beta

**Windows prerelease. Stable Latest remains 2.7.2.**

This release fixes long stalls of shell and Python commands caused by the agent's personal Git repository, found on the owner's machine right after 2.8.3.

**What happened.** Hélène keeps a personal Git repository of the agent's own edits and takes a safety snapshot before every command. A move to a new programme folder had placed the owner's backup folder inside the agent's data, and weekly multi-gigabyte backups entered that repository. On the owner's machine it grew to 21 GB, including a 4.8 GB file Git for Windows could not even read back. Every self-commit started Git's automatic repacking in the foreground; it never finished inside the 30-second limit, left gigabytes of temporary packs, and held the repository while the next command waited. The safety snapshot rebuilt its index from the last commit and re-hashed the backup each time. Some commands waited five minutes. 2.8.3 stopped new backups from entering, but the old history and the snapshot method remained.

**Cleanup at start, for every installation.** Before the agent starts, Hélène now:

- turns off automatic gc and maintenance for the personal repository;
- deletes temporary pack files left by interrupted repacks;
- removes owner backups (`retained-assets/`, `backups/`) and two service files from the latest commit, without touching the real files;
- if the repository is large and its history contains those paths, rewrites the history without them. Every other file and commit is kept. The rewrite reads commits and trees only, so a damaged oversized file being dropped cannot stop it. Each branch's tree is compared with the original, the new store is checked with `git fsck`, and only then does it replace the old one. On the owner's 21 GB history this took 7.6 seconds and left 9.7 MB, with all 395 commits and 41 references intact.

**Snapshots without `git add -A`.** The safety snapshot now stages exactly the files `git status` reports, starting from a copy of the real index, so unchanged files are not re-read. Ignored files and files over 50 MB never enter a snapshot or a self-commit. A self-commit expands an untracked folder into its files so a large file inside is caught, and it no longer adds back a service file that had been taken out of the repository.

All features of 2.8.0, the 2.8.1 startup hotfix, the 2.8.2 Windows fixes and the 2.8.3 fixes remain included. Only Windows assets are published. The inherited core failures recorded for 2.8.0 remain visible; this release does not claim an entirely green core suite.
