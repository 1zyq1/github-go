# GitHub Accelerator Design System

## 1. Direction & Atmosphere

This is a precise relay console, not a marketing page. The interface should feel engineered, quiet, and trustworthy: a user arrives with one GitHub URL and leaves with one verified download link. The brand mark is GitHub's own octocat — drawn the way GitHub draws it, a disc with the cat knocked out of it — so the brand names the platform being accelerated instead of inventing a logo of its own. Everything else stays neutral and quiet.

The page has exactly one primary conversion flow: paste URL, submit, receive a download action. There is no centered hero, no decorative dashboard, no gradient background, and no fabricated usage metric. The visual language is closer to a well-made network appliance than a SaaS landing page: hairline structure, deliberate spacing, real states, and no ornament that competes with the task.

This site and the blog (1zyq1.com) are one product, so they share one visual language, taken from the blog's inner pages: a monochrome ladder (near-black on white, near-white on a near-black canvas), square corners everywhere, hairline separation instead of shadow, and a sticky translucent bar carrying the brand. Colour is reserved for state — success, caution, failure — and is never decorative.

The product is Chinese-first, with concise English used only where it is a stable technical term. URLs, file kinds, and file names use the mono stack so structure is visible at a glance. Both themes ship: `prefers-color-scheme` decides the first paint, the reader can override it with the header toggle, and the choice is stored. There is no third theme and no auto-inversion filter — `dark` is a designed palette, not a filter over the light one.

## 2. Color Tokens

One neutral ladder carries the whole interface; the accent *is* the top of that ladder (near-black on light, near-white on dark), exactly as the blog's `bg-primary` button behaves. Success, warning, and danger colors are semantic signals only and are never used decoratively. Purple gradients, neon glows, and coloured panels are prohibited.

| Role | Token | Light | Dark | Usage |
|------|-------|-------|------|-------|
| Canvas | `--surface-canvas` | `#FFFFFF` | `#030303` | Page background |
| Raised surface | `--surface-raised` | `#FAFAFA` | `rgb(255 255 255 / 5%)` | Cards, panels |
| Sunken surface | `--surface-sunken` | `#F5F5F5` | `rgb(255 255 255 / 8%)` | Code chips, disabled fills |
| Skeleton bar | `--surface-skeleton` | `#E5E5E5` | `rgb(255 255 255 / 14%)` | Loading placeholders only |
| Canvas, translucent | `--surface-canvas-blur` | `rgb(255 255 255 / 60%)` | `rgb(3 3 3 / 60%)` | The sticky header bar only |
| Input surface | `--surface-input` | `#FFFFFF` | `rgb(0 0 0 / 40%)` | Text fields |
| Hover surface | `--surface-hover` | `#F5F5F5` | `rgb(255 255 255 / 10%)` | Link and icon-button hover |
| Text primary | `--text-primary` | `#0A0A0A` | `#FAFAFA` | Headings, body, file name |
| Text secondary | `--text-secondary` | `#404040` | `#D4D4D4` | Supporting copy, quiet buttons |
| Text muted | `--text-muted` | `#737373` | `#A3A3A3` | Hints, metadata |
| Text on accent | `--text-on-accent` | `#FFFFFF` | `#0A0A0A` | Primary button label |
| Border default | `--border-default` | `#E5E5E5` | `rgb(255 255 255 / 10%)` | Inputs, card edges, dividers |
| Border strong | `--border-strong` | `#A3A3A3` | `rgb(255 255 255 / 25%)` | Hover edges |
| Accent | `--accent` | `#171717` | `#EBEBEB` | Primary action, mark |
| Accent hover | `--accent-hover` | `#404040` | `#BFBFBF` | Primary action hover |
| Accent soft | `--accent-soft` | `#F5F5F5` | `rgb(255 255 255 / 10%)` | Selected and current tints |
| Focus ring | `--ring` | `#171717` | `#EBEBEB` | Focus outline |
| Success | `--status-success` | `#15803D` | `#4ADE80` | Successful resolution |
| Success soft | `--status-success-soft` | `rgb(34 197 94 / 5%)` | same | Success background |
| Warning | `--status-warning` | `#B45309` | `#FBBF24` | Caution text and borders |
| Warning soft | `--status-warning-soft` | `rgb(245 158 11 / 5%)` | same | Caution background |
| Danger | `--status-danger` | `#DC2626` | `#F87171` | Errors and invalid input |
| Danger soft | `--status-danger-soft` | `rgb(239 68 68 / 5%)` | same | Error background |

