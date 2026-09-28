# Guitarix web control surface

Three tabs — presets, EQ, effects — over one persistent JSON-RPC socket to the
guitarix engine, with changes pushed to every open browser.

```
app.py              Flask + Socket.IO, routes, state, fan-out
gx_rpc.py           the socket: reader thread, calls, notifications, reconnect
presets_io.py       preset import/export: parsing, checking, planning
recorder.py         ffmpeg recording and the take library
player.py           mpv playback into JACK, controlled while it plays
reamp.py            playing dry takes back through the amp, live
backing.py          backing tracks
jackutil.py         JACK port discovery and wiring
monitor.py          the rig as a live MP3 stream, for the Listen button
controls.py         which plugins appear on the EQ and Effects tabs
dump_params.py      prints the engine's real parameter ids and ranges
probe_rpc.py        finds which preset RPC methods your build supports
build_preview.py    rebuilds preview.html (the demo, in one file)
check_js.js         runs app.js headless and pushes fake events through it
tests/              lint.py, run_server_tests.py, ui_audit.py, and the fakes they use
requirements.txt    Python packages; system ones are listed inside
gxweb.service       run at boot as a systemd user service
gxweb-demo.service  the demo-only copy, safe to port-forward
static/demo.js      the demo's stand-in for the Pi
static/demo/        the demo's synthesized guitar clip
preview.html        the whole UI with fake data, openable without Flask
templates/index.html
static/style.css
static/app.js
static/manifest.webmanifest   home-screen app settings
static/icons/                 home-screen icons
```

Four sections: presets, EQ, effects, record.


## Quick start

```bash
sudo apt install ffmpeg mpv jackd2
pip install -r requirements.txt
guitarix -N -p 7000        # or with its window: guitarix -p 7000
python3 app.py             # then open http://<pi>:5000
```

To have it start at boot, see `gxweb.service` -- it's a user service, with the
steps in its header. Anything below that isn't working is most likely a port
name: see **Ports to check on the Pi**.

## Checking it works

```bash
python3 tests/lint.py               # undefined names, stale references between files
python3 tests/run_server_tests.py   # the server, against fake guitarix / JACK / ffmpeg / mpv
node check_js.js                    # the page, against a fake browser
```

And after any change to the look (needs Playwright, so on a desktop, not the Pi):

```bash
python3 build_preview.py && python3 tests/ui_audit.py
```

That drives the demo in a real browser -- desktop and phone, Live mode, the
dialogs -- and measures: whether every kind of control visibly responds to
hover, keyboard focus and press, and animates between them; text contrast
against what's actually behind it (WCAG AA); tap targets of at least 24 px;
controls sitting too close together; and anything spilling off a phone
screen. It currently finds nothing.

All three are safe to run on the Pi alongside the real thing: the server
tests start their own fake guitarix on a spare port and never touch your
recordings, banks or JACK wiring. The fakes refuse whatever the real tools
refuse, so a pass means something. What they can't tell you is whether your
port names are right -- that takes one real reamp and one real Listen.

## Live mode

**Live** in the header covers the page with big targets for playing: the
preset name large enough to read from a mic stand, ‹ › to step through the
bank (wrapping at the ends), the bank's presets as tiles, and four pads --
**Record** (with the attempt number, and the clock while recording),
**Loop last** (the newest take on repeat, to play over), **Backing** (the
track picked underneath), and **Listen**.

Foot pedals: Bluetooth page-turner pedals send arrow or page keys, so ← → or
Page Up / Page Down change presets, Space or Enter records, B toggles the
backing track, L toggles the loop, Escape leaves. A held key doesn't repeat.

Live mode is remembered across reloads. It asks the screen to stay on, but
browsers only allow that over HTTPS -- over plain http, your phone's own sleep
setting decides.
## Demo mode

**Demo** in the footer turns the page into a pretend amp for showing people:
three banks of presets that visibly move the faders when you switch, takes
with dry tracks and settings, backing tracks, and a synthesized guitar clip
(`static/demo/jam.mp3`) that Listen and Play actually play. Everything works
-- recording ticks and makes a new "Attempt N", reamps and exports run,
imports audition, Live mode and the pedal keys all respond.

It runs entirely in the browser. The page loads a stand-in (`static/demo.js`)
instead of the real connection, which is never opened -- so nothing done in
the demo can reach guitarix, the recorder, or a file on the Pi. **Exit demo**
goes back. Or share a link straight into it: `http://<pi>:5000/?demo`.

