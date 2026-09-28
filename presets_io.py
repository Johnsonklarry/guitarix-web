"""
Importing and exporting presets as JSON.

Everything here is a pure function over the engine's parameter list (the
flattened form GuitarixRPC.parameter_list() returns) and a dict of current
values, so it can be tested without an engine. app.py does the talking.

A presets file looks like this:

    {
      "bank": "Claude",
      "presets": [
        {
          "name": "Sultans Clean",
          "notes": "Glassy clean, short plate-ish reverb.",
          "params": { "amp.fuzz": 0.05, "freeverb.RoomSize": 0.35 }
        }
      ]
    }

Rules the importer applies:

  * "base" names one of your presets to start from ("Bank/Preset"). It's
    loaded first and the file's settings go on top, so the result is the same
    every time. It can be set for the whole file or per preset.
  * Every rack unit the preset sets anything on is switched on.
  * Every other unit is left as it is -- the cabinet simulation and anything
    else the file doesn't know about keep working. A file that really does
    describe the whole rack can say "others": "off" to switch the rest off.
  * Settings the preset leaves out, on a unit it uses, come from the base.
    With no base they go back to their defaults, where the engine says what
    the default is.
  * Unknown ids are rejected, out-of-range numbers are clamped, and both are
    reported, so nothing is quietly lost.
"""

import datetime
import difflib
import json
import math
import re

import controls

FORMAT = "gxweb-presets/1"
PARAMS_FORMAT = "gxweb-parameters/1"
MAX_BYTES = 512 * 1024
DEFAULT_BANK = "Imported"

TRUE_WORDS = {"1", "true", "on", "yes", "enabled"}
FALSE_WORDS = {"0", "false", "off", "no", "disabled"}


class ImportProblem(ValueError):
    """The file as a whole can't be used. The message says why and what to do."""


class Rejected(ValueError):
    """One value can't be used. The message is the reason."""


# ------------------------------------------------------------------ parsing

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.S)


def _extract_json(text):
    """
    Presets usually arrive pasted out of a chat, fenced in ``` and sometimes
    with a sentence either side. Take the fenced block if there is one,
    otherwise the outermost {...} or [...].
    """
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1)
    stripped = text.strip()
    if stripped[:1] in "{[":
        return stripped
    starts = [i for i in (stripped.find("{"), stripped.find("[")) if i >= 0]
    if not starts:
        return stripped
    start = min(starts)
    end = max(stripped.rfind("}"), stripped.rfind("]"))
    return stripped[start:end + 1] if end > start else stripped


def parse(text):
    """
    Returns {"bank": str|None, "others": "off"|"keep"|None, "presets": [...]},
    each preset {"name", "notes", "bank", "params"}. Raises ImportProblem.
    """
    if not text or not text.strip():
        raise ImportProblem("Nothing to import. Paste a presets file, or choose one.")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportProblem("That's too large to be a presets file.")

    try:
        data = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise ImportProblem("That isn't valid JSON: %s on line %d, column %d."
                            % (exc.msg.lower(), exc.lineno, exc.colno))

    bank, others, base = None, None, None
    if isinstance(data, list):
        raw_presets = data
    elif isinstance(data, dict) and isinstance(data.get("presets"), list):
        raw_presets = data["presets"]
        bank = data.get("bank")
        others = data.get("others")
        base = data.get("base")
    elif isinstance(data, dict) and "params" in data:
        raw_presets = [data]
        bank = data.get("bank")
    else:
        raise ImportProblem('Expected a "presets" list, or a single preset with '
                            '"name" and "params".')

    if not raw_presets:
        raise ImportProblem("The file has no presets in it.")

    presets, seen = [], set()
    for i, raw in enumerate(raw_presets, 1):
        if not isinstance(raw, dict):
            raise ImportProblem("Preset %d isn't an object with a name and params." % i)
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ImportProblem("Preset %d has no name." % i)
        if len(name) > 80:
            raise ImportProblem('"%s..." is too long for a preset name (80 characters at most).'
                                % name[:30])
        params = raw.get("params")
        if not isinstance(params, dict) or not params:
            raise ImportProblem('"%s" has no settings in it. Settings go under "params".' % name)
        if name.lower() in seen:
            raise ImportProblem('Two presets are called "%s". Saving both would keep only the '
                                'second, so rename one.' % name)
        seen.add(name.lower())
        presets.append({
            "name": name,
            "notes": str(raw.get("notes") or "").strip(),
            "bank": (str(raw["bank"]).strip() or None) if raw.get("bank") else None,
            "base": raw.get("base") if raw.get("base") else base,
            "params": params,
        })

    if others not in (None, "off", "keep"):
        others = None
    return {"bank": (str(bank).strip() or None) if bank else None,
            "others": others, "base": base or None, "presets": presets}