Rules:

- Every text/background pair must meet WCAG AA 4.5:1 in **both** themes; the dark column is checked separately, not assumed.
- Interactive text and icons must meet 3:1 against adjacent colors in addition to the text contrast target.
- Color never carries meaning alone. Errors, success, and focus also use text, shape, or an icon.
- No raw color value may appear outside this table. Extend this table before introducing a new semantic role.
- Focus uses `--ring` with a 2px offset; it is never removed as decoration. Because the ring flips with the ladder, it stays visible on the accent button, which is near-black on light and near-white on dark.
- State colours keep one hue in both themes and change only their lightness (green 700/400, amber 700/400, red 600/400), which is how the blog's alerts behave.

## 3. Typography

Three self-hosted variable families are used, the same three the blog renders with. Geist handles Latin text, Geist Mono handles addresses and identifiers, and Noto Sans SC carries Chinese — Geist has no CJK glyphs, so Chinese falls through to it. All are bundled as same-origin `woff2` files under `frontend/public/fonts/` and declared in `frontend/src/styles/fonts.css`, which is generated by `scripts/fetch_fonts.py`; no third-party font CDN is allowed.

```css
--font-sans: "Geist", "Noto Sans SC", system-ui, -apple-system, "Segoe UI", sans-serif;
--font-mono: "Geist Mono", ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
```

| Level | Token | Size | Weight | Line height | Usage |
|-------|-------|------|--------|-------------|-------|
| Subheading | `--text-subheading` | 20px | 500 | 1.4 | Card titles |
| Body | `--text-body` | 16px | 500/700 | 1.625 | Brand, default copy |
| Small | `--text-small` | 14px | 400/500 | 1.55 | Hints, helper text, actions |
| Caption | `--text-caption` | 12px | 400/500 | 1.5 | Non-critical metadata only |

Rules:

- Body and hint text never go below 14px. 12px is reserved for short metadata such as a file-kind label or the allowance line.
- Paragraph measure is capped at `62ch`.
- URLs, file names, and tokens use `--font-mono`, 14px minimum, and `overflow-wrap: anywhere`.
- There is no serif face, no gradient text, and no all-caps Chinese text.
- Weight is the blog's: 700 for the brand, 500 for titles, buttons, and hints, 400 for prose. Nothing heavier than 700 ships, and no weight is faked (`font-synthesis-weight: none`).
- A page has one `h1` — the header brand. Component heading levels follow document order without skipping levels.

## 4. Spacing, Layout & Components

All spacing derives from a 4px base. There are no one-off spacing values in components.

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | 4px | Icon to label |
| `--space-2` | 8px | Inline gap, compact padding |
| `--space-3` | 12px | Form control padding, list gap |
| `--space-4` | 16px | Mobile gutter, standard inset |
| `--space-5` | 20px | Comfortable control padding |
| `--space-6` | 24px | Tablet gutter, panel padding |
| `--space-8` | 32px | Desktop gutter, section gap |
| `--space-10` | 40px | Major group separation |
| `--space-12` | 48px | Page section separation |
| `--space-16` | 64px | Desktop top rhythm |
| `--space-20` | 80px | Maximum vertical separation |

Layout contract:

