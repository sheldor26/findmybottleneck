# Learnings

> The counterpart to MISTAKES.md. When something works unusually well, the
> transferable part gets written down before it is forgotten.
>
> The test for an entry: would it change how you approach the *next* problem?
> If not, it is a changelog line, not a learning. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new learning "Title" --tags area`
<!-- bitacora:entry
id: L-0001
date: 2026-09-21
tags: [gui, tooling]
-->
### CustomTkinter renders fully blank on Tk 8.5, with no error — needs Tk 8.6+

**What worked.** The GUI redesign this session (sidebar nav, dark/orange
palette, Report stat-tile row) rendered as a totally empty dark rectangle
on this Mac's default `python3` (Apple's Command Line Tools build) — no
exception, no stderr, process alive, window just blank. Stashing the
change and running the *pre-redesign* code showed the exact same blank
window, which ruled out the redesign as the cause. `python3 -c "import
tkinter; print(tkinter.TkVersion)"` showed `8.5` — that Python links
against macOS's ancient system Tk, and CustomTkinter's canvas-drawn
rounded widgets need Tk 8.6+ to draw at all; on 8.5 they silently draw
nothing. `brew install python-tk@3.12` (pulls a modern `tcl-tk`, currently
9.0) plus a throwaway venv with `customtkinter` installed gave a Python
that rendered the window correctly on the first try — same code, same
Mac, only the Tk version changed.

**Why it worked.** The diagnostic move was isolating variables one at a
time — code (stash/pop) first, then interpreter (`TkVersion` check) —
instead of assuming a blank canvas-based GUI meant a bug in the widget
tree that had just changed. A blank window with zero errors is a strong
signal to check the *runtime*, not the code that ran fine (no traceback)
inside it.

**Reuse it when.** Any CustomTkinter (or other canvas-heavy, modern-Tk-only
Tkinter) window needs visual verification on macOS and the default
`python3` is Apple's Command Line Tools build — check `tkinter.TkVersion`
before assuming the code is broken. This is a local-dev-verification gotcha
only: the actual target machine for `findmybottleneck gui` is Windows via
the python.org installer, which ships Tk 8.6+ by default, so this has
never been and is not expected to be a problem there.

