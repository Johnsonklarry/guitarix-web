#!/usr/bin/env python3
"""
UI audit, measured in a real browser against the demo (preview.html).

    python3 build_preview.py && python3 tests/ui_audit.py

Needs Playwright with Chromium (pip install playwright; playwright install
chromium) -- a desktop-side tool, not something the Pi needs.

For every kind of control on the page it checks:

  feedback   hover, keyboard focus and press are each forced through the
             browser's devtools protocol, and the computed style compared
             with the resting one: if nothing visible changes, the control
             gives no feedback for that state. It also notes whether the
             control animates between states (a transition) or snaps.
  contrast   every piece of visible text against the colour actually behind
             it, to WCAG AA: 4.5:1, or 3:1 for large text. Disabled and
             locked controls are exempt, as WCAG exempts them.
  targets    anything you tap smaller than 24 x 24 px (WCAG 2.2 AA).
  spacing    sibling controls that overlap, or sit closer than 4 px.
  overflow   anything that makes a phone screen scroll sideways.

It runs across desktop and phone sizes, Live mode, and the dialogs.
"""

import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = "file://" + os.path.join(ROOT, "preview.html")
# This audit drives preview.html only: it makes no claim about app.py or about
# the content demo.py had before deletion (see tests/test_bash_16701.py).

CONTROLS = ("button, a[href], select, input:not([type=hidden]), label.act, label.dry-toggle, "
            "[role=tab], .takes__badge.is-action")
# The JACK status readout is not a control: it is checked for a legible state
# (see MEASURE) but kept out of the target-size, spacing and feedback checks,
# which only make sense for things you point at.
JACK = ".jack-status, [data-jack-state]"
# the states the readout may report; keep in step with the routing code the way
# tests/test_bash_25701.py does
JACK_STATES = ["disconnected", "connecting", "connected", "error"]
WATCH = ["backgroundColor", "backgroundImage", "color", "borderTopColor", "boxShadow",
         "outlineStyle", "outlineColor", "transform", "opacity", "textDecorationLine", "filter"]

