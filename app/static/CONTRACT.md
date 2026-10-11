# Frontend contract (Anant: index.html + style.css · Pranav: *.js + server.py)

Rule: JS never sets inline styles for looks (only data-driven values like a bar width). It emits the
classes below; style.css owns every color, size and layout. index.html owns structure; JS only fills
the placeholders listed here. New feature → ask for a placeholder `<div id="...">`, don't edit index.html.

## Placeholders in index.html (JS fills these)
| id | Filled with |
|---|---|
| `#queue` | `.queue-card` elements, keyed by `data-id`, reordered with `appendChild` (AutoAnimate animates moves) |
| `#queue-count`, `#stat-slides`, `#gb-counter`, `#stat-urgent` | text |
| `#viewer` | OpenSeadragon |
| `#case-id`, `#case-meta` | text |
| `#case-priority` | priority banner children (below) + class `priority-banner prio-*` |
| `#case-metrics` | `.metric` blocks |
| `#case-foci` | `.focus-row` elements or one `.foci-empty` |
| `#case-footer` | text with `<b>` |
| `#report-panel` | add/remove `.is-open`; `#report-content` gets an `<iframe id="report-frame">` (class `modal-body`) or text (class `modal-body is-text`) |
| `#device-pill` | add `.is-offline` when the network is unplugged (feature idea) |
| `#viewer-empty` | add `.is-hidden` once a slide is shown |

Buttons in index.html call: `toggleHeatmap()`, `toggleOutline()`, `resetView()`, `fetchReport()`, `closeReport()`, `printReport()`.
`#heatmap-toggle-btn` / `#outline-toggle-btn` get `.is-on`; `#outline-toggle-btn` gets `disabled` when a slide has no annotation.

## Classes JS may emit
**Priority (one per element, sets `--prio`):** `prio-stat` (Macrometastasis) · `prio-urgent` (Micrometastasis) · `prio-priority` (Isolated tumor cells) · `prio-routine` (Negative)

**Queue card**
```html
<div class="queue-card prio-* is-{queued|processing|done|error} [is-active]" data-id="tumor_082">
  <img class="queue-thumb">
  <div class="queue-id">tumor_082</div>
  <div class="queue-score">9.2</div>              <!-- or <small>queued</small> -->
  <div class="queue-detail"><span class="chip">Macrometastasis</span><span class="measure">3.91 mm · 6 foci</span></div>
  <div class="queue-bar"><span style="width: 92%"></span></div>
</div>
```
**Priority banner** (inside `#case-priority`): `.priority-level`, `.priority-category`, `.priority-note`, `.priority-score` (+ `<small>`)
**Metric:** `.metric > .metric-label + .metric-value (+ <small>unit</small>)`
**Focus row:** `.focus-row > .focus-num + div(.focus-size, .focus-sub) + .focus-conf + <i data-lucide="chevron-right">`
**Generic:** `.chip`, `.dot`

Icons: `<i data-lucide="name"></i>` then call `lucide.createIcons()` after inserting HTML.

## Server endpoints the viewer uses
`/api/queue`, `/api/slide/{id}` (metrics.json), `/api/slide/{id}/heatmap`, `/api/slide/{id}/thumbnail`,
`/slide/{id}.dzi` + `/slide/{id}_files/{level}/{col}_{row}.jpeg` (full-res tiles, OpenSlide; falls back to thumbnail on 404),
`/api/slide/{id}/annotation` (pathologist outline), `/api/slide/{id}/report.html` (falls back to POST `/api/report`).

## Offline
All libraries are in `app/static/vendor/` (see VERSIONS.txt). Never load anything from a CDN: the demo unplugs the network.
