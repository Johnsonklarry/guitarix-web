# Theme kit

Shared themes for the orchestration dashboard, guitarix-web and family-app. The contract (every variable name, OLED, HDR and mobile rules) is in `.delegate/theme-contract.md`.

## Themes

| id | Name shown to people | Mood |
|---|---|---|
| `bag-end` (default) | Fireside | warm oak, candle amber, parchment, moss: calm, for long glances |
| `plex-amber` | Amber | amp-lamp orange fading into black, cinematic |
| `amp-lamp` | Amp lamp | Guitarix tolex, cream and lamp orange |
| `studio-glass` | Studio | sleek dark with a restrained amber |
| `mission-control` | Console | bold numbers, strong status colours |
| `light` | Light | warm paper |
| `dark` | Dark | neutral warm dark |

Every theme has an OLED variant: `data-oled="true"` gives pure black surfaces, hairline edges and accents only (no large glows), which is kinder to OLED panels left on for hours.

## Include

1. Fonts (one request):
   `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alegreya:wght@400;600&family=Barlow+Condensed:wght@500;600&family=Cormorant+Garamond:wght@500;600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap">`
2. `<link rel="stylesheet" href="theme-kit/themes.css">`
3. Inline the contents of `theme.js` in a `<script>` in `<head>` (no flash of the wrong theme); linking it without `defer` also works.
4. Use only the contract's variables in your CSS: `background: var(--surface); color: var(--text); box-shadow: var(--shadow-2);`, key-number cards `background-image: var(--card-glow);`, touch targets `min-height: var(--tap-min);`.
5. `ThemeKit.mountSwitcher(element)` adds the theme picker and the OLED button (a plain select and button, keyboard and screen-reader friendly).

## JavaScript API

`ThemeKit.get()`, `ThemeKit.set(id, {oled})`, `ThemeKit.toggleOled()`, `ThemeKit.onChange(fn)`, `ThemeKit.animate(fn)` (requestAnimationFrame loop: smooth at 60 to 240 Hz, pauses when hidden, respects reduced motion), `ThemeKit.burnInGuard({...})` (optional OLED pixel shift and idle dimming).

## HDR, 10-bit and banding

Wide-gamut screens (`@media (color-gamut: p3)`) get display-p3 accents and gradients; HDR (`dynamic-range: high`) only brightens accents, never body text. Gradients carry a faint noise dither (`--dither`) so 8-bit panels do not band.

## Copying into the other apps

`py -3 theme-kit/sync.py --dry-run` shows what would change; without `--dry-run` it copies `themes.css`, `theme.js` and this README into `guitarix-web/guitarix-web/static/theme-kit` and `Family/app/static/theme-kit` (next to this repo). It never deletes and refuses other paths. Commit the copies in each app's repo.

## Adding a theme

Add a `html[data-theme="<id>"]` block and its `[data-oled="true"]` variant that set every contract variable, check text and muted text at 4.5:1 on every surface, and add the id to `ThemeKit.themes` in `theme.js`.