- Page content uses a `896px` maximum width (the blog's `max-w-4xl`), so both sites break lines at the same width.
- Gutters are 16px at 375px, 24px at 768px, and 32px at 1280px. The sticky header uses the same gutters but spans the full window, as the blog's bar does.
- Full-height behavior uses `min-height: 100dvh`, never `100vh` or `h-screen`. The work area and the panels under it stay at the top of the free space; only the footer is pushed to the bottom edge.
- At 1024px and above, the work area is asymmetric: 7 columns for the form and 5 for the result. Below 1024px it becomes one column.
- The feature strip is one bordered band, not three floating cards. Its desktop columns are `1.2fr 1.6fr 1.2fr` with hairline vertical dividers.
- Radii are 0. Every `--radius-*` token resolves to `0`, so a rounded corner cannot reappear by accident; the square corner is the house style, not a default.

Depth strategy: **tonal shift**. Depth comes from `--surface-canvas`, `--surface-raised`, and `--surface-sunken`. No shadow token exists. Hairline borders are allowed for controls, dividers, cards, and focus boundaries. Exactly one element may use `backdrop-filter`: the sticky header bar, which floats on `--surface-canvas-blur` the way the blog's `bg-background/60 backdrop-blur-sm` bar does. Nothing else blurs, floats, or overlaps.

### AppHeader And GithubMark

- **Structure**: one sticky `header` bar with a hairline bottom rule: the brand on the left (the local SVG `GithubMark` — GitHub's octocat, filled with `--text-primary` and knocked out so the surface shows through the cat, so it inverts with the theme exactly as GitHub's own mark does — beside the product name, the whole group being the link home), then — right-aligned — the site navigation (加速 / 数据 / 博客), the theme toggle, and, when a session exists, the SessionChip.
- **Variants**: anonymous (brand, navigation, theme toggle) and signed-in (the same plus the SessionChip).
- **Spacing**: `--space-2` between mark and product name; `--space-4` between the brand and the aside; `--space-3` between navigation, toggle, and chip; `--space-1` between navigation links.
- **States**: the mark is static with no hover animation; a navigation link has hover, focus-visible, and current; the bar itself has no state.
- **Accessibility**: the SVG is decorative and marked as such; the product name is the page's only `h1`; the navigation is a labelled `nav`, and the current entry is marked with a neutral tint, a heavier weight, *and* `aria-current="page"`, so the state never rests on colour alone. The bar wraps onto further lines at narrow widths instead of scrolling sideways.
- **Motion**: one 320ms fade-and-rise entry on first paint only, on the work area.

### ThemeToggle

- **Structure**: one `button` holding a single 20px local SVG — a moon while the light theme is active, a sun while the dark theme is active.
- **Variants**: light and dark. There is no third state.
- **Spacing**: a `--icon-button-size` (32px) square, flush with the navigation.
- **States**: default, hover (`--surface-hover`), focus-visible.
- **Accessibility**: `aria-pressed` reports the current theme, and the label and title name the action (*切换到深色主题* / *切换到浅色主题*), never just the icon. The control is a real `button`, reachable by keyboard, and its 32px box sits inside the 44px row the header already provides.
- **Motion**: none. Theme changes apply instantly and deliberately have no cross-fade: the swap is a preference, not an animation.
- **Implementation**: the `dark` class on `<html>` is the single source of truth. `src/theme-init.ts` is imported first by the app entry, so the class is set from the stored-or-system preference before the app mounts and a dark-mode visit never flashes white; the toggle only flips the class and writes the choice back to storage. It must be a module loaded from the bundle, never an inline block, because the deployment's CSP allows `script-src 'self'` with no inline source. A blocked `localStorage` must not break the page.

### UrlConverter

- **Structure**: semantic `form`, visible `label`, single-line URL `input`, helper text, inline error, and one primary submit button.
- **Variants**: default, invalid, submitting.
- **Spacing**: `--space-3` inside the field group, `--space-4` between field and actions, `--space-6` panel padding.
- **States**: default, hover, focus-visible, invalid, disabled, submitting.
- **Accessibility**: `aria-describedby` links hint and error text; `aria-busy` marks submission; double submit is impossible while pending.
- **Motion**: label and helper do not move. The result region animates; the form does not.

### DownloadCard

- **Structure**: `section` with file-kind badge, file name, optional real metadata, one copy action, one same-origin new-tab download action, and a reset action.
- **Variants**: empty, loading, success, error.
- **Spacing**: `--space-4` between actions, `--space-6` panel padding.
- **States**: success is factual and calm; error is inline and recoverable; loading never invents file size or progress.
- **Accessibility**: result changes are announced through one polite live region; the new-tab link uses `rel="noopener noreferrer"`.
- **Motion**: panel enters with opacity and an 8px translate; copy feedback uses a 120ms label/opacity change.

### FeatureStrip

- **Structure**: one `section` with three items describing security, streaming, and privacy.
- **Variants**: horizontal at 768px and above, stacked with horizontal dividers below 768px.
- **Spacing**: `--space-3` between icon and text, `--space-6` between columns.
- **States**: static. These are capabilities, not metrics.
- **Accessibility**: plain text, no carousels, no auto-rotation, no color-only meaning.
- **Motion**: staggered 60ms opacity-only reveal, disabled under reduced motion.

### AuthPanel

- **Structure**: `section` with one `h2`, a one-line lead that names the hourly allowance and the confirmation requirement, and either the entry form or the pending view. The entry form is a two-option mode switch plus a `form` with a name field, a password field, an email field that appears only while registering, and one primary submit. The pending view names the address a link was sent to, repeats the email field, and offers a resend plus a way back to the form. While it is showing, the panel re-reads the session whenever the tab becomes visible or the window regains focus: the mailed link is opened in whichever tab the mail client picks, but the cookie it sets belongs to the whole browser, so returning to this tab is exactly when the confirmation can be noticed.
- **Variants**: login, register, awaiting confirmation. Submitting is a state, not a variant.
- **Spacing**: `--space-5` between the panel's blocks, `--space-4` inside the form, `--space-1` between mode segments, `--space-3` between the pending actions, `--space-6` panel padding.
- **States**: default, hover, focus-visible, invalid, disabled, submitting. The selected mode segment carries the neutral `--accent-soft` tint with primary text on a hairline-bordered track; it is deliberately *not* filled with the accent, because a solid block would compete with the submit button below it.
- **Accessibility**: the mode switch is a `radiogroup` whose inputs stay focusable but visually hidden, with `:focus-within` drawing the ring on the visible segment; each field links its hint and error through `aria-describedby`; a validation failure moves focus to the field at fault; server errors land on the field that owns them, and only unowned failures use the form-level region. The pending confirmation line is the only success-coloured text in the panel and is announced through `role="status"`, because it reports progress rather than a problem.
- **Motion**: no entrance of its own; it inherits the work-area entry. The submit label changes in place.

### SessionChip

- **Structure**: a horizontal cluster inside the header holding the signed-in account's name, its remaining allowance, an optional reset hint, and one quiet sign-out button.
- **Variants**: allowance remaining, and exhausted. When exhausted the allowance line turns `--status-warning` and the reset hint appears; the hint is shown only then, because the API reports a reset time only once a unit is actually blocking.
- **Spacing**: `--space-3` between the entries, wrapping onto its own line in the header at narrow widths rather than overflowing.
- **States**: default, hover, focus-visible, disabled, submitting.
- **Accessibility**: the name is text in the mono face, not a badge; the exhausted allowance is never signalled by colour alone, because the number itself reads `剩余 0`.
- **Motion**: none.

### Notice

- **Structure**: a single line of text on a tinted surface with a matching hairline border, used only for page-level conditions that are not tied to one field. The layout is identical to the blog's alert component.
- **Variants**: the default tone reports success (green), and `notice--warning` reports a caution or a failure (amber). Both are 5% washes of their hue so they stay quiet on a monochrome page.
- **Spacing**: `--space-2` between icon and text where an icon is used, `--space-3`/`--space-4` as inner padding.
- **States**: static.
- **Accessibility**: `role="alert"` because a condition that blocks work must interrupt; the message names the condition and the way out, and never repeats a URL or a raw error.
- **Motion**: none.

### StatsPanel

- **Structure**: `section` with one `h2` (使用统计), a one-line lead that states the counting rule, and one DataTable of the published windows.
- **Variants**: loading, ready, error. Loading and error replace the table; they never sit beside stale numbers.
- **Spacing**: `--space-5` between the panel's blocks, `--space-6` panel padding.
- **States**: static once rendered. The panel is a raised surface because it is a work area, and nothing is nested inside it.
- **Accessibility**: the table is labelled by the section heading through `aria-labelledby`; the loading line is a `role="status"` region so it is announced politely; an error reuses the Notice contract.
- **Motion**: no entrance of its own; it inherits the work-area entry.

### DataTable

- **Structure**: a real `table` with a visually hidden `caption`, a `thead` of column names, and one `tr` per row whose first cell is a `th` with `scope="row"`.
- **Variants**: one. Filtering and pagination are not shipped, so the table never grows controls it cannot honour.
- **Spacing**: `--space-2` cell padding on mobile rising to `--space-3` at 768px; hairlines separate rows and are dropped from the last row so the panel padding closes the surface.
- **States**: static. A highlighted row (here the unbounded 全部 range) uses weight, not colour alone.
- **Accessibility**: cells stay cells. The table is never reflowed into blocks with `display: block`, because that strips the row and column association screen readers depend on; it fits 320px instead by using 14px text and four narrow columns. Numeric columns are right-aligned with `font-variant-numeric: tabular-nums` so digits line up across rows.
- **Motion**: none.

### Social Preview Card

- **Structure**: a 1200x630 `og.png` served from the build root. The octocat mark sits beside the public host as the wordmark, the product name is the largest text, one line states the purpose, two capability chips follow, and a ruled footer closes the card.
- **Variants**: one. It is a static asset under `frontend/public/og.png`, not a component, so nothing here responds to state.
- **Spacing**: the artwork is drawn at the viewport size with no scrolling and the content box is exactly 1200x630, because a card that overflows is cropped by the platform instead of being scaled. These dimensions must keep matching `og:image:width` / `og:image:height` in `index.html`.
- **States**: none.
- **Accessibility**: every claim on the card repeats text already present on the page, and `og:image:alt` carries the same wording.
- **Motion**: none.
- **Rendering**: the card is the site's own type set in the site's own faces — Geist, Geist Mono, and Noto Sans SC, the last two bundled under `frontend/public/fonts/`. It is drawn on the dark canvas (`#0A0A0A`) with the mark inverted to near-white, so it reads as the dark theme rather than as a second brand. It must be redrawn whenever the palette, the mark, or the product name changes; a stale card is a defect.

## 5. States & Depth

Every interactive element defines all applicable states. Missing states are a defect, not a styling detail.

| Element | Default | Hover | Active | Focus-visible | Disabled | Loading / Invalid |
|---------|---------|-------|--------|---------------|----------|-------------------|
| Input | Input surface, `--border-default` | `--border-strong` | No transform | 2px `--ring` outline plus 2px offset | Sunken surface, muted text, `not-allowed` | Danger border, icon, message, `aria-invalid` |
| Primary button | Accent background, `--text-on-accent` label | `--accent-hover` | Translate down 1px | Same outline contract | Sunken surface, muted text, no pointer | Static label plus `aria-busy` |
| Quiet button | Transparent, `--border-default`, primary label | Hover surface | Translate down 1px | Same outline contract | Muted label, no pointer | Not applicable |
| Icon button | Transparent, muted glyph | Hover surface plus primary glyph | No transform | Same outline contract | Muted glyph, no pointer | Not applicable |
| Result panel | Raised surface, hairline border | Not interactive | Not interactive | Focus moves to new action | Not applicable | Skeleton rows or inline error |
| Account panel | Raised surface, hairline border | Not interactive | Not interactive | Segment ring plus field outlines | Muted label, no pointer | Field error below the field; unowned failure in the form region |
| Mode segment | Hairline track, muted label | Track unchanged, primary label | No transform | 2px `--ring` outline on the visible segment | Muted label, no pointer | Selected segment takes the `--accent-soft` tint with primary text |
| Session chip | Inside the header bar, no surface of its own | Quiet button only | Translate down 1px | Same outline contract | Hidden when signed out | Exhausted allowance in `--status-warning` |
| Theme toggle | Transparent, muted glyph | Hover surface plus primary glyph | No transform | Same outline contract | Not applicable | Not applicable |
| Notice | 5% success wash, matching border | Not interactive | Not interactive | No focus stop | Not applicable | Not applicable |
| Feature strip | Static | Not interactive | Not interactive | No focus stop | Not applicable | Not applicable |
| Data table | Raised surface, hairline row rules | Not interactive | Not interactive | No focus stop; the table is not a control | Not applicable | Loading line or inline error replaces the rows |
| Auth status line | Canvas, success-coloured text | Not interactive | Not interactive | No focus stop | Not applicable | Names the mailed address; never carries a failure |
| Nav link | Canvas, no surface | Hover surface plus primary text | Translate down 1px | Same outline contract | Not applicable | Current entry: `--accent-soft` tint, heavier weight, and `aria-current="page"` |

Error presentation rules:

- Place the error directly below the field it invalidates; do not replace the form with a toast.
- Prefix errors with a local SVG icon and plain language. Never show a raw exception, HTTP trace, local path, DNS detail, or upstream HTML.
- Errors use `--status-danger` and `--status-danger-soft`, but the message text and icon carry the meaning.
- The error region is announced assertively once; the result region is announced politely once.
- On failure, preserve the submitted URL in component memory so the user can correct or retry without retyping.

Loading and success rules:

- Loading shows a stable layout: input stays in place, button reports progress, result panel shows three static skeleton rows.
- A successful result shows only values returned by the API. No invented file size, speed, speedup, or progress percentage.
- Empty state explains the accepted URL classes with one example, then stays quiet until input begins.

Depth rules:

- Tonal shift only. No shadow token exists, and no shadow utility may be introduced.
- Raised surfaces are reserved for the work area, the account panel, the result panel, and the statistics panel; nested raised cards are forbidden.
- Overlapping and floating section decoration is prohibited.

## 6. Motion & Accessibility

Motion is confirmation, never spectacle. It uses only `transform` and `opacity` so it stays on the compositor.

| Token | Duration | Easing | Usage |
|-------|----------|--------|-------|
| `--duration-fast` | 120ms | `ease-out` | Press, copy confirmation |
| `--duration-standard` | 200ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Result panel change |
| `--duration-emphasis` | 320ms | `cubic-bezier(0.16, 1, 0.3, 1)` | First page entry |

Rules:

- First paint fades from opacity 0 to 1 while translating 8px upward. It runs once.
- Result success or error fades and translates 8px upward. It does not bounce, shake, or flash.
- Skeleton loading uses a slow opacity pulse between 0.45 and 1. It never animates background position.
- Every wait state carries a skeleton whose box matches the content it stands in for — the same card, columns, padding and control heights — so the real content lands without shifting the page. The bars are `aria-hidden`; the wait itself is still announced in words by a visually hidden live region, and the existing status text is never removed.
- No infinite decorative motion, parallax, marquee, typing effect, or scroll listener exists.
- `prefers-reduced-motion: reduce` sets all durations and delays to 1ms, removes transforms and skeleton pulsing, and keeps the loading label plus `aria-busy` as the state signal.

Accessibility contract:

- Use `header`, `main`, and `footer` landmarks, one `h1`, and a logical focus order.
- All controls are real `button`, `input`, and `a` elements. Clickable `div` elements are prohibited.
- Focus indicators meet 3:1 against both the control and the adjacent surface and are never clipped by `overflow: hidden`.
- Minimum interactive target is 44px by 44px, including icon-only actions.
- Completion with the keyboard alone must reach input, submit, copy, download, and reset in that order.
- Live regions exist in the initial DOM: one polite result region and one assertive error region. Region text is not duplicated visually.
- `forced-colors` mode keeps borders and focus visible; `prefers-contrast: more` strengthens borders and text.
- The complete flow remains usable at 200% browser zoom and at a 320px CSS width with no horizontal scrolling.
- Text contrast meets WCAG AA 4.5:1; non-text control contrast and focus indicators meet 3:1.

## 7. Responsive & Anti-Slop Rules

### 375px, Mobile

- Use 16px gutters and a single column.
- The header wraps: brand on the first line, navigation and theme toggle on the second, the session chip on a third when signed in. It never shrinks, overlaps, or scrolls sideways.
- Input and button stack vertically at full width, each at least 44px high.
- The result panel appears directly below the form without an intermediate hero band.
- The feature strip stacks into three rows separated by 1px horizontal dividers; it is not a horizontal scroller.
- Long URLs break inside the input and result without expanding the page.

### 768px, Tablet

- Use 24px gutters and one column of work area.
- The header fits on one line.
- The work area is still one column so the form and result are not compressed.
- The feature strip becomes a single horizontal band with three unequal columns and vertical dividers.
- The primary action remains full width directly under the input.

### 1280px, Desktop

- Use 32px gutters and the 896px maximum content width.
- The work area becomes an asymmetric 7/5 grid: form left, result right, with `--space-10` between columns.
- The form and result panel align to the top. Neither is vertically centered in a decorative hero.
- The header bar spans the window while the content column stays centered, exactly as the blog's inner pages are laid out.
- The form, result, feature strip, and promo card all sit on the same visible alignment rhythm.

### Anti-Slop Checklist

Every component and review must satisfy all of these:

- One primary conversion flow; no secondary navigation beyond the header, no pricing, testimonials, or marketing sections.
- No centered hero, gradient background, gradient text, glow, or glassmorphism. `backdrop-filter` is used by the sticky header bar and by nothing else.
- No decorative dashboard cards, nested cards, bento grid, fake charts, fake usage metrics, or fake testimonials.
- No three equal floating feature cards; the capability strip is one band with unequal columns.
- No emoji used as an icon or in visible copy. All icons are local SVG primitives with consistent 1.75px–2px stroke.
- No third-party font, icon, script, analytics, or tracking request. Fonts and icons are same-origin assets.
- No raw hex, magic spacing, arbitrary font size, or ad-hoc easing in components; all values come from this document.
- No hidden state that exists in only one theme. Every surface, border, and text pair is defined in both the light and the dark palette; a hardcoded colour, or a `#fff` that assumes a light page, is a defect.
- No `100vh`, horizontal scroll, decorative infinite animation, or motion that ignores reduced-motion.
- No value is displayed unless it came from the API. Unknown metadata stays absent.
- No rounded corner. The radius tokens are 0 and no component may restate a pixel radius.
- Any new reusable pattern used twice or a genuinely new semantic token must be added here before implementation continues.
