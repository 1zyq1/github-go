# GitHub Accelerator Design System

## 1. Direction & Atmosphere

This is a precise relay console, not a marketing page. The interface should feel engineered, quiet, and trustworthy: a user arrives with one GitHub URL and leaves with one verified download link. The signature is a single signal route, expressed once in the network mark and repeated as a thin accent rule that connects the form to the result. Everything else stays neutral and quiet.

The page has exactly one primary conversion flow: paste URL, submit, receive a download action. There is no centered hero, no decorative dashboard, no glassmorphism, no gradient background, and no fabricated usage metric. The visual language is closer to a well-made network appliance than a SaaS landing page: hairline structure, deliberate spacing, real states, and no ornament that competes with the task.

The product is Chinese-first, with concise English used only where it is a stable technical term. URLs, file kinds, and file names use the mono stack so structure is visible at a glance. The theme is light-only and declares `color-scheme: light`; there is no automatic or decorative dark mode.

## 2. Color Tokens

There is one accent hue: a deep azure. Success, warning, and danger colors are semantic signals only and are never used decoratively. Pure black, purple gradients, neon glows, and translucent panels are prohibited.

| Role | Token | Value | Contrast target | Usage |
|------|-------|-------|-----------------|-------|
| Canvas | `--surface-canvas` | `#F5F6F8` | - | Page background |
| Raised surface | `--surface-raised` | `#FFFFFF` | - | Form, result panel |
| Sunken surface | `--surface-sunken` | `#EDEFF3` | - | Disabled controls, code chips |
| Inverse surface | `--surface-inverse` | `#0D1117` | - | Short mono chips only |
| Text primary | `--text-primary` | `#0D1117` | 17.50:1 on canvas | Headings, body, file name |
| Text secondary | `--text-secondary` | `#3D4653` | 8.83:1 on canvas | Supporting copy |
| Text muted | `--text-muted` | `#5B6472` | 5.53:1 on canvas | Hints, metadata |
| Text on accent | `--text-on-accent` | `#FFFFFF` | 6.57:1 on accent | Primary button label |
| Border default | `--border-default` | `#D5DAE1` | - | Inputs, dividers |
| Border strong | `--border-strong` | `#A8B1BD` | - | Hover and high-contrast edges |
| Accent | `--accent` | `#0B5FA5` | 6.57:1 with white | Primary action, links, focus |
| Accent hover | `--accent-hover` | `#084C87` | 8.77:1 with white | Primary action hover |
| Accent soft | `--accent-soft` | `#E8F0F9` | 5.72:1 with accent text | Selected or informational tint |
| Accent ring | `--accent-ring` | `rgba(11, 95, 165, 0.32)` | - | Focus and validation halo |
| Success | `--status-success` | `#15803D` | 5.02:1 with white | Successful resolution |
| Success soft | `--status-success-soft` | `#ECFDF3` | - | Success background |
| Warning | `--status-warning` | `#A16207` | 4.92:1 with white | Caution text |
| Danger | `--status-danger` | `#B42318` | 6.57:1 with white | Errors and invalid input |
| Danger soft | `--status-danger-soft` | `#FEF3F2` | - | Error background |

Rules:

- Body text must meet WCAG AA at normal size; the documented minimum here is 4.5:1 and all text/background pairs above exceed it.
- Interactive text and icons must meet 3:1 against adjacent colors in addition to the text contrast target.
- Color never carries meaning alone. Errors, success, and focus also use text, shape, or an icon.
- No raw color value may appear outside this table. Extend this table before introducing a new semantic role.
- Focus uses `--accent` with `--accent-ring`; it is never removed as decoration.

## 3. Typography

Two self-hosted variable families are used. Plus Jakarta Sans handles Latin text and interface numerals. Noto Sans SC handles Chinese text. Both are bundled as same-origin `woff2` files under `frontend/public/fonts/` and declared in `frontend/src/styles/fonts.css`; no third-party font CDN is allowed.

```css
--font-sans: "Plus Jakarta Sans", "Noto Sans SC", system-ui, -apple-system, "Segoe UI",
  sans-serif;
--font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
```

| Level | Token | Size | Weight | Line height | Tracking | Usage |
|-------|-------|------|--------|-------------|----------|-------|
| Display | `--text-display` | `clamp(2rem, 1.5rem + 2vw, 2.75rem)` | 700 | 1.15 | -0.02em | Single page title |
| Heading | `--text-heading` | 24px | 650 | 1.3 | -0.01em | Panel headings |
| Subheading | `--text-subheading` | 20px | 600 | 1.4 | 0 | Result file name |
| Lead | `--text-lead` | 18px | 400 | 1.55 | 0 | One-sentence explanation |
| Body | `--text-body` | 16px | 400 | 1.625 | 0 | Default copy |
| Small | `--text-small` | 14px | 400/500 | 1.55 | 0 | Hints, helper text, actions |
| Caption | `--text-caption` | 12px | 500 | 1.5 | 0.02em | Non-critical metadata only |
| Overline | `--text-overline` | 12px | 650 | 1.4 | 0.08em | Short section labels |

