"""
HTTP Stream Reader - Thread-based HTTP stream reader that writes to a pipe.
Moved from app/proxy to app/data_plane.
"""

import threading
import os
import requests
import logging
from requests.adapters import HTTPAdapter
from ..core.config import cfg
from ..shared.utils import get_logger, SyncHunter
from ..shared.constants import VLC_USER_AGENT

logger = get_logger("http_streamer")

class HTTPStreamReader:
    """Thread-based HTTP stream reader that writes to a pipe"""

    def __init__(self, url, user_agent=None, chunk_size=8192):
        self.url = url
        self.user_agent = user_agent
        self.chunk_size = chunk_size
        self.session = None
        self.response = None
        self.thread = None
        self.pipe_read = None
        self.pipe_write = None
        self.running = False

    def start(self):
        """Start the HTTP stream reader thread"""
        self.pipe_read, self.pipe_write = os.pipe()
        self.running = True
        self.thread = threading.Thread(target=self._read_stream, daemon=True)
        self.thread.start()
        return self.pipe_read

    def _read_stream(self):
        try:
            headers = {
                'User-Agent': self.user_agent if self.user_agent else VLC_USER_AGENT,
                'Accept': '*/*',
                'Accept-Encoding': 'identity',
                'Connection': 'keep-alive',
            }

            self.session = requests.Session()
            adapter = HTTPAdapter(max_retries=0, pool_connections=1, pool_maxsize=1)
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)

            connect_timeout = 3.0
            read_timeout = 30.0
            timeout_pair = (connect_timeout, read_timeout)

            self.response = self.session.get(
                self.url,
                headers=headers,
                stream=True,
                timeout=timeout_pair
            )

            if self.response.status_code != 200:
                logger.error(f"HTTP {self.response.status_code} from {self.url}")
                return

            hunter = SyncHunter(required_confirmations=3)
            
            for chunk in self.response.iter_content(chunk_size=8272):
                if not self.running:
                    break

                if chunk:
                    aligned_data = hunter.feed(chunk)
                    if aligned_data:
                        try:
                            os.write(self.pipe_write, aligned_data)
                        except OSError:
                            break
        except Exception as e:
            logger.error(f"Streaming error: {e}")
        finally:
            self.running = False
            try:
                if self.pipe_write is not None:
                    os.close(self.pipe_write)
                    self.pipe_write = None
            except:
                pass

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        if self.response:
            try: self.response.close()
            except: pass
        if self.session:
            try: self.session.close()
            except: pass
        if self.pipe_write is not None:
            try:
                os.close(self.pipe_write)
                self.pipe_write = None
            except:
                pass