MEASURE = r"""
() => {
  const px = s => parseFloat(s) || 0;
  function rgba(s) {
    const m = (s || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function gradientColour(img) {
    // a gradient's colour is taken as the average of its (mostly opaque) stops
    const stops = (img.match(/rgba?\([^)]+\)/g) || []).map(rgba).filter(c => c.a > 0.5);
    if (!stops.length) return null;
    const n = stops.length;
    return { r: stops.reduce((s, c) => s + c.r, 0) / n, g: stops.reduce((s, c) => s + c.g, 0) / n,
             b: stops.reduce((s, c) => s + c.b, 0) / n, a: 1 };
  }
  function behind(el) {
    const layers = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const g = cs.backgroundImage !== 'none' ? gradientColour(cs.backgroundImage) : null;
      const c = g || rgba(cs.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let out = { r: 11, g: 9, b: 8 };                       // the page itself
    for (let i = layers.length - 1; i >= 0; i--) {
      const c = layers[i];
      out = { r: c.r * c.a + out.r * (1 - c.a), g: c.g * c.a + out.g * (1 - c.a), b: c.b * c.a + out.b * (1 - c.a) };
    }
    return out;
  }
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const overlay = document.querySelector('#live:not([hidden])');
  const visible = el => {
    if (el.closest('[hidden],[inert]')) return false;
    if (overlay && !overlay.contains(el)) return false;      // covered by Live mode
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.visibility === 'hidden' || cs.display === 'none' || px(cs.opacity) === 0) return false;
    }
    return true;
  };
  // the JACK connection status indicator: its state must be legible from the
  // DOM (data-jack-state) and it must carry a text or aria-label name, so the
  // audit can tell "connected" from "error" without relying on colour alone
  // name/text are used by every check below, so they are declared first: using
  // them from a block above these consts would hit the temporal dead zone and
  // throw, taking the whole measurement (and its findings) down with it
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
    + (el.classList.length ? '.' + [...el.classList].join('.') : '');
  const text = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 28);
  // the JACK connection status indicator: its state must be legible from the
  // DOM (data-jack-state), be one of the states the routing code reports, and
  // carry a text or aria-label name, so the audit can tell "connected" from
  // "error" without relying on colour alone
  const jack = [];
  const states = %JACKSTATES%;
  document.querySelectorAll(%JACK%).forEach(el => {
    if (!visible(el)) return;
    const state = el.getAttribute('data-jack-state') || '';
    const label = (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!state) jack.push({ el: name(el), text: text(el), issue: 'no data-jack-state attribute' });
    else if (states.indexOf(state) < 0) jack.push({ el: name(el), text: text(el), issue: 'unknown data-jack-state ' + state });
    else if (!label) jack.push({ el: name(el), text: text(el), issue: 'no text or aria-label for state ' + state });
  });

  // contrast: elements that carry text themselves
  const contrast = [];
  document.querySelectorAll('body *').forEach(el => {
    if (!visible(el) || el.closest(':disabled')) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own && !['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    const fg = rgba(cs.color);
    if (!fg) return;
    let eff = 1;                                             // faded by an ancestor's opacity?
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) eff *= px(getComputedStyle(e).opacity) || 1;
    if (eff < 0.99) return;                                  // deliberately dimmed state, like bypassed
    const bg = behind(el);
    const col = { r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a) };
    const size = px(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    const got = ratio(col, bg);
    if (got < need) contrast.push({ el: name(el), text: text(el), ratio: +got.toFixed(2), need: need,
      fg: cs.color, size: size });
  });

  // targets, spacing, overflow
  const controls = [...document.querySelectorAll(%CONTROLS%)].filter(visible);
  const small = [], tight = [], spill = [];
  controls.forEach(el => {
    if (el.matches('input[type=checkbox]') && el.closest('label')) return;   // the label is the target
    const r = el.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) small.push({ el: name(el), text: text(el), w: Math.round(r.width), h: Math.round(r.height) });
  });
  const seen = new Set();
  controls.forEach(el => {
    if (el.matches('[role=tab]')) return;          // tab strips are joined by design
    const sibs = [...(el.parentElement ? el.parentElement.children : [])].filter(s => s !== el && controls.includes(s));
    const a = el.getBoundingClientRect();
    sibs.forEach(s => {
      const key = [el, s].map(name).sort().join('|') + '|' + name(el.parentElement);
      if (seen.has(key)) return;
      const b = s.getBoundingClientRect();
      const gapX = Math.max(b.left - a.right, a.left - b.right);
      const gapY = Math.max(b.top - a.bottom, a.top - b.bottom);
      const gap = Math.max(gapX, gapY);
      if (gap < 4) { seen.add(key); tight.push({ a: name(el), b: name(s), gap: Math.round(gap) }); }
    });
  });
  const vw = document.documentElement.clientWidth;
  const pageScrolls = document.documentElement.scrollWidth > vw + 1;
  document.querySelectorAll('body *').forEach(el => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    if (r.right <= vw + 1) return;
    for (let e = el.parentElement; e; e = e.parentElement) {
      const ox = getComputedStyle(e).overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return;   // scrolls inside its own box
    }
    spill.push({ el: name(el), right: Math.round(r.right), vw: vw });
  });
  return { contrast, small, tight, pageScrolls, spill: spill.slice(0, 8), jack };
}
""".replace("%CONTROLS%", repr(CONTROLS)).replace("%JACK%", repr(JACK)) \
   .replace("%JACKSTATES%", repr(JACK_STATES))

KINDS = r"""
(sel) => {
  const out = [], seen = new Set();
  [...document.querySelectorAll(sel)].forEach((el, i) => {
    if (el.closest('[hidden],[inert]') || el.disabled) return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const kind = el.tagName.toLowerCase() + '.' + [...el.classList].filter(c => !/^is-/.test(c)).sort().join('.')
               + (el.type && el.tagName === 'INPUT' ? '[' + el.type + ']' : '');
    if (seen.has(kind)) return;
    seen.add(kind);
    el.setAttribute('data-audit', String(out.length));
    out.push({ kind: kind, text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 20) });
  });
  return out;
}
"""


