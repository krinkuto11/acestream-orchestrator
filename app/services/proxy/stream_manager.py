"""Stream manager that pulls from AceStream and writes to buffer"""

import asyncio
import logging
import httpx
import time
from typing import Optional, Any

from .stream_buffer import StreamBuffer
from .config import COPY_CHUNK_SIZE, USER_AGENT

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages pulling stream data from AceStream and writing to buffer.
    
    Based on dispatcharr_proxy's StreamManager pattern:
    - Single connection to upstream (AceStream)
    - Writes data to Redis buffer
    - Multiple clients read from buffer independently
    - Handles connection failures and retries
    """
    
    def __init__(
        self,
        stream_id: str,
        playback_url: str,
        buffer: StreamBuffer,
        http_client: httpx.AsyncClient,
        proxy_mode: str = "lightweight",
        stream_session: Optional[Any] = None,
    ):
        """Initialize stream manager.
        
        Args:
            stream_id: Unique stream identifier
            playback_url: URL to fetch stream from
            buffer: Stream buffer to write to
            http_client: HTTP client for requests
            proxy_mode: Proxy mode ("lightweight" or "ffmpeg")
            stream_session: Reference to parent StreamSession (for metadata storage)
        """
        self.stream_id = stream_id
        self.playback_url = playback_url
        self.buffer = buffer
        self.http_client = http_client
        self.proxy_mode = proxy_mode
        self.stream_session = stream_session
        
        # State
        self.is_running = False
        self.is_connected = False
        self.stream_task: Optional[asyncio.Task] = None
        self.error: Optional[Exception] = None
        
        # Connection event - signals when connection is established or failed
        self.connection_event = asyncio.Event()
        
        # Stats
        self.bytes_received = 0
        self.chunks_received = 0
        self.start_time: Optional[float] = None
        
        # Health tracking
        self.last_data_time: Optional[float] = None
        self.healthy = True
        
        # FFmpeg process (only used in FFmpeg mode)
        self.ffmpeg_process: Optional[asyncio.subprocess.Process] = None
        
    async def start(self):
        """Start pulling stream data."""
        if self.is_running:
            logger.warning(f"StreamManager for {self.stream_id} already running")
            return
        
        self.is_running = True
        self.start_time = time.time()
        # Reset connection event for new start
        self.connection_event.clear()
        self.stream_task = asyncio.create_task(self._stream_loop())
        logger.info(f"Started StreamManager for {self.stream_id}")
    
    async def stop(self):
        """Stop pulling stream data."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None
        
        logger.info(
            f"Stopped StreamManager for {self.stream_id} "
            f"({self.chunks_received} chunks, {self.bytes_received / 1024 / 1024:.1f}MB)"
        )
    
    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Wait for the stream manager to establish connection.
        
        Args:
            timeout: Maximum time to wait for connection in seconds
            
        Returns:
            True if connected successfully, False if timeout or connection failed
        """
        try:
            await asyncio.wait_for(self.connection_event.wait(), timeout=timeout)
            # Event was set - check if it was due to success or error
            if self.error:
                logger.error(
                    f"StreamManager connection failed for {self.stream_id}: {self.error}"
                )
                return False
            if not self.is_connected:
                logger.error(
                    f"StreamManager connection event set but not connected for {self.stream_id}"
                )
                return False
            logger.info(f"StreamManager connected successfully for {self.stream_id}")
            return True
        except asyncio.TimeoutError:
            logger.error(
                f"Timeout waiting for StreamManager connection for {self.stream_id} "
                f"after {timeout}s"
            )
            return False
    
    async def _stream_loop(self):
        """Main loop that pulls data from AceStream and writes to buffer."""
        if self.proxy_mode == "ffmpeg":
            await self._stream_loop_ffmpeg()
        else:
            await self._stream_loop_lightweight()
    
    async def _stream_loop_lightweight(self):
        """Lightweight mode: Direct pipe from playback URL to buffer."""
        try:
            logger.info(f"Starting lightweight stream fetch for {self.stream_id} from {self.playback_url}")
            
            # Build headers - critical for AceStream compatibility
            # Based on acexy reference implementation
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Encoding": "identity",  # Disable compression - required for AceStream
            }
            
            # Stream from playback URL
            async with self.http_client.stream(
                "GET",
                self.playback_url,
                headers=headers,
                timeout=httpx.Timeout(timeout=None, connect=30.0, read=None, write=None, pool=None)
            ) as response:
                logger.info(
                    f"Stream response received for {self.stream_id}, "
                    f"status: {response.status_code}"
                )
                response.raise_for_status()
                
                self.is_connected = True
                # Signal that connection is established
                self.connection_event.set()
                last_log_time = time.time()
                
                async for chunk in response.aiter_bytes(chunk_size=COPY_CHUNK_SIZE):
                    if not chunk or not self.is_running:
                        break
                    
                    # Update stats
                    self.chunks_received += 1
                    self.bytes_received += len(chunk)
                    self.last_data_time = time.time()
                    self.healthy = True
                    
                    # Write to buffer
                    success = self.buffer.add_chunk(chunk)
                    if not success:
                        logger.warning(f"Failed to add chunk to buffer for {self.stream_id}")
                    
                    # Log progress periodically
                    if self.chunks_received % 1000 == 0:
                        elapsed = time.time() - last_log_time
                        rate = 1000 / elapsed if elapsed > 0 else 0
                        logger.debug(
                            f"Stream {self.stream_id}: {self.chunks_received} chunks "
                            f"({self.bytes_received / 1024 / 1024:.1f}MB), "
                            f"buffer_index={self.buffer.index}, "
                            f"rate={rate:.1f} chunks/s"
                        )
                        last_log_time = time.time()
                
                logger.info(f"Stream {self.stream_id} ended normally after {self.chunks_received} chunks")
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.error = e
            self.healthy = False
            # Signal connection event even on error so initialize() doesn't hang
            self.connection_event.set()
            
        except Exception as e:
            logger.error(f"Unexpected error streaming {self.stream_id}: {e}", exc_info=True)
            self.error = e
            self.healthy = False
            # Signal connection event even on error so initialize() doesn't hang
            self.connection_event.set()
            
        finally:
            self.is_connected = False
    
    async def _stream_loop_ffmpeg(self):
        """FFmpeg mode: Pipe through FFmpeg for compatibility and metadata extraction."""
        try:
            logger.info(f"Starting FFmpeg stream fetch for {self.stream_id} from {self.playback_url}")
            
            # First, extract metadata using ffprobe
            await self._extract_metadata_ffprobe()
            
            # Build FFmpeg command for passthrough (copy codecs, minimal overhead)
            # This allows FFmpeg to handle any stream format issues while maintaining quality
            ffmpeg_cmd = [
                "ffmpeg",
                "-i", self.playback_url,
                "-c", "copy",  # Copy all streams without re-encoding
                "-f", "mpegts",  # Output format
                "-user_agent", USER_AGENT,
                "-headers", "Accept-Encoding: identity",
                "pipe:1"  # Output to stdout
            ]
            
            logger.info(f"Starting FFmpeg process for {self.stream_id}: {' '.join(ffmpeg_cmd)}")
            
            # Start FFmpeg process
            self.ffmpeg_process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            self.is_connected = True
            self.connection_event.set()
            last_log_time = time.time()
            
            # Read from FFmpeg stdout and write to buffer
            while self.is_running and self.ffmpeg_process.returncode is None:
                chunk = await self.ffmpeg_process.stdout.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                
                # Update stats
                self.chunks_received += 1
                self.bytes_received += len(chunk)
                self.last_data_time = time.time()
                self.healthy = True
                
                # Write to buffer
                success = self.buffer.add_chunk(chunk)
                if not success:
                    logger.warning(f"Failed to add chunk to buffer for {self.stream_id}")
                
                # Log progress periodically
                if self.chunks_received % 1000 == 0:
                    elapsed = time.time() - last_log_time
                    rate = 1000 / elapsed if elapsed > 0 else 0
                    logger.debug(
                        f"FFmpeg Stream {self.stream_id}: {self.chunks_received} chunks "
                        f"({self.bytes_received / 1024 / 1024:.1f}MB), "
                        f"buffer_index={self.buffer.index}, "
                        f"rate={rate:.1f} chunks/s"
                    )
                    last_log_time = time.time()
            
            # Wait for process to complete
            await self.ffmpeg_process.wait()
            
            if self.ffmpeg_process.returncode != 0:
                stderr = await self.ffmpeg_process.stderr.read()
                logger.error(
                    f"FFmpeg process exited with code {self.ffmpeg_process.returncode} "
                    f"for {self.stream_id}: {stderr.decode('utf-8', errors='ignore')}"
                )
            else:
                logger.info(f"FFmpeg stream {self.stream_id} ended normally after {self.chunks_received} chunks")
                
        except Exception as e:
            logger.error(f"Error in FFmpeg streaming {self.stream_id}: {e}", exc_info=True)
            self.error = e
            self.healthy = False
            self.connection_event.set()
            
        finally:
            self.is_connected = False
            # Cleanup FFmpeg process
            if self.ffmpeg_process and self.ffmpeg_process.returncode is None:
                try:
                    self.ffmpeg_process.terminate()
                    await asyncio.wait_for(self.ffmpeg_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.ffmpeg_process.kill()
                    await self.ffmpeg_process.wait()
                except Exception as e:
                    logger.error(f"Error cleaning up FFmpeg process: {e}")
    
    async def _extract_metadata_ffprobe(self):
        """Extract stream metadata using ffprobe."""
        try:
            # Build ffprobe command to get stream info as JSON
            ffprobe_cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-user_agent", USER_AGENT,
                "-headers", "Accept-Encoding: identity",
                self.playback_url
            ]
            
            logger.debug(f"Running ffprobe for {self.stream_id}")
            
            # Run ffprobe with timeout
            process = await asyncio.create_subprocess_exec(
                *ffprobe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
            
            if process.returncode != 0:
                logger.warning(
                    f"ffprobe failed for {self.stream_id}: {stderr.decode('utf-8', errors='ignore')}"
                )
                return
            
            # Parse JSON output
            import json
            data = json.loads(stdout.decode('utf-8'))
            
            # Extract video and audio stream info
            for stream in data.get("streams", []):
                codec_type = stream.get("codec_type")
                
                if codec_type == "video" and self.stream_session:
                    # Extract video metadata
                    width = stream.get("width")
                    height = stream.get("height")
                    if width and height:
                        self.stream_session.resolution = f"{width}x{height}"
                    
                    fps_str = stream.get("r_frame_rate", "")
                    if fps_str and "/" in fps_str:
                        try:
                            num, denom = fps_str.split("/")
                            self.stream_session.fps = float(num) / float(denom)
                        except (ValueError, ZeroDivisionError):
                            pass
                    
                    self.stream_session.video_codec = stream.get("codec_name")
                    
                    logger.info(
                        f"Extracted video metadata for {self.stream_id}: "
                        f"resolution={self.stream_session.resolution}, "
                        f"fps={self.stream_session.fps}, "
                        f"codec={self.stream_session.video_codec}"
                    )
                
                elif codec_type == "audio" and self.stream_session:
                    self.stream_session.audio_codec = stream.get("codec_name")
                    logger.info(
                        f"Extracted audio metadata for {self.stream_id}: "
                        f"codec={self.stream_session.audio_codec}"
                    )
            
            # Update metadata in state after extraction
            if self.stream_session:
                self.stream_session.update_metadata_in_state()
                    
        except asyncio.TimeoutError:
            logger.warning(f"ffprobe timeout for {self.stream_id}")
        except Exception as e:
            logger.warning(f"Error extracting metadata with ffprobe for {self.stream_id}: {e}")
    
    def is_healthy(self) -> bool:
        """Check if stream manager is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.healthy:
            return False
        
        # Check if we've received data recently
        if self.last_data_time:
            elapsed = time.time() - self.last_data_time
            if elapsed > 30:  # No data for 30 seconds
                logger.warning(
                    f"StreamManager for {self.stream_id} unhealthy: "
                    f"no data for {elapsed:.1f}s"
                )
                return False
        
        return True
