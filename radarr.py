"""Radarr API client for remediation workflow."""
import time
from functools import lru_cache
from datetime import timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

from config import RADARR_URL, RADARR_API


class RadarrClient:
    """Radarr API client lifted from Radarr Import from Staging Folder patterns."""

    def __init__(self, url: str = RADARR_URL, api_key: str = RADARR_API):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-Api-Key": api_key}

    def get_library_cached(self, ttl_sec: int = 300) -> List[Dict[str, Any]]:
        """Fetch full Radarr library (cached for ttl_sec via lru_cache)."""
        return self._fetch_library()

    @lru_cache(maxsize=1)
    def _fetch_library(self) -> List[Dict[str, Any]]:
        """Internal cached fetch. Cache is per-instance, TTL via external cache clear if needed."""
        try:
            resp = requests.get(
                f"{self.url}/api/v3/movie",
                headers=self.headers,
                timeout=30
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to fetch Radarr library: {e}")
    
    def find_movie_by_path(self, folder_path: str) -> Optional[Dict[str, Any]]:
        """Resolve folder path to Radarr movie record.

        Matching is done in two passes:
        1. Exact (case-insensitive) folder name match.
        2. Punctuation-normalised match — strips apostrophes, dots, and
           colons, then collapses runs of spaces.  This handles cases where
           the scanner folder is named e.g. "Dont.Move. (2024)" while Radarr
           stores the path as "Don't Move (2024)".
        """
        import re

        def _norm(s: str) -> str:
            """Lowercase and strip punctuation that varies between sources.

            Dots are replaced with spaces (not deleted) so that "Dont.Move."
            and "Dont Move" both normalise to "dont move".  Apostrophes and
            colons are deleted because they carry no word-separation meaning.
            """
            s = s.lower()
            s = s.replace("'", "").replace(":", "")
            s = s.replace(".", " ")
            s = re.sub(r"\s+", " ", s).strip()
            return s

        library = self.get_library_cached()
        folder_name = Path(folder_path).name.lower()
        folder_name_norm = _norm(folder_name)

        exact_match = None
        norm_match = None

        for movie in library:
            movie_path = movie.get("path", "")
            candidates = [Path(movie_path).name if movie_path else "",
                          movie.get("folder", "") or ""]
            for candidate in candidates:
                if not candidate:
                    continue
                c_lower = candidate.lower()
                if c_lower == folder_name:
                    return movie          # exact hit — return immediately
                if _norm(c_lower) == folder_name_norm and norm_match is None:
                    norm_match = movie    # keep as fallback

        return norm_match
    
    def get_movie(self, movie_id: int) -> Optional[Dict[str, Any]]:
        """Get movie details by ID."""
        try:
            resp = requests.get(
                f"{self.url}/api/v3/movie/{movie_id}",
                headers=self.headers,
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to get movie {movie_id}: {e}")
    
    def unmonitor(self, movie_id: int):
        """Set movie to unmonitored."""
        movie = self.get_movie(movie_id)
        if not movie:
            raise RuntimeError(f"Movie {movie_id} not found")
        
        movie["monitored"] = False
        
        try:
            resp = requests.put(
                f"{self.url}/api/v3/movie/{movie_id}",
                headers=self.headers,
                json=movie,
                timeout=10
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to unmonitor movie {movie_id}: {e}")
    
    def monitor(self, movie_id: int):
        """Set movie to monitored."""
        movie = self.get_movie(movie_id)
        if not movie:
            raise RuntimeError(f"Movie {movie_id} not found")
        
        movie["monitored"] = True
        
        try:
            resp = requests.put(
                f"{self.url}/api/v3/movie/{movie_id}",
                headers=self.headers,
                json=movie,
                timeout=10
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to monitor movie {movie_id}: {e}")
    
    def delete_moviefile(self, file_id: int):
        """Delete moviefile record (not the file on disk)."""
        try:
            resp = requests.delete(
                f"{self.url}/api/v3/moviefile/{file_id}",
                headers=self.headers,
                timeout=10
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to delete moviefile {file_id}: {e}")
    
    def search(self, movie_id: int) -> int:
        """Trigger Radarr search for movie. Returns command ID."""
        payload = {
            "name": "MoviesSearch",
            "movieIds": [movie_id]
        }
        
        try:
            resp = requests.post(
                f"{self.url}/api/v3/command",
                headers=self.headers,
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            cmd_id = resp.json().get("id")
            return cmd_id
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to trigger search for movie {movie_id}: {e}")
    
    def get_queue(self) -> List[Dict[str, Any]]:
        """Return Radarr's current download queue (in-progress grabs).

        Each record includes movieId and size/sizeleft so we can tell which
        re-downloads are actively downloading vs. done.
        """
        records = []
        try:
            page = 1
            while True:
                resp = requests.get(
                    f"{self.url}/api/v3/queue",
                    headers=self.headers,
                    params={"page": page, "pageSize": 100},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                recs = data.get("records", data if isinstance(data, list) else [])
                records.extend(recs)
                total = data.get("totalRecords", len(records)) if isinstance(data, dict) else len(records)
                if len(records) >= total or not recs:
                    break
                page += 1
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to fetch Radarr queue: {e}")
        return records
    
    def get_movie_history(self, movie_id: int) -> List[Dict[str, Any]]:
        """Return history records for a movie, newest first.

        Each record includes 'id', 'eventType' ('grabbed', 'downloadFolderImported',
        etc.), 'sourceTitle' (the release name), and 'downloadId'.
        """
        try:
            resp = requests.get(
                f"{self.url}/api/v3/history/movie",
                headers=self.headers,
                params={"movieId": movie_id},
                timeout=15,
            )
            resp.raise_for_status()
            records = resp.json()
            # Sort newest first by date field if present.
            records.sort(key=lambda r: r.get("date", ""), reverse=True)
            return records
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to fetch history for movie {movie_id}: {e}")

    def mark_history_failed(self, history_id: int):
        """Mark a history record as failed.

        This simultaneously:
        1. Adds the release to Radarr's blocklist so it is never grabbed again.
        2. Triggers an automatic re-search for a *different* release.

        Use this for 'bad source' files where re-downloading the same release
        would just produce the same broken file.  Pass the 'id' of the most
        recent 'grabbed' history record for the movie.
        """
        try:
            resp = requests.post(
                f"{self.url}/api/v3/history/failed/{history_id}",
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to mark history {history_id} as failed: {e}")

    def wait_for_command(self, cmd_id: int, timeout: int = 300, interval: int = 2) -> bool:
        """Poll command status until complete. Returns True if successful."""
        max_polls = timeout // interval
        
        for _ in range(max_polls):
            time.sleep(interval)
            
            try:
                resp = requests.get(
                    f"{self.url}/api/v3/command/{cmd_id}",
                    headers=self.headers,
                    timeout=10
                )
                
                if resp.ok:
                    state = resp.json()
                    status = state.get("status")
                    
                    if status == "completed":
                        return True
                    elif status == "failed":
                        return False
            
            except requests.exceptions.RequestException:
                pass
        
        # Timeout
        return False
