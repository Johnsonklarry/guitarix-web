# Theme Kit

A small, dependency-free CSS theme kit plus a matching JavaScript controller (`theme.js`).
Themes are selected by attributes on the root element, so any app can adopt the kit by
copying two files and setting two attributes.

## Hide themes for this browser

Use `ThemeKit.hide(id)` and `ThemeKit.unhide(id)` to control which themes appear in the switcher. `ThemeKit.hidden()` returns hidden IDs, and `ThemeKit.visibleThemes()` returns the themes still shown. The switcher's “Manage themes…” disclosure has Show checkboxes for every theme. Preferences are stored locally under `theme-kit-hidden`; hiding the active theme does not change it or remove it from the switcher until another theme is selected. At least one theme must remain visible. Changes dispatch `themekit:hidden` on `document`.

## Gamepad navigation

`gamepad.js` gives any page Xbox/XInput (W3C "standard" mapping) navigation with one tag: `<script src="theme-kit/gamepad.js" defer></script>`. It does nothing until the browser fires `gamepadconnected`, stops on `gamepaddisconnected` and pauses while the tab is hidden; mouse, touch and keyboard are untouched (any pointer or key press clears the pad ring).

| Control | Action |
|---|---|
| D-pad / left stick | move focus to the nearest control in that direction (deadzone 0.3; hold to repeat: 400 ms, then every 110 ms) |
| A | activate: `click()`, plus an Enter `keydown` on custom (non-native) widgets |
| B | back: close `dialog[open]`, else collapse the `[aria-expanded="true"]` control, else close the `details[open]` around focus, else click `[data-nav-back]`, else `history.back()` |
| Y | theme switcher: focus `[data-nav-theme]` or the `ThemeKit.mountSwitcher` select and enter edit mode (d-pad changes the value, A or B leaves); without one, cycles `ThemeKit.set` |
| LB / RB | previous / next `[role="tab"]` in the current tablist, else previous / next `[data-nav-group]` section (lands on its `[data-nav-default]` or first control) |
| Start (menu) | focus the main nav: `[data-nav-main]`, else `nav`, `[role="navigation"]`, `header` |
| Right stick | scroll the focused scroll container or the page |

The focused control gets class `gp-focus` (ring from `--focus-ring`, so it is theme-coloured in every theme and OLED mode) and is kept on screen with `scrollIntoView({block: "nearest"})`; `<html data-gamepad="true">` is set while a pad is connected. Optional markup: `data-nav-group` on sections, `data-nav-default` on the control to land on first (page or group), `data-nav-skip` to hide controls from the pad. Tag options: `data-nav-auto="false"` (call `GamepadNav.start()` yourself), `data-nav-toast="false"`.

`GamepadNav.start(options)`, `GamepadNav.stop()`, `GamepadNav.configure({deadzone, repeatDelayMs, repeatIntervalMs, scrollSpeed, themeSwitcher, mainNav, onAction})`, `GamepadNav.onAction(fn)` (return `false` to take over an action: `activate back x theme prev next lt rt view menu ls rs up down left right`), `GamepadNav.handleAction(name)`, `GamepadNav.moveFocus(direction)`. `GamepadNav.pure` holds the DOM-free helpers (`pickNearest`, `stepFrame`, `stickDirection`, `repeatTick`) used by `tests/smoke_gamepad_nav.mjs`. Forge pages that used `forge_core/static/gamepad.js` keep the `ForgeGamepad` API; it now delegates to this kit.

## Bag End Doorway (`bag-end-doorway`)

A scene theme: you stand inside Bag End looking out of the open round door, over the front garden and the gate, down the Hill, across the Water to Hobbiton and the far downs. Light (the native look) is a sunny midday with drifting clouds, glints on the Water and butterflies in the garden; dark is golden hour with the hall lantern lit and fireflies; OLED is a true-black night where only the doorway rim, the lantern, the moon on the Water and the lit windows glow. Everything is CSS plus inline SVG on `html::before/::after` and `body::before/::after` (no markup needed), measured from one square `min(110vw, 100vh)` so the whole door stays centred on a portrait phone. Animation is transform/opacity only and stops under `prefers-reduced-motion`, `(update: slow)`, `html[data-fx="off"]` (drops the two animated layers) and print. HDR (`dynamic-range: high`) and P3 (`color-gamut: p3`) blocks brighten the sun, lantern and brass accent in oklch.
