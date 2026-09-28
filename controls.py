"""
What goes on the EQ and Effects tabs.

Rather than hard-coding parameter ids (they differ between guitarix builds),
each group names a plugin prefix. At startup the app pulls the engine's real
parameter list and keeps every parameter whose id starts with that prefix,
using the engine's own name, range and step. If a plugin isn't in your rack
the group simply doesn't render.

To see what your engine actually offers:  python3 dump_params.py

`order` is optional. Parameters listed there float to the top of the group in
that order; anything else follows alphabetically. An entry can be a full id
or just its last part ("fuzz" matches amp.fuzz). `hide` drops parameters you
don't want on screen.

`layout` picks how the group is drawn:
    "rack"  vertical faders side by side, the way a graphic EQ looks
    "rows"  one horizontal slider per line, with the name and value above

`split` breaks one plugin into several groups. Guitarix's graphic EQ exposes
three parameters per band -- gain, Q and centre frequency -- which lands thirty
faders in a row if you take them all at face value. Each entry matches
parameter ids with a regex and becomes its own group; `"match": None` catches
whatever is left over. Mark the ones you rarely touch `"collapsed": True` and
they render folded up.

`sort: "frequency"` orders members by the frequency in their name rather than
alphabetically, so 31.25 Hz comes before 1 kHz instead of after 16 kHz.
`relabel: "band"` rewrites `Qs31_25` as `31.25 Hz`.
"""

import re

# Words that humanising alone wouldn't get right.
KNOWN_NAMES = {
    "wet_dry": "Wet / dry",
    "on_off": "On",
    "out_master": "Master",
    "out_amp": "Amp out",
}

_SWITCH_SUFFIX = re.compile(r"[_ ]?on_?of+$", re.I)     # on_off, on_of, onoff
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def humanize(name):
    """
    Turn an engine identifier into something readable, conservatively.

        reverb_on_of -> Reverb      (the switch suffix goes; it's a switch)
        RoomSize     -> Room size
        Wet_dry      -> Wet / dry
        s_h          -> s_h         (left alone: "S h" is no better)

    Anything this gets wrong can be overridden in LABELS or a group's
    `labels`, which always win.
    """
    raw = str(name or "").strip()
    if not raw:
        return raw
    if raw.lower() in KNOWN_NAMES:
        return KNOWN_NAMES[raw.lower()]

    stripped = _SWITCH_SUFFIX.sub("", raw)
    if stripped and stripped != raw:
        raw = stripped

    parts = [p for p in re.split(r"[_\s]+", _CAMEL.sub(" ", raw)) if p]
    # abbreviations built from single letters don't survive being spelled out
    if len(parts) > 1 and any(len(p) < 2 for p in parts):
        return str(name)
    text = " ".join(parts).lower()
    return text[:1].upper() + text[1:]


def band_hz(text):
    """Pull a frequency out of a parameter name. Qs31_25 -> 31.25, Freq16k -> 16000."""
    m = re.search(r"(\d+(?:[._]\d+)?)\s*(k)?$", text, re.I)
    if not m:
        return None
    value = float(m.group(1).replace("_", "."))
    return value * 1000 if m.group(2) else value


def pretty_band(text, fallback):
    hz = band_hz(text)
    if hz is None:
        return fallback
    if hz >= 1000:
        return "%g kHz" % (hz / 1000)
    return "%g Hz" % hz


EQ_GROUPS = [
    {
        "layout": "rack",
        "prefix": "tone.",
        "title": "Tone stack",
        "blurb": "Bass, middle and treble ahead of the power stage.",
        "order": ["tone.bass", "tone.middle", "tone.treble"],
    },
    {
        "layout": "rack",
        "prefix": "eqs.",
        "title": "Graphic EQ",
        "sort": "frequency",
        "relabel": "band",
        "split": [
            {"match": None,
             "title": "Graphic EQ",
             "blurb": "Cut or boost, one band at a time."},
            {"match": r"\.Qs",
             "title": "Band width",
             "blurb": "How wide each band reaches. Rarely worth touching.",
             "collapsed": True},
            {"match": r"\.Freq",
             "title": "Band centres",
             "blurb": "Where each band sits. These are the standard centres.",
             "collapsed": True},
        ],
    },
    {
        "layout": "rack",
        "prefix": "low_highpass.",
        "title": "High and low cut",
        "blurb": "Trim the mud and the fizz.",
    },
    {
        "layout": "rack",
        "prefix": "amp.",
        "title": "Amp",
        "blurb": "Levels, drive and the tone stack.",
        # Guitarix calls the tone stack model picker just "select". Its values
        # (Triple Giant, Bassman, JCM-800...) are tone stack voicings, which
        # set how Bass, Middle and Treble respond.
        "labels": {"select": "Tone stack"},
        # signal order, left to right, like the front of an amp: drive, tone,
        # then output with the master last
        "order": ["fuzz", "bass", "middle", "treble", "balance", "wet_dry",
                  "out_amp", "out_master"],
    },
]

FX_GROUPS = [
    {
        "prefix": "freeverb.",
        "title": "Reverb",
        "blurb": "Room size and mix.",
        "order": ["freeverb.RoomSize", "freeverb.damp", "freeverb.wet_dry"],
    },
    {
        "prefix": "echo.",
        "title": "Echo",
        "blurb": "Simple repeats.",
        "order": ["echo.time", "echo.percent"],
    },
    {
        "prefix": "delay.",
        "title": "Delay",
        "blurb": "Longer, tempo-ish repeats.",
        "order": ["delay.delay", "delay.gain"],
    },
    {
        "prefix": "chorus.",
        "title": "Chorus",
        "blurb": "Thickens a clean tone.",
    },
    {
        "prefix": "flanger.",
        "title": "Flanger",
        "blurb": "Sweeping comb filter.",
    },
]

