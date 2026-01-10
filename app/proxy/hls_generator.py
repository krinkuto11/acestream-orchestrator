"""
HLS Stream Generator for proxying HLS streams from AceStream engines.
This handles M3U8 manifest proxying and URL rewriting.
"""

import requests
import re
import logging
from typing import AsyncGenerator
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class HLSStreamGenerator:
    """Handles HLS manifest proxying and URL rewriting"""
    
    def __init__(self, playback_url: str, base_path: str):
        """
        Initialize HLS stream generator.
        
        Args:
            playback_url: The M3U8 manifest URL from AceStream engine
            base_path: Base path for rewriting URLs (e.g., "/ace/hls/content_id")
        """
        self.playback_url = playback_url
        self.base_path = base_path
        
    async def generate_manifest(self) -> AsyncGenerator[bytes, None]:
        """
        Fetch and proxy HLS manifest with URL rewriting.
        
        Yields:
            Chunks of the modified M3U8 manifest
        """
        try:
            logger.info(f"Fetching HLS manifest from {self.playback_url}")
            
            # Fetch the manifest from AceStream engine
            response = requests.get(self.playback_url, timeout=10)
            response.raise_for_status()
            
            manifest_content = response.text
            logger.debug(f"Original manifest:\n{manifest_content}")
            
            # Rewrite URLs in the manifest
            modified_manifest = self._rewrite_urls(manifest_content)
            logger.debug(f"Modified manifest:\n{modified_manifest}")
            
            # Yield the modified manifest
            yield modified_manifest.encode('utf-8')
            
        except Exception as e:
            logger.error(f"Error fetching HLS manifest: {e}", exc_info=True)
            raise
    
    def _rewrite_urls(self, manifest: str) -> str:
        """
        Rewrite URLs in M3U8 manifest to proxy through orchestrator.
        
        Args:
            manifest: Original M3U8 manifest content
            
        Returns:
            Modified manifest with rewritten URLs
        """
        lines = manifest.split('\n')
        modified_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip comment lines and empty lines
            if not line or line.startswith('#'):
                modified_lines.append(line)
                continue
            
            # This is a segment URL or sub-manifest URL
            # Check if it's already an absolute URL
            if line.startswith('http://') or line.startswith('https://'):
                # Extract the path component and rewrite it
                parsed = urlparse(line)
                # Keep the segment filename but proxy it through our endpoint
                segment_path = parsed.path.split('/')[-1]
                rewritten_url = f"{self.base_path}/segment/{segment_path}"
                modified_lines.append(rewritten_url)
            elif line.startswith('/'):
                # Absolute path - extract filename
                segment_path = line.split('/')[-1]
                rewritten_url = f"{self.base_path}/segment/{segment_path}"
                modified_lines.append(rewritten_url)
            else:
                # Relative path - rewrite to proxy through our endpoint
                rewritten_url = f"{self.base_path}/segment/{line}"
                modified_lines.append(rewritten_url)
        
        return '\n'.join(modified_lines)


async def proxy_hls_segment(segment_url: str) -> AsyncGenerator[bytes, None]:
    """
    Proxy an HLS segment from the AceStream engine.
    
    Args:
        segment_url: Full URL to the segment on the AceStream engine
        
    Yields:
        Chunks of the segment data
    """
    try:
        logger.debug(f"Proxying HLS segment from {segment_url}")
        
        # Stream the segment
        with requests.get(segment_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            
            # Stream chunks to client
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
                    
    except Exception as e:
        logger.error(f"Error proxying HLS segment: {e}", exc_info=True)
        raise
