"""Read-only Joplin setlist / practice notebook adapter.

The adapter reads titles, bodies and tags from two configured Joplin
notebooks: a *setlist* notebook and a *practice* notebook.  It never writes
to Joplin and it exposes no note-edit or playback affordances.

Callers get two guarantees:

* notebook restriction - notes stored in any other notebook are dropped;
* graceful degradation - when Joplin cannot be reached the adapter returns a
  view whose ``available`` flag is ``False`` instead of raising, so whatever
  renders the view keeps working.

Reads are cached for a bounded interval (``DEFAULT_CACHE_TTL`` seconds) so a
burst of page requests costs a single call into Joplin.
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

__all__ = [
    "DEFAULT_CACHE_TTL",
    "JoplinAdapter",
    "JoplinUnavailable",
    "JoplinView",
    "Note",
    "Notebook",
    "UNAVAILABLE_NOTICE",
    "render_view",
]

DEFAULT_CACHE_TTL = 30.0
SETLIST_ROLE = "setlist"
PRACTICE_ROLE = "practice"
UNAVAILABLE_NOTICE = "Joplin is unavailable; no notes are shown."


class JoplinUnavailable(RuntimeError):
    """Raised by a data source when the Joplin service cannot be reached."""


def _text(value: Any) -> str:
    """Return *value* as text, mapping ``None`` to an empty string."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _escape(value: Any) -> str:
    """Return an HTML-safe rendering of *value*, quotes included."""
    return html.escape(_text(value), quote=True)


def _key(value: Any) -> str:
    """Normalise a notebook name so configured names compare reliably."""
    return " ".join(_text(value).split()).casefold()


def _tag_tuple(raw: Any) -> tuple[str, ...]:
    """Return unique, escaped tags from a raw tag payload."""
    if raw is None:
        return ()
    parts: list[Any]
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set, frozenset)):
        parts = list(raw)
    else:
        parts = [raw]
    tags: list[str] = []
    for part in parts:
        tag = _escape(part).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


