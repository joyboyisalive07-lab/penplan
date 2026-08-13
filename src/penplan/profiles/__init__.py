"""Calibration profiles shipped with the tool.

``test-canvas`` was produced by running the wizard against
``tools/testcanvas.html``, which is in this repository: a plain HTML canvas
with a palette, two tools and four brush sizes, so the whole pipeline can be
exercised end to end without touching anyone else's game. Its colours were read
off the screen and its brush widths measured from test strokes, which came back
as exactly the 2, 6, 14 and 28 pixels the page defines.

Its coordinates are where that page sat in a maximised browser on a 1920x1080
display at 100 per cent scaling. They will not match your window, and the tool
checks before it draws: it reads the palette back off the screen and refuses if
it does not match. Recalibrating takes a minute and is the honest answer.
"""