`preview.html` is the same demo in one file, openable from disk with no Pi at
all.

### Showing it off over the internet

**Don't forward the real app.** It has no login: anyone who reaches it can
delete takes, overwrite presets, upload files and disconnect your guitar with
a reamp. The Demo button doesn't protect it -- a visitor can simply not press
it.

Forward a **demo-only copy** instead: the same app started with
`GX_DEMO_ONLY=1`, on its own port. It serves nothing but the demo, refuses
every request that could touch the rig (recordings, uploads, the audio
stream, the parameter list, the live connection), and never contacts
guitarix. The server tests check each of those refusals.

```bash
GX_DEMO_ONLY=1 GX_WEB_PORT=5080 python3 app.py      # or: gxweb-demo.service
```

Forward 5080 for friends; keep 5000 on your LAN. To use the *real* app away
from home, put it behind a login -- an Access List in Nginx Proxy Manager --
or reach your LAN through a VPN such as Tailscale or WireGuard, rather than
forwarding it.

## Two layouts

Below 1000px the UI is a stompbox — one section at a time behind tabs, big
targets, reachable with a thumb. That's the phone-on-the-music-stand case and
it's unchanged.

At 1000px and up the tabs disappear and the cabinet spreads into three
columns: presets down the left, the controls in the middle, takes down the
right. Presets and takes are always visible, so you can punch a preset or
start a take without leaving the controls. The middle column carries its own
two tabs for EQ and effects -- they had a row each at first, and two scrollers
splitting the height meant neither had enough.

Both layouts are the same DOM and the same JavaScript. `applyLayout()` in
`app.js` watches a `matchMedia` query and either pages the panes or shows them
all; the CSS from `@media (min-width: 1000px)` down does the rearranging. The
breakpoint is in two places -- that query in `app.js` and the media queries at
the bottom of `style.css` -- so change both if you move it.

There are two extra breakpoints inside the wide layout: under 800px tall the
EQ faders shorten so the effects below them aren't squeezed, and over 1500px
wide the columns get more room rather than leaving margins.

## How the sync works

The engine broadcasts state changes to every connected client **except** the one
that caused them. So:

- a change made in the GTK UI, by MIDI, or by a preset load arrives as a `set`
  notification, gets cached, and is pushed to the browsers;
- a change made from a browser is written to the engine and then fanned out to
  the *other* browsers by `app.py`, because the engine won't echo it back.

Parameter updates are coalesced on a 50 ms timer, so dragging a slider sends
roughly 20 messages a second rather than one per pixel. Incoming values never
overwrite a control the user is currently holding.

Parameters ending in `.v` are output meters. They fire constantly and are
dropped in `gx_rpc.py`.

## Taming a plugin with too many parameters

Guitarix's graphic EQ exposes three parameters per band -- gain, Q and centre
frequency -- so taking them at face value puts thirty faders in a row, sorted
alphabetically, which is how `Qs125` ends up next to `Qs16k` and before
`Qs1k`. Three keys in `controls.py` deal with that:

- `split` breaks one plugin into several groups by matching parameter ids with
  a regex. `"match": None` catches the leftovers. Entries marked
  `"collapsed": True` render folded up, one line each, with a Show button.
- `sort: "frequency"` orders members by the frequency in their name instead of
  alphabetically, so 31.25 Hz comes before 1 kHz.
- `relabel: "band"` rewrites `Qs31_25` as `31.25 Hz`.

The shipped `eqs` spec uses all three: ten gain faders in frequency order, with
Q and the band centres folded away underneath. The same approach works for any
plugin that exposes more than you want to look at.

## When the engine goes quiet mid-operation

Earlier versions hung up on the engine whenever it sent a notification with
no parameters, on the assumption -- borrowed from the maintainer's demo script
-- that an empty notification means guitarix is quitting. It doesn't: the
engine also sends them for ordinary events like the preset list changing. The
app now treats them as "something changed, re-read the banks", and relies on
the socket actually closing to notice a real shutdown.

While either link is down, presets, EQ and effects are locked with the
`inert` attribute -- greyed out, and unreachable by mouse or keyboard. The
recorder and the tabs stay usable: one doesn't go through guitarix, the
other only changes the view.

Static files are served with a modification-time stamp (`asset()` in
`app.py`), so a browser can't keep running an old `app.js` after a deploy.