def styles(page, idx, props):
    return page.evaluate("""([i, props]) => {
        const el = document.querySelector('[data-audit="' + i + '"]');
        const cs = getComputedStyle(el);
        const out = {}; props.forEach(p => out[p] = cs[p]);
        if (el.type === 'range') {           // a slider's look lives on its thumb
          const th = getComputedStyle(el, '::-webkit-slider-thumb');
          props.forEach(p => out['thumb.' + p] = th[p]);
        }
        out.transition = cs.transitionDuration;
        return out; }""", [idx, props])


def feedback(page, label, report):
    cdp = page.context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    kinds = page.evaluate(KINDS, CONTROLS)
    animated = {}
    for i in range(len(kinds)):
        d = page.evaluate("i => getComputedStyle(document.querySelector('[data-audit=\"' + i + '\"]')).transitionDuration", i)
        animated[i] = any(float(x.strip().rstrip("ms") or 0) > 0 for x in d.split(","))
    page.evaluate("""() => {
        if (document.activeElement) document.activeElement.blur();
        const s = document.createElement('style'); s.id = 'audit-still';
        s.textContent = '*, *::before, *::after, *::-webkit-slider-thumb { transition: none !important; animation: none !important; }';
        document.head.appendChild(s); }""")
    root = cdp.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
    for i, k in enumerate(kinds):
        node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": '[data-audit="%d"]' % i})["nodeId"]
        base = styles(page, i, WATCH)
        keys = [k for k in base if k != "transition"]
        missing = []
        if "[range]" in k["kind"]:
            # a slider's look is on its thumb, which the browser won't report as
            # a style -- so compare pixels instead: rest, then each state
            loc = page.locator('[data-audit="%d"]' % i)
            loc.scroll_into_view_if_needed()
            rest = loc.screenshot()
            for state in ("hover", "focus-visible", "active"):
                forced = ["focus", "focus-visible"] if state == "focus-visible" else [state]
                cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": forced})
                shot = loc.screenshot()
                cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": []})
                if shot == rest:
                    missing.append(state)
            report.setdefault(k["kind"], {"text": k["text"], "missing": set(missing), "animated": True, "where": label})
            report[k["kind"]]["missing"] &= set(missing)
            continue
        for state in ("hover", "focus-visible", "active"):
            forced = ["focus", "focus-visible"] if state == "focus-visible" else [state]
            cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": forced})
            now = styles(page, i, WATCH)
            cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": []})
            if all(now[k] == base[k] for k in keys):
                # clicking a text field or a dropdown focuses it, so focus IS its
                # pressed look; a separate :active style would never be seen
                if state == "active" and (k["kind"].startswith(("select", "textarea")) or "[text]" in k["kind"]):
                    continue
                missing.append(state)
        report.setdefault(k["kind"], {"text": k["text"], "missing": set(missing), "animated": animated[i], "where": label})
        report[k["kind"]]["missing"] &= set(missing)      # a state that shows anywhere, shows
        report[k["kind"]]["animated"] |= animated[i]
    page.evaluate("() => { document.querySelectorAll('[data-audit]').forEach(e => e.removeAttribute('data-audit'));"
                  " const s = document.getElementById('audit-still'); if (s) s.remove(); }")


