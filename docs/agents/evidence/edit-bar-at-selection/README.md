# Edit bar at the selection (TOOLS-129): screenshots

Taken by `tests/cut-preview-browser.mjs` in headless Chromium on the made-up 8-second test video.

- `edit-bar-desktop-light.png` (1440 x 1040): one word selected, "Tigh" typed. The bar sits just below the word, with
  the grey suggestion and the Tab key in the box. The two earlier cuts stay in the text, struck through, each with a
  Restore mark.
- `edit-bar-phone-light.png` (390 x 844): a deleted word selected. The bar spans the screen just below the word, and
  its first button reads Restore.

The app has one light theme. With the browser set to dark, the same screens render byte for byte the same, so no dark
copies are kept.