The banner also says which link dropped. "Lost the connection to the web app"
is the browser to the Pi. "Can't reach the guitarix engine" is the Pi to
guitarix. Different causes, different fixes.

## Cryptic parameter names

Some guitarix parameters have short internal names (`s_h`, `pp`, `lhc`) and
no longer name attached. Two things help:

- `python3 dump_params.py <plugin>` now prints the engine's own description
  under each parameter when it sends one. That's the same text guitarix shows
  as a tooltip, and it's also shown on hover in this UI (dotted underline).
- Hover any control's name to see its parameter id. A dotted underline means
  the engine sent a description too, and it's in the same tooltip.
- `LABELS` in `controls.py` renames a parameter by full id, and wins over
  everything. A group's own `labels` also accepts short names (`"s_h"`) and
  only applies inside that group -- which matters, because the same short
  name can belong to unrelated parameters in different plugins.
- `python3 dump_params.py --raw <text>` prints descriptors exactly as the
  engine sent them. That's the thing to look at when a control renders wrong.

`.s_h` is hidden along with `.on_off`, `.pp` and `.position`. It turns up on
every rack unit as a 0/1 value, which makes it per-unit plumbing rather than a
sound control -- most likely the show/hide state of the unit's panel in the
GTK rack. A unit whose only remaining parameter is its bypass keeps a
header with just the on/off switch.

Engine names are humanised conservatively: `reverb_on_of` becomes `Reverb`,
`RoomSize` becomes `Room size`, `Wet_dry` becomes `Wet / dry`. Abbreviations
made of single letters (`s_h`) are left alone, since spelling them out
doesn't help. Two controls in one group with the same name are named by the
part of their id that differs instead: the amp's two `Level`s become `Master`
and `Amp out`. The engine's own name stays in the hover text.

Readouts take their decimal places from the control's step, so a fader that
moves in 0.1s always shows one decimal and neighbouring faders line up.
Cut/boost controls are signed: `+3.5`, `0.0`, `-2.0`.

Parameters that are really on/off (`BoolParameter`, or 0 to 1 in whole steps)
render as switches rather than sliders, and enums with named values render as
a dropdown, in a strip beneath the group's sliders.

## Adding controls

`controls.py` lists plugin prefixes, not individual parameter ids. On connect
the app reads the engine's parameter list and builds each group from every
parameter under that prefix, using the engine's own names, ranges and steps. A
`<prefix>on_off` parameter becomes the group's bypass switch.

To see what your build actually exposes:

```bash
python3 dump_params.py            # everything
python3 dump_params.py freeverb   # just one plugin
```

or hit `/api/parameters` in a browser. Groups whose plugin isn't in your rack
are skipped, so the starter list in `controls.py` is safe to leave as is.

## Saving and creating presets

`Save` overwrites the loaded preset with the live settings. `Save as` stores
them under a new name, in any bank. `Organise` puts rename and delete buttons
on each preset tile, the way guitarix's own organise mode works. `+ bank`
creates a bank.

Organise mode doesn't rearrange the preset tiles. Tap one to pick it --
it gets an amber outline and nothing loads -- and the organise bar above acts
on the pick: rename or move, delete, and bank creation and deletion.

`Delete bank` in the organise bar removes the selected bank with everything
in it. Three guards: it won't touch the last
remaining bank, it won't touch the bank you're currently playing from, and the
bank name has to be typed out. Guitarix won't delete factory or read-only
banks either -- that comes back as a message saying the bank is still there.

Rename also offers a bank, and picking a different one moves the preset. If
your build has no real move method, it's composed from what does exist: load
the preset, save it into the destination, confirm the copy is there, and only
then delete the original. Loading it is a visible side effect -- the amp
switches to that sound while the move happens -- but reading a stored preset's
values over RPC isn't otherwise possible. If the copy doesn't land the
original is left alone.

**Read this before trusting it.** Guitarix publishes no list of RPC method
names, and the maintainer has said in the issue tracker that no general
documentation exists. `get`, `set`, `banks`, `setpreset`, `listen` and
`parameterlist` are confirmed from the maintainer's own example scripts. The
preset *management* names in `PRESET_METHODS` at the top of `gx_rpc.py` are
educated guesses. Find out what your build really answers to:

```bash
cp -r ~/.config/guitarix/banks ~/banks-backup
python3 probe_rpc.py
```