# Your own names for parameters. These win over whatever the engine calls
# them. Keys are full parameter ids, so they're unambiguous: "s_h" exists
# under more than one plugin, and each one is a different parameter. For a
# label that should only apply inside one group, use that group's `labels`
# instead, which also accepts the short name ("s_h").
#
# Hover any control's name in the UI to see its id, or run
#     python3 dump_params.py <plugin>
# which also prints the engine's own description when it sends one.
LABELS = {
    # "low_highpass.s_h": "Your name here",
}

# Parameters that shouldn't get a slider: bypass switches are rendered as the
# group's on/off toggle, and these are internal plumbing.
#
# .s_h turns up on every rack unit -- the graphic EQ, the amp, the cut filter --
# as a 0/1 value. Something every unit carries is per-unit plumbing, not a
# sound control; most likely the show/hide state of the unit's panel in the
# GTK rack. It sits here with the rest of that family.
HIDE_SUFFIXES = (".on_off", ".position", ".pp", ".v", ".dsp_stage", ".s_h")


def build_groups(specs, parameters):
    """
    specs:      EQ_GROUPS or FX_GROUPS
    parameters: {id: info} from GuitarixRPC.parameter_list()

    Returns a JSON-friendly list the front end can render directly.
    """
    groups = []
    for spec in specs:
        prefix = spec["prefix"]
        hide = set(spec.get("hide", []))

        members = [
            info for pid, info in parameters.items()
            if pid.startswith(prefix)
            and pid not in hide
            and not pid.endswith(HIDE_SUFFIXES)
        ]
        if not members and prefix + "on_off" not in parameters:
            continue

        toggle = prefix + "on_off"
        toggle = toggle if toggle in parameters else None

        emitted = False
        for sub, picked in _partition(spec, members):
            if not picked:
                continue
            groups.append(_group(spec, sub, picked, toggle))
            toggle = None            # only the first group carries the switch
            emitted = True

        # nothing left to show but the on/off switch: keep the switch
        if not emitted and toggle:
            groups.append(_group(spec, {}, [], toggle))
    return groups


def _partition(spec, members):
    """Yield (sub-spec, members) pairs, one per split entry, in spec order."""
    splits = spec.get("split")
    if not splits:
        return [({}, members)]

    remaining = list(members)
    taken = {}
    for index, sub in enumerate(splits):
        if not sub.get("match"):
            continue
        pattern = re.compile(sub["match"], re.I)
        hits = [p for p in remaining if pattern.search(p["id"])]
        taken[index] = hits
        remaining = [p for p in remaining if p not in hits]

    # whatever no pattern claimed goes to the "match": None entry
    return [(sub, taken.get(i, remaining if not sub.get("match") else []))
            for i, sub in enumerate(splits)]


def _group(spec, sub, members, toggle):
    order = sub.get("order", spec.get("order", []))
    rank = {}
    for i, entry in enumerate(order):
        rank.setdefault(entry.lower(), i)

    def placed(p):
        """`order` entries match a full id or just its last part ("fuzz")."""
        pid = p["id"].lower()
        return rank.get(pid, rank.get(pid.split(".")[-1], len(order)))

    if (sub.get("sort") or spec.get("sort")) == "frequency":
        def key(p):
            hz = band_hz(p["id"])
            return (placed(p), hz if hz is not None else 1e9, p["id"])
    else:
        def key(p):
            return (placed(p), p["id"])

    members = sorted(members, key=key)

    members = [dict(p, engine_name=p["name"], name=humanize(p["name"])) for p in members]

    if (sub.get("relabel") or spec.get("relabel")) == "band":
        members = [dict(p, name=pretty_band(p["id"], p["name"])) for p in members]

    scoped = dict(spec.get("labels", {}))
    scoped.update(sub.get("labels", {}))

    def label_for(p):
        if p["id"] in LABELS:
            return LABELS[p["id"]]
        tail = p["id"].split(".")[-1]
        for key in (p["id"], tail, p["engine_name"]):
            if key in scoped:
                return scoped[key]
        return p["name"]

    members = [dict(p, name=label_for(p)) for p in members]
    members = _disambiguate(members, spec["prefix"])

    return {
        "prefix": spec["prefix"],
        "title": sub.get("title", spec["title"]),
        "blurb": sub.get("blurb", spec.get("blurb", "")),
        "layout": sub.get("layout", spec.get("layout", "rows")),
        "collapsed": bool(sub.get("collapsed")),
        "toggle": toggle,
        "controls": members,
    }


def _disambiguate(members, prefix):
    """
    Two controls in one group sharing a name -- the Amp group has two called
    "Level" -- are unusable side by side. Name each one by the part of its
    id that differs instead: "Master" and "Amp out" rather than "Level" twice.

    The distinguishing part alone, not "Level (Master)": under a narrow fader
    the bracketed form wraps to two lines, and "Level" is already implied by
    what the control does. The engine's own name stays in the tooltip.
    """
    counts = {}
    for p in members:
        counts[p["name"]] = counts.get(p["name"], 0) + 1

    out = []
    for p in members:
        if counts[p["name"]] > 1:
            rest = p["id"][len(prefix):] if p["id"].startswith(prefix) else p["id"]
            key = rest.replace(".", "_")
            hint = KNOWN_NAMES.get(key.lower()) or humanize(rest.replace(".", " "))
            if hint and hint.lower() != p["name"].lower():
                p = dict(p, name=hint)
        out.append(p)
    return out


def all_ids(groups):
    """Every parameter id used by the rendered tabs, toggles included."""
    ids = []
    for group in groups:
        if group["toggle"]:
            ids.append(group["toggle"])
        ids.extend(c["id"] for c in group["controls"])
    return ids
