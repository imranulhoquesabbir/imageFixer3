imageFixer3 - final update
Copy these files over your project (same folders), then restart Image Fixer / PC Print.

core.py, card.py, canvas.py, printlayout.py   -> project root
pcprint/server.py                              -> pcprint/
pcprint/static/index.html                      -> pcprint/static/

Fixes:
1. Eraser (brush / selection) no longer changes anything outside the brush or the selection.
2. No ghost of a layer at its old place while dragging 2+ selected layers.
3. Print: a nearly square picture is not turned sideways any more; Rotate picture button + R / Shift+R key; Auto-turn to fit checkbox.
