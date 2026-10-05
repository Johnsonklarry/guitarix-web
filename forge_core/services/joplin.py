import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class JoplinClient:
    """Client for interacting with the Joplin Data API."""

    def __init__(self, token: str, base_url: str = "http://localhost:41184") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(self, endpoint: str, query_params: Optional[Dict[str, Any]] = None) -> Any:
        params = {"token": self.token}
        if query_params:
            params.update(query_params)
        url = f"{self.base_url}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))

    def list_notebooks(self) -> List[Dict[str, str]]:
        """List all notebooks with normalized IDs and titles."""
        data = self._request("folders")
        items = data.get("items", data) if isinstance(data, dict) else data
        notebooks = []
        for item in items:
            notebooks.append({
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
            })
        return notebooks

    def get_note(self, note_id: str) -> Dict[str, Any]:
        """Retrieve a note by ID with normalized title, body, timestamp, and parent notebook ID."""
        fields = "id,title,body,updated_time,parent_id"
        item = self._request(f"notes/{note_id}", query_params={"fields": fields})
        return {
            "id": str(item.get("id", "")),
            "title": str(item.get("title", "")),
            "body": str(item.get("body", "")),
            "updated_time": item.get("updated_time"),
            "parent_id": str(item.get("parent_id", "")),
        }
