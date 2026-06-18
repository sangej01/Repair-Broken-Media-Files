"""Radarr API client for remediation workflow."""
import time
from datetime import datetime, timedelta
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
        self._library_cache: Optional[List[Dict[str, Any]]] = None
        self._library_cache_time: Optional[datetime] = None
    
    def get_library_cached(self, ttl_sec: int = 300) -> List[Dict[str, Any]]:
        """Fetch full Radarr library with TTL cache."""
        now = datetime.utcnow()
        
        # Check if cache is valid
        if self._library_cache is not None and self._library_cache_time is not None:
            age = (now - self._library_cache_time).total_seconds()
            if age < ttl_sec:
                return self._library_cache
        
        # Fetch from Radarr
        try:
            resp = requests.get(
                f"{self.url}/api/v3/movie",
                headers=self.headers,
                timeout=30
            )
            resp.raise_for_status()
            self._library_cache = resp.json()
            self._library_cache_time = now
            return self._library_cache
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to fetch Radarr library: {e}")
    
    def find_movie_by_path(self, folder_path: str) -> Optional[Dict[str, Any]]:
        """Resolve folder path to Radarr movie record."""
        library = self.get_library_cached()
        
        # Normalize folder path for comparison
        folder_name = Path(folder_path).name.lower()
        
        for movie in library:
            # Check against movie path
            movie_path = movie.get("path", "")
            if movie_path:
                movie_folder_name = Path(movie_path).name.lower()
                if movie_folder_name == folder_name:
                    return movie
            
            # Also check folder name
            folder = movie.get("folder", "")
            if folder and folder.lower() == folder_name:
                return movie
        
        return None
    
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