It sends each candidate with no arguments and reads the error back: a method
the engine doesn't know returns JSON-RPC -32601, one it does know complains
about the arguments instead. It prints a `PRESET_METHODS` block to paste into
`gx_rpc.py`.

Until then nothing fails silently — a missing method shows up as a message in
the UI naming the method it tried. Setting an entry to `None` hides that
button entirely.

## Importing presets

**Import** in the presets toolbar takes a preset file -- pasted, opened, or
straight out of a chat reply, code fences and all -- and shows what each
preset would do before anything changes: what it sets, anything rejected
(with a suggestion when an id is a typo), anything clamped into range.

1. **Download the parameter list** from the importer. It carries your banks,
   every parameter with its range, and instructions for writing presets, so it
   works on its own in any conversation.
2. Send it to Claude with what you want to hear.
3. Paste the reply into the importer. **Audition** one -- it plays live, and
   you can tweak it -- then **Save** or **Discard**. Discard puts back exactly
   what you had, unsaved tweaks included. **Save all** skips auditioning.

The rules, which the parameter list also spells out:

- A preset can name a **base** (`"Bank/Preset"`). It loads first and the file's
  settings go on top, so the result is the same every time.
- Every unit a preset sets anything on is switched on. **Other units are left
  alone**, so your cabinet and anything else the file doesn't mention keep
  working. `"others": "off"` switches the rest off, for files that describe the
  whole rack.
- With no base, settings a preset leaves out on a unit it uses go back to
  their defaults.

**Export the loaded preset** gives Claude a reference: "like this, but darker".

## Recording, reamping and backing tracks

Three tools do the audio work, and each does only what it's good at:

