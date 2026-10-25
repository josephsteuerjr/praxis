# Linux package and portable transitions

The DEB owns the immutable embedded Python seed and `/usr/bin/helene`. Your
writable working program lives below `$XDG_DATA_HOME/helene-python/program`
(normally `~/.local/share/helene-python/program`). These are separate versions
of the program: replacing the seed does not silently overwrite your working copy.

The launcher offers a Python-owned transition when its seed is newer or a repaired
seed has a different file inventory. A newer working version is retained. You can
open the previous copy or cancel. A transition preserves profile data and authored
source, confirms the new native window, and restores the previous application if
startup fails. Later updates use the same Python updater.

Legacy Debian `helene` packages before 2.0 share `/usr/bin/helene` with the Python
edition. The DEB declares a bounded package transition so apt can retire that
obsolete package. Use your package manager; do not force file overwrites or delete
your profile. Package removal does not remove the separate per-user home.

In a portable archive, choose **Run as a Program** for `helene` in your file
manager. The GUI starts independently of a terminal. Run
`Add Helene to Applications.sh` once to create a user application-menu entry with
the correct embedded runtime, working directory, icon and `Terminal=false`.
GNOME can require you to allow launching a local script or shortcut. No sudo,
system Python or manual pip installation is needed.

For diagnostics, use `sh ./helene --diagnostic`. GUI logs remain under
`${XDG_STATE_HOME:-~/.local/state}/helene/logs/`. A startup error also names its
log. A repeated launch keeps one GUI owner for that program directory.

Debian and Fedora update assets are distinct. The updater selects its recorded
distribution and refuses a package for the other Linux distribution before
overlaying the working program.

Stock source updates use the previous shipped hashes. Authored source and Git
HEAD/index are retained in an independent preserved tree. Overlapping or unknown
source changes stop the transition with `code-transition.json`, naming both
owner and incoming candidates. Resolve the differences in the retained source
before retrying; the old application remains available.

Native acceptance must identify its session. X11/container checks do not establish
Wayland, GNOME, a particular virtual GPU, physical devices or authenticated model
providers. No permanent GPU or sandbox bypass is required by this launcher.