def resolve_base(raw, banks):
    """
    Find the base preset a file names, in {bank: [preset, ...]}. Accepts
    "Bank/Preset", {"bank": ..., "preset": ...}, or just "Preset" when only
    one bank has it. Returns (bank, preset), or None for no base. Raises
    ImportProblem when it's named but can't be found.
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        bank, name = str(raw.get("bank") or "").strip(), str(raw.get("preset") or "").strip()
        if bank in banks and name in banks[bank]:
            return bank, name
        raise ImportProblem('The base preset "%s/%s" isn\'t in your banks.' % (bank, name))

    text = str(raw).strip()
    # "Bank/Preset" -- try every split point, since either half can contain a slash
    for i, ch in enumerate(text):
        if ch == "/":
            bank, name = text[:i].strip(), text[i + 1:].strip()
            if bank in banks and name in banks[bank]:
                return bank, name
    holders = [b for b, names in banks.items() if text in names]
    if len(holders) == 1:
        return holders[0], text
    if len(holders) > 1:
        raise ImportProblem('More than one bank has a preset called "%s". Name the base as '
                            '"Bank/Preset": %s.' % (text, ", ".join("%s/%s" % (b, text) for b in holders)))
    raise ImportProblem('The base preset "%s" isn\'t in your banks.' % text)


# ------------------------------------------------------------------ units

def plumbing(pid):
    """Rack layout and UI state (.pp, .position, .s_h ...), never a sound setting."""
    return pid.endswith(controls.HIDE_SUFFIXES) and not pid.endswith(".on_off")


def units(parameters):
    """Rack units: every prefix with its own on/off switch. {prefix: switch id}."""
    return {pid[:-len("on_off")]: pid
            for pid, p in parameters.items()
            if pid.endswith(".on_off") and not p.get("non_preset")}


def unit_for(pid, unit_map):
    """The unit a parameter belongs to: the longest matching prefix."""
    best = None
    for prefix in unit_map:
        if pid.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def unit_label(prefix):
    for spec in controls.EQ_GROUPS + controls.FX_GROUPS:
        if spec["prefix"] == prefix:
            return spec["title"]
    return controls.humanize(prefix.rstrip(".").replace(".", " "))


def truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in TRUE_WORDS
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ values

def is_switch(p):
    if p.get("options"):
        return False
    if "bool" in str(p.get("type", "")).lower():
        return True
    try:
        return float(p["min"]) == 0 and float(p["max"]) == 1 and float(p.get("step") or 0) >= 1
    except (TypeError, ValueError, KeyError):
        return False


def is_whole(p):
    kind = str(p.get("type", "")).lower()
    try:
        return "int" in kind or float(p.get("step") or 0) >= 1
    except (TypeError, ValueError):
        return "int" in kind


def coerce(p, raw):
    """
    Turn a value from a presets file into one the engine accepts for p.
    Returns (value, clamp) -- clamp is (given, used) when the value was out of
    range. Choices come back as the option dict; see option_wire(). Raises
    Rejected with a reason.
    """
    options = p.get("options")
    if options:
        text = str(raw).strip().lower()
        for o in options:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                if float(raw) == float(o["value"]):
                    return o, None
            if text in (str(o["value"]).lower(), str(o.get("key") or "").lower(),
                        str(o["label"]).lower()):
                return o, None
        names = [o["label"] for o in options]
        shown = ", ".join(names[:10]) + (", ..." if len(names) > 10 else "")
        raise Rejected("isn't one of the choices: %s" % shown)

    if is_switch(p):
        if isinstance(raw, bool):
            return int(raw), None
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return int(raw), None
        text = str(raw).strip().lower()
        if text in TRUE_WORDS:
            return 1, None
        if text in FALSE_WORDS:
            return 0, None
        raise Rejected("is a switch, so it takes on or off")

    if isinstance(raw, bool):
        raise Rejected("needs a number, not true or false")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise Rejected("needs a number")
    if math.isnan(value) or math.isinf(value):
        raise Rejected("needs a finite number")

    clamp = None
    lo, hi = float(p.get("min", 0)), float(p.get("max", 1))
    if lo <= hi and not lo <= value <= hi:
        used = min(max(value, lo), hi)
        clamp, value = (value, used), used
    if is_whole(p):
        value = int(round(value))
    return value, clamp


def option_wire(option, current):
    """
    Engines report a choice either as its index or its name. Send it back in
    the same form the engine is using, going by the current value.
    """
    if isinstance(current, str) and option.get("key") is not None:
        return option["key"]
    return option["value"]


def suggest(pid, parameters):
    """The id that was probably meant, if there's a convincing one."""
    candidates = [k for k, p in parameters.items() if not p.get("non_preset")]
    close = difflib.get_close_matches(pid, candidates, n=1, cutoff=0.8)
    if close:
        return close[0]
    tail = pid.split(".")[-1].lower()
    same_tail = [k for k in candidates if k.split(".")[-1].lower() == tail]
    return same_tail[0] if len(same_tail) == 1 else None