- **ffmpeg** records. It reads from JACK. It can't play into JACK -- `ffmpeg
  -devices` lists `jack` as input-only -- which is why playback isn't its job.
- **mpv** plays. It has a real JACK output and a control socket, so pause,
  seek, volume and looping all work while a file plays. Install it and check
  it has JACK:

  ```bash
  sudo apt install mpv
  mpv --ao=help | grep jack      # must list jack
  ```

- **jack_lsp / jack_connect / jack_disconnect** do the wiring (`jackutil.py`).

### Takes

New takes are named **Attempt N**, one past the highest attempt in the
folder, and the Record tab's header shows the next number, how many takes
there are, how much is recorded, and how much disk is left -- it turns amber
under a gigabyte.

Tick **+ dry** and the take is also recorded unprocessed, straight off the
interface, as `Attempt N (dry).wav`. Every take also gets `Attempt N.json`:
the amp settings at the moment you pressed Record, in the importer's format,
so **Settings** on a take loads them for review like any other import. Wet,
dry and settings travel together -- one row in the list, renamed and deleted
together.

### Reamping

**Reamp** on a take with a dry recording plays it into guitarix's input in
real time. You hear it through the amp, and anything you change -- presets,
faders, effects -- you hear straight away. The **Amp** switch sends the same
playback straight to your outputs instead, so you can flip between the raw
take and the amped one mid-phrase.

**Record a pass** starts from the top and captures it as `Attempt N
(render)`, through whatever is dialed in. It runs start to finish: the amp
switch, pause and loop are locked until it ends, since a bypassed amp would
record silence.

While a reamp runs, your guitar is disconnected from the amp -- guitarix has
one input. Its wiring is saved first and restored when the reamp ends: when
the file finishes, when you stop it, or when anything fails.

### Backing tracks

**Add tracks** uploads audio files (wav, mp3, flac, ogg, m4a, opus) to
`~/backing`. A backing track plays to your outputs alongside the amp, never
into it, with its own volume, pause and loop. **Put the backing into takes
too** feeds it to the recorder as well; leave it off for anything you might
reamp later, so the take is your part alone.

**Loop** on a take plays that take on repeat as a backing track -- record a
phrase, loop it, play over it. It's not sample-accurate the way a hardware
looper is: the loop is as long as the file, and a take starts a moment after
you press Record while ffmpeg connects. For tight looping, a dedicated JACK
looper like SooperLooper is the right tool, and could be driven from here.

### Listening through the page

**Listen** in the header plays the rig in the browser: the amp, any backing
track, and a reamp with the amp switched off -- whatever your outputs are
playing. It's an MP3 stream into a plain `<audio>` element, like an internet
radio station, so:

- it works over plain http, in any browser, and keeps playing with a phone's
  screen locked;
- it runs a second or two behind, mostly the browser's own buffering. If it
  drifts more than three seconds behind, it jumps forward to the live edge;
- if the stream drops, it reconnects on its own a few times before giving up.

The encoder only runs while someone is listening, and runs at a lower
priority than guitarix (`nice`), so it can't take CPU from the amp. Listening
taps each source in JACK without disturbing its existing connections.
`libmp3lame` has to be in your ffmpeg: `ffmpeg -encoders | grep mp3`.

Behind a reverse proxy such as Nginx Proxy Manager, the stream sends
`X-Accel-Buffering: no` so nginx doesn't hold it back to fill a buffer.
(HTTPS through NPM doesn't need port forwarding, by the way: a Let's Encrypt
certificate from a DNS challenge works for a LAN-only hostname. It isn't
needed for any of this.)

### Exporting

**Export...** under a take's More renders it to a new file, in the background.
Choose what it should sound like -- as it sounds now, as it was recorded (from
the take's settings file), or any saved preset -- and WAV or MP3. It lands in
the take list as `Attempt N (export ...)`.

guitarix has no offline mode, so an export plays through the live amp in real
time: the rig is busy for the length of the take, with your guitar
disconnected, and the reamp bar shows its progress with a Cancel. Afterwards
everything is put back -- the loaded preset and every setting, unsaved tweaks
included. A cancelled export keeps nothing.

Rendering without tying up the rig would need a second, hidden guitarix on a
second JACK server. Possible, but a much bigger job, and it costs memory.

### Ports to check on the Pi

These are best guesses from typical JACK naming. Confirm with `jack_lsp -p`:

| Setting | File | Default | Is |
|---|---|---|---|
| `SOURCE_CLIENT` | recorder.py | `gx_head_amp` | guitarix, for the wet signal |
| `DRY_CLIENT` | recorder.py | `system` | your interface's capture ports |
| `OUTPUT_CLIENT` | reamp.py | `system` | your interface's playback ports |

In JACK, an interface's *capture* ports are output-typed -- they feed signal
into the graph -- which is why the dry signal can be tapped there. guitarix's
input can't be tapped: an input port has nothing to read from.

## On a phone: installing it as an app

Opened in a phone browser, the page sits between the browser's address bar
and toolbar. They're tinted dark to match now, but they're still there --
mobile browsers only tuck them away when the page itself scrolls, and this
layout scrolls inside its panels instead.

Added to the home screen it launches as an app with no browser bars at all:

- **iPhone:** in Safari, Share -> Add to Home Screen.
- **Android:** in Chrome, menu -> Add to Home screen. Chrome only launches it
  without its bars when the site is served over HTTPS. Over plain http you get
  a shortcut that opens an ordinary tab. Putting the app behind a reverse
  proxy with a certificate fixes that.

The manifest is `static/manifest.webmanifest`, served from `/`. The icons in
`static/icons/` are the header's pilot lamp.

## After changing app.js

```bash
node check_js.js
```

It loads the front end against a stub DOM and pushes a snapshot, parameter
updates, preset changes and recording events through it. `node --check` only
parses -- it can't see a variable read before its `let` runs, and that mistake
leaves the page frozen on its template defaults with one line in the console.

## Empty presets, empty EQ, empty effects

That combination means one thing: the app isn't talking to the guitarix
engine. The recording tab keeps working because it goes to ffmpeg, not to
guitarix, which makes the failure easy to misread as "my banks are gone".

A red lamp in the header and a banner across the top say so now. To check the
engine end:

```bash
pgrep -a guitarix                      # running, and started with -p 7000?
ss -ltn | grep 7000                    # is anything listening?
python3 dump_params.py                 # can this machine reach it?
```

`GX_HOST` and `GX_PORT` at the top of `app.py` are where it tries. The app
reconnects on its own once the engine answers -- no restart needed.

## Loose ends

- `SECRET_KEY` in `app.py` is a placeholder.
- `socket.io.min.js` loads from a CDN. Vendor it into `static/` if you want the
  page to work with no internet on the phone.
- Werkzeug's dev server is fine on a LAN; put it behind gunicorn with the
  `geventwebsocket` worker if you want it hardened.
- There's no authentication. Anyone who can reach the port can delete your
  takes and overwrite your presets. Fine on a home LAN, not on anything
  port-forwarded.
- Recordings are never cleaned up automatically. A long session will fill the
  card.