Rules:

- Body and hint text never go below 14px. 12px is reserved for short metadata such as a file-kind label.
- Paragraph measure is capped at `62ch`.
- URLs, file names, and tokens use `--font-mono`, 14px minimum, and `overflow-wrap: anywhere`.
- There is no serif face, no gradient text, and no all-caps Chinese text. Overline casing applies only to Latin labels.
- A page has one `h1`. Component heading levels follow document order without skipping levels.

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

- Page content uses a 12-column grid with a `1080px` maximum width.
- Gutters are 16px at 375px, 24px at 768px, and 32px at 1280px.
- Full-height behavior uses `min-height: 100dvh`, never `100vh` or `h-screen`.
- At 1024px and above, the work area is asymmetric: 7 columns for the form and 5 for the result. Below 1024px it becomes one column.
- The feature strip is one bordered band, not three floating cards. Its desktop columns are `1.2fr 1.6fr 1.2fr` with hairline vertical dividers.
- Radii are restrained: 16px for the main work surface, 10px for inputs and buttons, 6px for badges. Pills are reserved for a 6px status dot, not containers.

Depth strategy: **tonal shift**. Depth comes from `--surface-canvas`, `--surface-raised`, and `--surface-sunken`. Shadows, blur, and `backdrop-filter` are prohibited. Hairline borders are allowed only for controls, dividers, and focus boundaries.

### AppHeader And NetworkMark

- **Structure**: `header` with the local SVG `NetworkMark`, product name, and a one-line purpose statement. The right-hand aside carries the site navigation (加速 / 数据) and, when a session exists, the SessionChip.
- **Variants**: anonymous (brand and navigation) and signed-in (brand, navigation, and SessionChip).
- **Spacing**: `--space-3` between mark and text; `--space-1` between navigation links and `--space-3` between navigation and SessionChip; `--space-4` between brand and aside when stacked, `--space-8` when side by side; `--space-16` from header to work area on desktop.
- **States**: the mark is static with no hover animation; a navigation link has hover, focus-visible, and current.
- **Accessibility**: the SVG is decorative and marked as such; the product name is real text; the navigation is a labelled `nav`, and the current entry is marked with an accent tint *and* an accent colour so the state never rests on colour alone.
- **Motion**: one 320ms fade-and-rise entry on first paint only.

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
- **States**: default, hover, focus-visible, invalid, disabled, submitting. The selected mode segment uses `--surface-raised` on a `--surface-sunken` track.
- **Accessibility**: the mode switch is a `radiogroup` whose inputs stay focusable but visually hidden, with `:focus-within` drawing the ring on the visible segment; each field links its hint and error through `aria-describedby`; a validation failure moves focus to the field at fault; server errors land on the field that owns them, and only unowned failures use the form-level region. The pending confirmation line is the only success-coloured text in the panel and is announced through `role="status"`, because it reports progress rather than a problem.
- **Motion**: no entrance of its own; it inherits the work-area entry. The submit label changes in place.

### SessionChip

- **Structure**: the signed-in account's name, its remaining allowance, an optional reset hint, and one quiet sign-out button.
- **Variants**: allowance remaining, and exhausted. When exhausted the allowance line turns `--status-warning` and the reset hint appears; the hint is shown only then, because the API reports a reset time only once a unit is actually blocking.
- **Spacing**: `--space-1` between lines, `--space-8` from the brand on desktop.
- **States**: default, hover, focus-visible, disabled, submitting.
- **Accessibility**: the name is text, not a badge; the exhausted allowance is never signalled by colour alone, because the number itself reads `剩余 0`; right-aligned on desktop and left-aligned when stacked.
- **Motion**: none.

### Notice

- **Structure**: a single line of text on a raised surface with a hairline border, used only for page-level conditions that are not tied to one field.
- **Variants**: `notice--warning` is the only variant shipped.
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