# ------------------------------------------------------------------ planning

def ids_to_read(preset, parameters):
    """Every id plan() needs a current value for, so app.py can read them in one call."""
    umap = units(parameters)
    wanted = set(umap.values())
    used = set()
    for pid in preset["params"]:
        if pid in parameters:
            wanted.add(pid)
            u = unit_for(pid, umap)
            if u:
                used.add(u)
    for pid in parameters:
        if unit_for(pid, umap) in used:
            wanted.add(pid)
    return sorted(wanted)


def plan(preset, parameters, current, others_off=False, reset_defaults=True):
    """
    Work out exactly what importing `preset` would change.

    others_off      switch off every unit the preset doesn't use. Off by
                    default: a file can't know about everything in the rack,
                    and switching off the cabinet sim wrecks the sound.
    reset_defaults  settings it leaves out, on units it uses, go back to their
                    defaults. Pass False when a base preset was loaded first,
                    so those settings come from the base instead.

    Returns (changes, report). `changes` is {id: value}, ready for the engine.
    `report` is what the importer shows before anything is applied.
    """
    umap = units(parameters)
    changes, rejected, clamped, explicit = {}, [], [], []

    for pid, raw in preset["params"].items():
        p = parameters.get(pid)
        if p is None:
            rejected.append({"id": pid, "reason": "isn't on this engine",
                             "suggestion": suggest(pid, parameters)})
            continue
        if p.get("non_preset"):
            rejected.append({"id": pid, "reason": "isn't stored in presets"})
            continue
        if plumbing(pid):
            rejected.append({"id": pid, "reason": "is rack layout, not a sound setting"})
            continue
        try:
            value, clamp = coerce(p, raw)
        except Rejected as why:
            rejected.append({"id": pid, "reason": str(why)})
            continue
        if isinstance(value, dict):
            value = option_wire(value, current.get(pid))
        if clamp:
            clamped.append({"id": pid, "given": clamp[0], "used": clamp[1],
                            "range": [p.get("min"), p.get("max")]})
        changes[pid] = value
        explicit.append(pid)

    used = {unit_for(pid, umap) for pid in explicit} - {None}

    # every unit the preset uses is on, unless it says otherwise
    switched_on = []
    for prefix in sorted(used):
        switch = umap[prefix]
        if switch not in changes:
            changes[switch] = 1
        if truthy(changes[switch]) and not truthy(current.get(switch)):
            switched_on.append(unit_label(prefix))

    # every unit it doesn't use is off -- only when the file asks for that
    switched_off = []
    if others_off:
        for prefix, switch in sorted(umap.items()):
            if prefix in used or switch in changes:
                continue
            if current.get(switch) is None or truthy(current.get(switch)):
                changes[switch] = 0
                switched_off.append(unit_label(prefix))

    # settings it leaves out, on units it uses, go back to their defaults --
    # unless a base preset is supplying them
    reset, inherited = 0, 0
    for pid, p in (parameters.items() if reset_defaults else ()):
        if pid in changes or p.get("non_preset") or plumbing(pid) or pid.endswith(".on_off"):
            continue
        if unit_for(pid, umap) not in used:
            continue
        default = p.get("default")
        if default is None:
            inherited += 1
            continue
        if p.get("options"):
            match = next((o for o in p["options"]
                          if str(o["value"]) == str(default)
                          or str(o.get("key")) == str(default)), None)
            if match is None:
                inherited += 1
                continue
            default = option_wire(match, current.get(pid))
        changes[pid] = default
        reset += 1

    differs = sum(1 for pid, v in changes.items() if str(current.get(pid)) != str(v))

    return changes, {
        "name": preset["name"],
        "notes": preset.get("notes", ""),
        "set": len(explicit),
        "switched_on": switched_on,
        "switched_off": switched_off,
        "reset": reset,
        "inherited": inherited,
        "rejected": rejected,
        "clamped": clamped,
        "changes": len(changes),
        "differs": differs,
    }


