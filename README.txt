HERETIC'S FORK - 10.5x SPEED BUTTON  (v0.1)
===========================================

Adds a 4th speed button to the battle HUD, right of fast-forward, that runs the
game at 10.5x (3x the normal 3.5x fast-forward). Pause, 1x and 3.5x are unchanged.
Keyboard speed-up also reaches the new speed.

REQUIREMENTS
  - Windows, Steam version of Heretic's Fork (the exact build the mod was made for;
    other builds are detected and refused without changing anything)
  - Python 3.8 or newer: https://www.python.org/downloads/
    (tick "Add python.exe to PATH" during setup). No extra packages needed.

INSTALL
  1. Close the game.
  2. Double-click install.bat.
     The game folder is found automatically in any Steam library.
     Installing takes a few seconds and adds about 4 MB to data.win.

UNINSTALL
  Double-click uninstall.bat. The original files are restored byte-for-byte.
  Steam "Verify integrity of game files" also removes the mod completely.

KNOWN ISSUES
  * Gravity active ability increases in size with speed up
  * All other mechanics and audio/graphics seem to be in working order

NOTES
  - Game updates replace the patched files and remove the mod. The installer
    will refuse a new build until the mod is updated for it.
  - If you get a permission error, right-click install.bat > Run as administrator.
  - The installer leaves two backup files next to the game files
    (Heretics Fork.exe.speedmod.bak, data.win.speedmod.undo.json); uninstall
    uses them, so don't delete them while the mod is installed.
  - Unofficial fan mod; not affiliated with the developers.

Advanced: python hf_speedmod.py install|uninstall|status ["<game folder>"]

LICENSE
  Copyright (C) 2026 found3r
  This mod is free software under the GNU General Public License v3.0 or later;
  see LICENSE. It comes with ABSOLUTELY NO WARRANTY.
  The license covers the mod's own files only. Heretic's Fork and its assets
  belong to their respective owners; none of them are included in this package.
