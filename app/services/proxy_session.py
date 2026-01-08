"""
Proxy Session for individual AceStream streams.

Each ProxySession represents one stream from an AceStream engine,
with multiple clients multiplexed to the same stream.
"""

import asyncio
import logging
import httpx
from typing import Optional, AsyncIterator
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ProxySession:
    """Manages a single AceStream stream session with multiple clients."""
    
    def __init__(self, content_id: str):
        self.content_id = content_id
        self.engine_id: Optional[str] = None
        self.engine_host: Optional[str] = None
        self.engine_port: Optional[int] = None
        self.playback_url: Optional[str] = None
        self.stat_url: Optional[str] = None
        self.command_url: Optional[str] = None
        self.playback_session_id: Optional[str] = None
        self.started_at: Optional[datetime] = None
        
        # Client management
        from .proxy_client_manager import ProxyClientManager
        self.client_manager = ProxyClientManager()
        
        # Stream buffer for multiplexing
        from .proxy_buffer import ProxyBuffer
        self.buffer = ProxyBuffer()
        
        # HTTP stream reader task
        self._reader_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        
        logger.info(f"ProxySession created for content_id={content_id}")
    
    async def initialize(self):
        """Initialize the session by selecting an engine and starting the stream."""
        # Select best engine
        engine = await self._select_engine()
        if not engine:
            raise RuntimeError("No suitable engine available")
        
        self.engine_id = engine["container_id"]
        self.engine_host = engine["host"]
        self.engine_port = engine["port"]
        
        # Request stream from engine
        await self._request_stream()
        
        # Start HTTP reader
        self._reader_task = asyncio.create_task(self._read_stream())
        
        self.started_at = datetime.now(timezone.utc)
        logger.info(
            f"ProxySession initialized for content_id={self.content_id} "
            f"on engine {self.engine_id[:12]} ({self.engine_host}:{self.engine_port})"
        )
    
    async def _select_engine(self) -> Optional[dict]:
        """
        Select the best available engine for this stream.
        
        Priority:
        1. Among engines with same stream count, choose forwarded ones
        2. Otherwise choose engine with least streams
        
        Returns:
            Engine dict with container_id, host, port
        """
        from .state import state
        
        engines = state.list_engines()
        if not engines:
            logger.error("No engines available")
            return None
        
        # Count streams per engine
        active_streams = state.list_streams(status="started")
        engine_stream_counts = {}
        for stream in active_streams:
            cid = stream.container_id
            engine_stream_counts[cid] = engine_stream_counts.get(cid, 0) + 1
        
        # Sort engines by priority:
        # 1. Fewer streams first
        # 2. Forwarded engines preferred (when stream count is equal)
        def engine_priority(engine):
            stream_count = engine_stream_counts.get(engine.container_id, 0)
            # Lower is better: (stream_count, not forwarded)
            # This makes forwarded engines preferred when stream counts are equal
            return (stream_count, not engine.forwarded)
        
        engines_sorted = sorted(engines, key=engine_priority)
        selected = engines_sorted[0]
        
        logger.info(
            f"Selected engine {selected.container_id[:12]} "
            f"(forwarded={selected.forwarded}, streams={engine_stream_counts.get(selected.container_id, 0)})"
        )
        
        return {
            "container_id": selected.container_id,
            "host": selected.host,
            "port": selected.port,
            "forwarded": selected.forwarded,
        }
    
    async def _request_stream(self):
        """Request stream from AceStream engine."""
        url = f"http://{self.engine_host}:{self.engine_port}/ace/getstream"
        params = {
            "format": "json",
            "infohash": self.content_id,
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if data.get("error"):
                    raise RuntimeError(f"Engine returned error: {data['error']}")
                
                resp_data = data.get("response", {})
                self.playback_url = resp_data.get("playback_url")
                self.stat_url = resp_data.get("stat_url")
                self.command_url = resp_data.get("command_url")
                self.playback_session_id = resp_data.get("playback_session_id")
                
                if not self.playback_url:
                    raise RuntimeError("No playback_url in engine response")
                
                logger.info(
                    f"Stream requested successfully: playback_session_id={self.playback_session_id}"
                )
                
        except Exception as e:
            logger.error(f"Failed to request stream from engine: {e}")
            raise
    
    async def _read_stream(self):
        """Read stream data from engine and feed to buffer."""
        if not self.playback_url:
            logger.error("No playback_url, cannot start stream reader")
            return
        
        logger.info(f"Starting HTTP stream reader for {self.playback_url}")
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=30.0)) as client:
                async with client.stream("GET", self.playback_url) as response:
                    response.raise_for_status()
                    
                    chunk_count = 0
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if self._stop_event.is_set():
                            break
                        
                        if chunk:
                            await self.buffer.add_chunk(chunk)
                            chunk_count += 1
                            
                            if chunk_count % 1000 == 0:
                                logger.debug(f"Streamed {chunk_count} chunks for content_id={self.content_id}")
                    
                    logger.info(f"Stream ended for content_id={self.content_id}")
                    
        except Exception as e:
            logger.error(f"Error reading stream for content_id={self.content_id}: {e}")
            # Mark buffer as failed so clients know to disconnect
            self.buffer.mark_failed()
            raise
    
    async def stream_data(self, client_id: str) -> AsyncIterator[bytes]:
        """
        Stream data to a client.
        
        Args:
            client_id: Unique client identifier
            
        Yields:
            Chunks of video data
        """
        logger.info(f"Starting stream for client {client_id}")
        
        try:
            # Start from current buffer position
            chunk_index = 0
            
            while True:
                # Check if stream has failed
                if self.buffer.is_failed():
                    logger.error(f"Stream failed for client {client_id}")
                    raise RuntimeError("Stream failed")
                
                # Get chunks from buffer
                chunks = await self.buffer.get_chunks(chunk_index)
                
                if chunks:
                    # Yield chunks to client
                    for chunk in chunks:
                        yield chunk
                    chunk_index += len(chunks)
                else:
                    # No data available, wait a bit
                    await asyncio.sleep(0.1)
                    
                    # Check if reader task has ended
                    if self._reader_task and self._reader_task.done():
                        # Check if there was an exception
                        try:
                            self._reader_task.result()
                        except Exception:
                            # Reader failed, propagate error
                            logger.error(f"Stream reader failed for client {client_id}")
                            raise RuntimeError("Stream reader failed")
                        
                        # Reader finished naturally, check if buffer has more data
                        if not await self.buffer.has_data(chunk_index):
                            logger.info(f"Stream ended for client {client_id}")
                            break
                            
        except asyncio.CancelledError:
            logger.info(f"Stream cancelled for client {client_id}")
            raise
        except Exception as e:
            logger.error(f"Error streaming to client {client_id}: {e}")
            raise
    
    async def stop(self):
        """Stop the session and clean up resources."""
        logger.info(f"Stopping session for content_id={self.content_id}")
        
        # Signal stop
        self._stop_event.set()
        
        # Cancel reader task
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        # Stop stream on engine if we have command_url
        if self.command_url:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.get(f"{self.command_url}?method=stop")
                    logger.info(f"Sent stop command to engine for content_id={self.content_id}")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
        
        # Clear buffer
        self.buffer.clear()
        
        logger.info(f"Session stopped for content_id={self.content_id}")
