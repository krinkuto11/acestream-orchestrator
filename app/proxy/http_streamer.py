"""
HTTP Stream Reader - Thread-based HTTP stream reader that writes to a pipe.
This allows us to use the same fetch_chunk() path for both transcode and HTTP streams.
"""

import threading
import os
import requests
from requests.adapters import HTTPAdapter
from .utils import get_logger
from .constants import VLC_USER_AGENT

logger = get_logger()


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
        # Create a pipe (works on Windows and Unix)
        self.pipe_read, self.pipe_write = os.pipe()

        # Start the reader thread
        self.running = True
        self.thread = threading.Thread(target=self._read_stream, daemon=True)
        self.thread.start()

        logger.info(f"Started HTTP stream reader thread for {self.url}")
        return self.pipe_read

    def _read_stream(self):
        """Thread worker that reads HTTP stream and writes to pipe"""
        try:
            # Build headers - mimic VLC player for better compatibility
            # Use provided user_agent or fall back to VLC default if None
            headers = {
                'User-Agent': self.user_agent if self.user_agent else VLC_USER_AGENT,
                'Accept': '*/*',
                'Accept-Encoding': 'identity',
                'Connection': 'keep-alive',
            }

            logger.info(f"HTTP reader connecting to {self.url}")
            logger.debug(f"Request headers: {headers}")

            # Create session
            self.session = requests.Session()

            # Disable retries for faster failure detection
            adapter = HTTPAdapter(max_retries=0, pool_connections=1, pool_maxsize=1)
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)

            # Stream the URL
            logger.debug(f"Initiating HTTP GET request with timeout=(5, 30)")
            self.response = self.session.get(
                self.url,
                headers=headers,
                stream=True,
                timeout=(5, 30)  # 5s connect, 30s read
            )

            logger.debug(f"HTTP response status: {self.response.status_code}")
            logger.debug(f"HTTP response headers: {dict(self.response.headers)}")

            if self.response.status_code != 200:
                logger.error(f"HTTP {self.response.status_code} from {self.url}")
                # Log a preview of the response body (limited to avoid loading entire response into memory)
                try:
                    # Read only first 500 bytes without loading entire response
                    response_preview = next(self.response.iter_content(chunk_size=500), b'').decode('utf-8', errors='ignore')
                    logger.debug(f"Response preview: {response_preview}")
                except Exception:
                    logger.debug("Could not read response preview")
                return

            logger.info(f"HTTP reader connected successfully, streaming data...")

            # Stream chunks to pipe
            chunk_count = 0
            try:
                for chunk in self.response.iter_content(chunk_size=self.chunk_size):
                    # Check if we should stop before processing chunk
                    if not self.running:
                        logger.debug("HTTP reader stopping (running=False)")
                        break

                    if chunk:
                        try:
                            # Write binary data to pipe
                            os.write(self.pipe_write, chunk)
                            chunk_count += 1

                            # Log progress periodically
                            if chunk_count % 1000 == 0:
                                logger.debug(f"HTTP reader streamed {chunk_count} chunks")
                        except OSError as e:
                            logger.error(f"Pipe write error: {e}")
                            break
            except requests.exceptions.ConnectionError as e:
                # Handle read timeouts and connection errors during streaming
                # This is common when the upstream source stops sending data or times out
                error_msg = str(e)
                if 'Read timed out' in error_msg or 'ReadTimeoutError' in error_msg:
                    logger.info(f"HTTP stream read timeout after {chunk_count} chunks - stream likely ended")
                else:
                    logger.warning(f"HTTP stream connection error: {error_msg}")
            except AttributeError as e:
                # This can happen if response is closed during iteration
                # Check if it's the specific 'read' error we expect during shutdown
                error_msg = str(e)
                if not self.running and ('read' in error_msg or 'NoneType' in error_msg):
                    logger.debug("HTTP reader stopped during iteration (expected)")
                else:
                    # Unexpected AttributeError - re-raise to avoid masking bugs
                    logger.error(f"Unexpected attribute error in HTTP reader: {e}", exc_info=True)
                    raise
            except Exception as e:
                logger.error(f"HTTP reader streaming error: {e}", exc_info=True)

            logger.info("HTTP stream ended")

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP reader request error: {e}")
        except Exception as e:
            logger.error(f"HTTP reader unexpected error: {e}", exc_info=True)
        finally:
            self.running = False
            # Close write end of pipe to signal EOF
            try:
                if self.pipe_write is not None:
                    os.close(self.pipe_write)
                    self.pipe_write = None
            except:
                pass

    def stop(self):
        """Stop the HTTP stream reader"""
        logger.info("Stopping HTTP stream reader")
        self.running = False

        # Wait a moment for the read loop to notice running=False and exit
        # This prevents closing the response while it's still being read
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)

        # Close response
        if self.response:
            try:
                self.response.close()
                self.response = None
            except:
                pass

        # Close session
        if self.session:
            try:
                self.session.close()
                self.session = None
            except:
                pass

        # Close write end of pipe
        if self.pipe_write is not None:
            try:
                os.close(self.pipe_write)
                self.pipe_write = None
            except:
                pass

        # Final wait for thread if it's still alive
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
