# Hélène 2.8.7 Beta — all platforms, Latest

This release restores the smoothness of the desktop window.

While the agent worked, scrolling, typing, animations and buttons could wait about half a second. Two window-wide `:has()` style rules made every small change in the page — a status text, a voice-player flag rewritten on each poll — restyle the whole window, including the full-window paper layer. The graphics process then repainted the entire window and the page waited for it before the next frame. The rules are replaced by explicit flags set when the state actually changes, and the voice player and the work-activity block no longer rewrite identical values on every poll.

Measured on a stand that runs the release frontend in the same frameless WebView2 window as the application (WebView2 157, 125 % scaling, 600-message feed), interleaved, three rounds each: in 2.8.6 a typed letter appeared after 410–480 ms and the idle window had several half-second stalls every few seconds; in 2.8.7 a letter appears after 18–21 ms and there are no such stalls. 2.5.3 behaves like 2.8.6 on the same WebView2, so the slowdown is not tied to one recent Hélène version. A guard test now rejects window-wide `:has()` rules.

On Windows the window follows the cursor when it is moved by its header or resized by an edge or corner: the press hands the drag to the system's own move and size loops instead of moving the window from the page on every mouse movement. With a busy page the old drag trailed the cursor by about 60 px on average; the system loop stays within a few pixels. A double click on the header still maximizes or restores, and a maximized window is not dragged. macOS and Linux keep their previous window behavior.

This release includes all 2.8.6 changes. The core, bundled runtime and relay are unchanged; their prior qualification remains explicitly bound to identical inputs. Detailed fresh checks and inherited limitations are recorded in release-evidence. The previously recorded core failures remain visible; this release does not claim an entirely green core suite.

macOS packages are ad hoc signed and are not notarized. The ARM64 server retains relay 1.2.0; desktop packages include subscription quota failover.