- **Structure**: a 1200x630 `og.png` served from the build root. An azure bar closes the top, a rounded azure mark carrying the favicon glyph sits beside the public host as the wordmark, the product name is the largest text, one line states the purpose, four capability chips follow, and a ruled footer closes the card.
- **Variants**: one. It is a static asset under `frontend/public/og.png`, not a component, so nothing here responds to state.
- **Spacing**: the artwork is drawn at the viewport size with no scrolling and the content box is exactly 1200x630, because a card that overflows is cropped by the platform instead of being scaled.
- **States**: none.
- **Accessibility**: every claim on the card repeats text already present on the page, and `og:image:alt` carries the same wording.
- **Motion**: none. Depth stays tonal, so the card takes the canvas, the azure accent and the hairlines from the tokens above, with no shadow, gradient or glow.

## 5. States & Depth

Every interactive element defines all applicable states. Missing states are a defect, not a styling detail.

| Element | Default | Hover | Active | Focus-visible | Disabled | Loading / Invalid |
|---------|---------|-------|--------|---------------|----------|-------------------|
| Input | White, `--border-default` | `--border-strong` | No transform | 2px accent outline plus 2px offset and ring | Sunken surface, muted text, `not-allowed` | Danger border, icon, message, `aria-invalid` |
| Primary button | Accent background, white label | `--accent-hover` | Translate down 1px | Same outline contract | 45% neutral surface, no pointer | Static label plus transform-based progress line |
| Copy action | Raised surface, default border | Strong border and icon accent | Translate down 1px | Same outline contract | Hidden or disabled when unavailable | Label changes to confirmed state |
| Result panel | Raised surface | Not interactive | Not interactive | Focus moves to new action | Not applicable | Skeleton rows or inline error |
| Account panel | Raised surface, hairline border | Not interactive | Not interactive | Segment ring plus field outlines | Sunken segment track, muted label | Field error below the field; unowned failure in the form region |
| Mode segment | Sunken track, secondary label | Track stays sunken | No transform | 2px accent outline on the visible segment | Muted label, no pointer | Selected segment turns raised with accent label |
| Session chip | Canvas, no surface | Quiet button only | Translate down 1px | Same outline contract | Hidden when signed out | Exhausted allowance in `--status-warning` |
| Notice | Raised surface, warning border | Not interactive | Not interactive | No focus stop | Not applicable | Not applicable |
| Feature strip | Static | Not interactive | Not interactive | No focus stop | Not applicable | Not applicable |
| Data table | Raised surface, hairline row rules | Not interactive | Not interactive | No focus stop; the table is not a control | Not applicable | Loading line or inline error replaces the rows |
| Auth status line | Canvas, success-coloured text | Not interactive | Not interactive | No focus stop | Not applicable | Names the mailed address; never carries a failure |
| Nav link | Canvas, no surface | Sunken tint plus primary text | Translate down 1px | Same outline contract | Not applicable | Current entry: accent tint plus accent text |

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
- Skeleton loading uses a slow opacity pulse between 0.55 and 1. It never animates background position.
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
- The page title uses the lower end of `--text-display`; no text wraps into more than three lines.
- Input and button stack vertically at full width, each at least 48px high.
- The result panel appears directly below the form without an intermediate hero band.
- The feature strip stacks into three rows separated by 1px horizontal dividers; it is not a horizontal scroller.
- Long URLs break inside the input and result without expanding the page.

### 768px, Tablet

- Use 24px gutters and a 720px maximum content width.
- The work area is still one column so the form and result are not compressed.
- The feature strip becomes a single horizontal band with three unequal columns and vertical dividers.
- The primary action remains full width directly under the input.

### 1280px, Desktop

- Use 32px gutters and the 1080px maximum content width.
- The work area becomes an asymmetric 7/5 grid: form left, result right, with `--space-10` between columns.
- The form and result panel align to the top. Neither is vertically centered in a decorative hero.
- The title, form, result, and feature strip all sit on the same visible alignment rhythm.

### Anti-Slop Checklist

Every component and review must satisfy all of these:

- One primary conversion flow; no secondary navigation, pricing, testimonials, or marketing sections.
- No centered hero, gradient background, gradient text, glow, glassmorphism, or `backdrop-filter`.
- No decorative dashboard cards, nested cards, bento grid, fake charts, fake usage metrics, or fake testimonials.
- No three equal feature cards; the capability strip is one band with unequal columns.
- No emoji used as an icon or in visible copy. All icons are local SVG primitives with consistent 1.75px stroke.
- No third-party font, icon, script, analytics, or tracking request. Fonts and icons are same-origin assets.
- No raw hex, magic spacing, arbitrary font size, or ad-hoc easing in components; all values come from this document.
- No `100vh`, horizontal scroll, decorative infinite animation, or motion that ignores reduced-motion.
- No value is displayed unless it came from the API. Unknown metadata stays absent.
- Any new reusable pattern used twice or a genuinely new semantic token must be added here before implementation continues.
