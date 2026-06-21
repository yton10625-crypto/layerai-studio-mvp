# LayerAI Studio — MVP Skeleton

A working full-stack skeleton of the LayerAI Studio PRD: upload a marketing
image, run it through an AI pipeline that splits it into a layer tree
(background / hero product / headline / price / CTA / logo), review and edit
that layer tree, then export it as structured JSON or a PSD-style package.

This was built end-to-end and tested in a sandboxed dev environment with
**no internet access**, which forced a few stack substitutions from the PRD.
They're called out below so you know exactly what to swap when you move this
to a real environment.

## What's real vs. mocked

**Genuinely real, not mocked:**
- OCR text extraction — runs actual Tesseract OCR on the uploaded image, two
  merged passes (dense + sparse layout modes) to catch both paragraph text
  and isolated price/CTA-style text common in ads.
- The full CRUD workflow — upload, layer rename/delete/merge/reorder/edit,
  modification history, and export all hit a real backend and real SQLite
  database. Nothing in the workspace is a frontend-only mock.
- Role classification (headline/subheadline/price/cta/logo) — a two-pass rule
  engine (regex for price, keyword list for CTA, corner-position heuristic
  for logo, relative text size for headline/subheadline), not random.
- **PSD export is a real, openable-in-Photoshop binary `.psd` file**
  (`backend/psd_writer.py`) — written from scratch with `struct` since no
  PSD-writing library was available offline. Verified three independent
  ways: the `file` command's libmagic signature detection identifies it as
  a valid Adobe Photoshop image; Pillow's own PSD *reader* parses all
  layers back out with correct names/bounding boxes; a manual byte-level
  parse confirms the per-layer visibility flag is set correctly. Each
  layer's pixels are a literal crop of the original image at that layer's
  bounding box (see the "Known limitations" section below for what that
  does and doesn't get you).

**Mocked / heuristic stand-ins, documented inline where they live:**
- **Object detection & segmentation** (`backend/mock_ai.py`) — the PRD calls
  for Florence2 + SAM2. This skeleton uses OpenCV edge/contour detection to
  guess a single "hero product" region. It's a crude stand-in, not a learned
  model — expect it to miss on busy or low-contrast images.
- **OCR engine** — Tesseract stands in for the PRD's PaddleOCR. Tesseract is
  noticeably weaker on stylized reversed-out text (white text on a dark
  button is a common failure case you'll hit in testing).
- **Font matching** (`guess_font()` in `mock_ai.py`) — returns a
  deterministic pick from a short font list, not real glyph-shape matching.
  (Not used by the PSD export, which uses real cropped pixels instead of
  rendered text — see above.)

## Stack substitutions from the PRD

| PRD spec | This skeleton | Why |
|---|---|---|
| FastAPI | Flask | Only Flask was available offline; route handlers are thin wrappers around plain functions in `database.py`/`mock_ai.py`/`export.py`, so porting to FastAPI routers is mechanical. |
| PostgreSQL | SQLite (stdlib `sqlite3`, no ORM) | File-based, zero install. Schema in `database.py` maps directly to the PRD's data model — swap in SQLAlchemy + psycopg2 against the same schema when you're ready. |
| Celery + Redis | Synchronous in-process call | The heuristic pipeline runs in well under a second. Re-introduce a job queue once analysis is calling real, slower models. |
| Next.js + React + TS + Tailwind | Vanilla HTML/CSS/JS, no build step | No npm registry access in the sandbox to install Next.js. The frontend is structured as four clear view-states (`goToView()` in `app.js`) so porting each into a Next.js page/route is straightforward. |
| Florence2 / SAM2 / PaddleOCR | OpenCV + Tesseract | See "What's real vs. mocked" above. |

None of this is hidden — every substitution has a comment at its call site
explaining the swap point for when you have full internet/GPU access.

## Running it

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5050` — the Flask app serves the API and the frontend
from the same origin, so there's no CORS configuration to fight with locally.

No separate frontend build step or server is needed; `frontend/` is static
files served directly by Flask.

## Project layout

```
backend/
  app.py              entrypoint — registers routes, serves frontend
  database.py          SQLite schema + connection helpers
  mock_ai.py            AI pipeline (OCR + heuristic detection + layout classifier)
  export.py             LayerAI JSON builder + real .psd export (crops + DB data)
  psd_writer.py           from-scratch binary .psd file writer
  routes/
    projects.py         upload, list, fetch project/image
    analysis.py          run pipeline, fetch layer tree + layout structure
    layers.py             rename / delete / merge / reorder / edit_text / replace_image
    export.py              export JSON / export real .psd / download
  requirements.txt
frontend/
  index.html            4 view-states: upload, analyzing, workspace, export
  styles.css            dark workspace UI, design tokens at the top of the file
  app.js                 all client-side logic, no framework
test_ad.png             synthetic test image used during development
```

## What's been tested

Verified end-to-end via the actual HTTP API: upload → AI analysis → layer
tree returned with correct roles → rename, edit text, delete, merge, and
reorder all persist correctly and log to the modification history → JSON
export produces valid, inspected output. The frontend was clicked through
with Playwright (upload → analyze → select a layer → edit its text → check
history → export both formats) with no console errors.

The .psd export was checked three independent ways since real Photoshop
wasn't available to test against directly: the `file` command's libmagic
signature detection identifies the output as a valid Adobe Photoshop image;
Pillow's own (separately implemented) PSD reader parses all layers back out
with matching names and bounding boxes; a manual byte-level parse confirms
the per-layer visibility flag round-trips correctly when a layer is hidden
before export. If you hit an actual Photoshop-side issue opening it, that's
useful signal — flag it.

## Known limitations / what to fix first

1. **CTA detection on stylized buttons is unreliable.** Tesseract struggles
   with bold white-on-dark button text. A real PaddleOCR integration (PRD's
   original choice) should resolve most of this — see the docstring in
   `mock_ai.py`'s `_ocr_blocks()`.
2. **Hero product detection is a single best-guess contour**, not real
   instance segmentation. It'll miss multi-product layouts or busy
   backgrounds.
3. **PSD layers are crops, not true segmented objects.** Each layer's pixels
   are lifted straight from the original image at that layer's bounding
   box — there's no inpainting. That means: hiding or deleting a layer in
   Photoshop reveals whatever's on the layer(s) underneath, which still has
   the original content baked into its own crop — not a clean background.
   Editing a layer's text in the LayerAI Studio workspace updates the data
   model (and the JSON export) but does NOT re-render new text into the
   PSD's pixels; the exported .psd reflects the original image's pixels
   regardless of in-app text edits. Real clean removal/replacement needs
   generative fill — a Phase 2/3 item per the PRD roadmap, not this MVP.
4. **No alpha channel / soft edges** — every PSD layer is a fully opaque
   rectangle (the crop's bounding box), not a shape-accurate cutout.
5. **Image replacement doesn't recomposite the workspace canvas.** The
   backend stores the new asset and logs the change correctly
   (`/api/layers/replace-image`), but the workspace canvas still shows the
   original image with bbox overlays — actually rendering the swapped asset
   in place needs a real compositing step.
6. **No auth, billing, or team workspace** — out of scope for this skeleton,
   per the "full-stack MVP, mock AI, get the flow working" brief.

## Suggested next steps (mapping to the PRD roadmap)

- **Finish Phase 1**: swap Tesseract → PaddleOCR, OpenCV heuristic → SAM2
  for real object segmentation (this is what would let PSD layers be true
  cutouts with clean backgrounds instead of crops), port Flask → FastAPI +
  Postgres once you have a normal dev environment with internet/GPU access.
- **Phase 2**: Figma export, real font matching (glyph comparison, not the
  current deterministic stub), team workspace, canvas recompositing for
  image replacement, render edited text into the PSD's actual pixels
  (currently in-app text edits only affect the JSON export, not the .psd).
- **Phase 3**: AI-assisted editing (generative background fill so removing
  a layer actually leaves a clean background), auto-resize to other ad
  formats, public API platform.
