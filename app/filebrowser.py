"""
Authorized take listing, upload, and download through the canonical connector
and proxy (issue #116, part 2).

The canonical connector is the single object that knows how to reach the
recorder's storage; the proxy is the single object that knows how to reach the
connector. Everything in this module goes through them, so there is exactly one
place where authorization is decided and exactly one place where the root
containment check lives.

Authorization model: a request is authorized when it carries a token that the
connector accepts. The connector is the authority -- this module never decides
on its own whether a caller may see or touch a take, it only asks. That keeps
the listing, the upload, and the download from drifting apart, which is how the
first pass at this went wrong: the listing was checked and the download wasn't.

Root containment: every name a client supplies is resolved against the
connector's root and rejected if it lands outside it. The connector does that
resolution (see `resolve`), and this module refuses to touch a path the
connector didn't hand back. A name like "../../etc/passwd" therefore fails at
the connector, not at a regex here.
"""

import os

from flask import Blueprint, jsonify, request, send_file

# The canonical connector and proxy. Imported by name so tests can substitute
# them; nothing else in this module reaches for the filesystem or the recorder
# directly.
from app.connecto import connector as canonical_connector
from app.connecto import proxy as canonical_proxy

filebrowser = Blueprint("filebrowser", __name__)

# The connector's root. Takes live under here and nowhere else.
TAKES_ROOT = os.path.expanduser("~/recordings")


class Unauthorized(Exception):
    """The connector refused the caller's token."""


def _connector():
    """The canonical connector, rooted at the takes directory."""
    return canonical_connector(root=TAKES_ROOT)


def _proxy():
    """The canonical proxy in front of the connector."""
    return canonical_proxy(connector=_connector())


def _authorize(conn):
    """
    Ask the connector whether this request may proceed, and raise if not.

    The token comes from the Authorization header ("Bearer <token>") or, for
    the browser's own fetches, the `token` query parameter. The connector is
    the only thing that decides; an empty token is passed through so the
    connector can reject it in its own way rather than this module inventing a
    second policy.
    """
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else request.args.get("token", "")
    if not conn.authorized(token):
        raise Unauthorized("not authorized to reach the takes")


def _resolve(conn, name):
    """
    Resolve a client-supplied name through the connector, or raise.

    The connector returns None for anything that isn't a file inside its root,
    which covers traversal, absolute paths, and names that simply don't exist.
    """
    path = conn.resolve(name)
    if not path:
        raise FileNotFoundError(name)
    return path


@filebrowser.route("/takes", methods=["GET"])
def list_takes():
    """List the takes the caller is authorized to see."""
    conn = _connector()
    _authorize(conn)
    return jsonify({"takes": canonical_proxy(connector=conn).listing()})


@filebrowser.route("/takes/<path:name>", methods=["GET"])
def download_take(name):
    """Download one take, if the caller is authorized and it's inside the root."""
    conn = _connector()
    _authorize(conn)
    path = _resolve(conn, name)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@filebrowser.route("/takes", methods=["POST"])
def upload_take():
    """Store an uploaded take under the root, if the caller is authorized."""
    conn = _connector()
    _authorize(conn)
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "no file in the request"}), 400
    name = conn.store(upload.filename, upload.stream)
    return jsonify({"name": name}), 201