@dataclass(frozen=True)
class Note:
    """A single read-only note; ``title``, ``body`` and ``tags`` are escaped."""

    id: str
    title: str
    notebook: str
    body: str
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """Return a JSON-friendly copy of this note."""
        return {
            "id": self.id,
            "title": self.title,
            "notebook": self.notebook,
            "body": self.body,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class Notebook:
    """A configured notebook with the notes and tags read from it."""

    name: str
    role: str
    notes: tuple[Note, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoplinView:
    """Result of a read; ``available`` is False when Joplin is unreachable."""

    available: bool
    notebooks: tuple[Notebook, ...] = ()
    reason: str = ""
    fetched_at: float = 0.0

    def notes(self) -> tuple[Note, ...]:
        """Return every note across the configured notebooks."""
        return tuple(note for book in self.notebooks for note in book.notes)

    def tags(self) -> tuple[str, ...]:
        """Return every tag across the configured notebooks."""
        found: list[str] = []
        for book in self.notebooks:
            for tag in book.tags:
                if tag not in found:
                    found.append(tag)
        return tuple(found)


class JoplinAdapter:
    """Read-only adapter over a Joplin data source.

    ``fetch`` is a zero-argument callable returning an iterable of raw note
    mappings using the keys ``id``, ``title``, ``notebook``, ``body`` and
    ``tags``.  It may raise :class:`JoplinUnavailable`, or any other
    exception, when the service cannot be reached; the adapter turns that
    into an unavailable view rather than propagating the failure.
    """

    def __init__(
        self,
        fetch: Callable[[], Any],
        setlist_notebook: str,
        practice_notebook: str,
        *,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._configured = (
            (SETLIST_ROLE, _text(setlist_notebook)),
            (PRACTICE_ROLE, _text(practice_notebook)),
        )
        self._cache_ttl = float(cache_ttl)
        self._clock = clock
        self._cache: JoplinView | None = None
        self._cache_at: float | None = None

    @property
    def notebooks(self) -> tuple[str, ...]:
        """Names of the configured notebooks, setlist first."""
        return tuple(name for _role, name in self._configured)

    def invalidate(self) -> None:
        """Drop the cached view so the next read calls Joplin again."""
        self._cache = None
        self._cache_at = None

    def load(self, *, force: bool = False) -> JoplinView:
        """Return the current view, reading from Joplin when the cache is stale."""
        now = self._clock()
        if (
            not force
            and self._cache is not None
            and self._cache_at is not None
            and now - self._cache_at < self._cache_ttl
        ):
            return self._cache
        view = self._read(now)
        self._cache = view
        self._cache_at = now
        return view

    def _read(self, now: float) -> JoplinView:
        wanted: dict[str, tuple[str, str]] = {}
        for role, name in self._configured:
            key = _key(name)
            if key:
                wanted.setdefault(key, (role, name))
        try:
            raw_notes = self._fetch()
        except Exception as exc:  # unreachable service, bad payload, timeout
            return JoplinView(
                available=False,
                reason=f"{type(exc).__name__}: {exc}",
                fetched_at=now,
            )
        buckets: dict[str, list[Note]] = {key: [] for key in wanted}
        for raw in raw_notes or ():
            if not isinstance(raw, Mapping):
                continue
            key = _key(raw.get("notebook", raw.get("notebook_name")))
            if key not in wanted:
                continue
            configured_name = wanted[key][1]
            buckets[key].append(
                Note(
                    id=_escape(raw.get("id")),
                    title=_escape(raw.get("title")),
                    notebook=_escape(configured_name),
                    body=_escape(raw.get("body")),
                    tags=_tag_tuple(raw.get("tags")),
                )
            )
        books: list[Notebook] = []
        for name_key, (role, name) in wanted.items():
            notes = tuple(buckets[name_key])
            tags: list[str] = []
            for note in notes:
                for tag in note.tags:
                    if tag not in tags:
                        tags.append(tag)
            books.append(
                Notebook(name=_escape(name), role=role, notes=notes, tags=tuple(tags))
            )
        return JoplinView(available=True, notebooks=tuple(books), fetched_at=now)


def render_view(view: JoplinView) -> str:
    """Render *view* as a read-only HTML fragment.

    The fragment is plain escaped markup: it carries no note-edit controls
    and no playback controls, and an unavailable view still renders an
    ordinary section so the caller's page keeps working.
    """
    if not view.available:
        notice = f'<p class="joplin-notice">{_escape(UNAVAILABLE_NOTICE)}</p>'
        reason = (
            f'<p class="joplin-reason">{_escape(view.reason)}</p>'
            if view.reason
            else ""
        )
        return (
            '<section class="joplin joplin-unavailable" data-joplin="unavailable">'
            f"{notice}{reason}</section>"
        )
    parts = ['<section class="joplin" data-joplin="available">']
    for book in view.notebooks:
        parts.append(
            f'<div class="joplin-notebook" data-notebook="{book.name}"'
            f' data-role="{book.role}">'
        )
        parts.append(f"<h3>{book.name}</h3>")
        if book.tags:
            tags = "".join(
                f'<span class="joplin-tag">{tag}</span>' for tag in book.tags
            )
            parts.append(f'<p class="joplin-tags">{tags}</p>')
        if not book.notes:
            parts.append('<p class="joplin-empty">No notes.</p>')
        for note in book.notes:
            parts.append(
                f'<article class="joplin-note" data-note-id="{note.id}"'
                f' id="joplin-note-{note.id}">'
            )
            parts.append(f"<h4>{note.title}</h4>")
            if note.tags:
                tags = "".join(
                    f'<span class="joplin-tag">{tag}</span>' for tag in note.tags
                )
                parts.append(f'<p class="joplin-tags">{tags}</p>')
            parts.append(f'<div class="joplin-body">{note.body}</div>')
            parts.append("</article>")
        parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)