# ------------------------------------------------------------------ export

def _clean(value):
    if isinstance(value, float):
        return float("%.6g" % value)
    return value


def export_ids(parameters):
    """Ids export_live() needs current values for."""
    umap = units(parameters)
    return sorted(pid for pid, p in parameters.items()
                  if not p.get("non_preset") and not plumbing(pid)
                  and unit_for(pid, umap) is not None)


def export_live(parameters, current, bank=None, name=None):
    """The sound as it is right now, in the import format: every unit that's on."""
    umap = units(parameters)
    on = {prefix for prefix, switch in umap.items() if truthy(current.get(switch))}
    params = {}
    for pid in export_ids(parameters):
        if unit_for(pid, umap) not in on or pid not in current:
            continue
        p, value = parameters[pid], current[pid]
        if pid.endswith(".on_off"):
            params[pid] = 1
        elif p.get("options"):
            match = next((o for o in p["options"]
                          if str(o["value"]) == str(value) or str(o.get("key")) == str(value)), None)
            params[pid] = match["label"] if match else value
        else:
            params[pid] = _clean(value)
    return {
        "format": FORMAT,
        "exported": datetime.datetime.now().isoformat(timespec="seconds"),
        "bank": bank or DEFAULT_BANK,
        "presets": [{"name": name or "Untitled", "notes": "", "params": params}],
    }


GUIDE = {
    "what_this_is": "The parameters this guitarix engine has. Use it to write presets "
                    "that import cleanly into the web app.",
    "file_format": 'JSON: {"bank": "<bank name>", "base": "<Bank/Preset to start from>", '
                   '"presets": [{"name": "...", "notes": "...", '
                   '"params": {"<parameter id>": <value>}}]}',
    "rules": [
        "Use parameter ids exactly as they appear under \"parameters\". Anything else is rejected.",
        "Numbers must sit between min and max; values outside are clamped. "
        "Switches take 0 or 1. Choices take one of the listed choices, by name.",
        "Pick a \"base\" from \"banks\" — the existing preset closest to the sound you "
        "want — and set only what differs from it. The base is loaded first. It can be "
        "given for the whole file or per preset, as \"Bank/Preset\".",
        "Every unit a preset sets anything on is switched on. Other units are left as the "
        "base has them. Only add \"others\": \"off\" if the file describes the whole rack.",
        "Without a base, settings a preset leaves out on a unit it uses go back to defaults.",
        "Describe the intended sound in one line under \"notes\".",
    ],
}


def export_parameters(parameters, banks=None):
    """
    The engine's parameter list in a form that's easy to write presets against,
    plus the existing banks, so a file can name one of them as its base.
    """
    umap = units(parameters)
    unit_list = [{"prefix": prefix, "name": unit_label(prefix), "switch": switch}
                 for prefix, switch in sorted(umap.items())]
    out = {}
    for pid, p in sorted(parameters.items()):
        if p.get("non_preset") or plumbing(pid) or pid.endswith(".v"):
            continue
        entry = {"name": controls.humanize(p.get("name") or pid.split(".")[-1])}
        if p.get("desc"):
            entry["description"] = p["desc"]
        if p.get("options"):
            entry["kind"] = "choice"
            entry["choices"] = [o["label"] for o in p["options"]]
        elif is_switch(p):
            entry["kind"] = "switch"
        else:
            entry["kind"] = "number"
            entry["min"], entry["max"] = _clean(p.get("min")), _clean(p.get("max"))
            if p.get("step"):
                entry["step"] = _clean(p.get("step"))
        if p.get("default") is not None:
            entry["default"] = _clean(p["default"])
        unit = unit_for(pid, umap)
        if unit:
            entry["unit"] = unit
        out[pid] = entry
    return {
        "format": PARAMS_FORMAT,
        "exported": datetime.datetime.now().isoformat(timespec="seconds"),
        "guide": GUIDE,
        "banks": {name: list(presets) for name, presets in (banks or {}).items()},
        "units": unit_list,
        "parameters": out,
    }