def main():
    errors, results, fb = [], {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def open_page(desktop):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900} if desktop else
                                      {"width": 390, "height": 844},
                                      is_mobile=not desktop, has_touch=not desktop)
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(PAGE)
            pg.wait_for_timeout(700)
            return pg

        def audit(pg, label, check_feedback=False):
            results[label] = pg.evaluate(MEASURE)
            if check_feedback:
                feedback(pg, label, fb)

        pg = open_page(True)
        audit(pg, "desktop", True)
        pg.click("#btn-import"); pg.wait_for_timeout(300)
        pg.fill("#imp-text", '{"presets":[{"name":"A","params":{"amp.fuzz":0.3,"nope.x":1}}]}')
        pg.click("#imp-check"); pg.wait_for_timeout(700)
        audit(pg, "desktop, importer", True)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        pg.locator("tr", has=pg.locator(".takes__name-text", has_text="Attempt 3.wav")).locator("text=More").click()
        pg.click("text=Export…"); pg.wait_for_timeout(300)
        audit(pg, "desktop, export dialog", True)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
        pg.click(".takes__actions >> text=Reamp"); pg.wait_for_timeout(800)
        pg.click(".backing__item >> text=Play"); pg.wait_for_timeout(800)
        audit(pg, "desktop, reamp + backing", True)
        pg.click("#btn-live"); pg.wait_for_timeout(400)
        audit(pg, "desktop, live", True)

        ph = open_page(False)
        audit(ph, "phone, presets")
        ph.click(".tab[data-tab=rec]"); ph.wait_for_timeout(300)
        audit(ph, "phone, record")
        ph.click(".tab[data-tab=eq]"); ph.wait_for_timeout(300)
        audit(ph, "phone, eq")
        ph.click("#btn-live"); ph.wait_for_timeout(400)
        audit(ph, "phone, live")
        browser.close()

    problems = 0
    print("FEEDBACK  (states that change nothing visible)")
    for kind, r in sorted(fb.items()):
        if r["missing"] or not r["animated"]:
            problems += 1
            print("  %-46s %-18s missing: %-28s %s" % (kind[:46], '"' + r["text"][:16] + '"',
                  ", ".join(sorted(r["missing"])) or "-", "" if r["animated"] else "(snaps: no transition)"))
    print("  %d kinds of control checked\n" % len(fb))

    for label, r in results.items():
        by_colour = {}
        for c in r["contrast"]:
            by_colour.setdefault(c["fg"], []).append(c)
        items = ([("contrast", "%s at %.1f-%.1f:1 (needs %.1f) on %d elements, e.g. %s"
                   % (fg, min(x["ratio"] for x in cs), max(x["ratio"] for x in cs), max(x["need"] for x in cs),
                      len(cs), ", ".join(sorted({x["el"].split(".")[0].split("#")[-1] if "#" in x["el"] else x["el"].split(".")[1] if "." in x["el"] else x["el"] for x in cs}))[:70]))
                  for fg, cs in by_colour.items()] +
                 [("target", "%s %r %dx%d" % (s["el"][:40], s["text"], s["w"], s["h"])) for s in r["small"]] +
                 [("spacing", "%s | %s gap %dpx" % (t["a"][:34], t["b"][:34], t["gap"])) for t in r["tight"]] +
                 [("overflow", "%s ends at %dpx of %d" % (s["el"][:40], s["right"], s["vw"])) for s in r["spill"]] +
                 [("jack", "%s %r %s" % (j["el"][:40], j["text"], j["issue"])) for j in r.get("jack", [])])
        if r["pageScrolls"]:
            items.append(("overflow", "the page scrolls sideways"))
        seen, unique = set(), []
        for kind, text in items:
            if (kind, text) not in seen:
                seen.add((kind, text)); unique.append((kind, text))
        problems += len(unique)
        print("%s: %s" % (label.upper(), "clean" if not unique else "%d finding%s" % (len(unique), "" if len(unique) == 1 else "s")))
        for kind, text in unique[:14]:
            print("  %-8s %s" % (kind, text))
        if len(unique) > 14:
            print("  ... and %d more" % (len(unique) - 14))

    print("\npage errors: %s" % (errors or "none"))
    print("%d finding%s" % (problems, "" if problems == 1 else "s"))
    return 1 if problems or errors else 0


if __name__ == "__main__":
    sys.exit(main())
