"""
Utility functions for VOD proxy operations.
"""

import logging
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def get_client_info(request):
    """
    Extract client IP and User-Agent from request.

    Args:
        request: Django HttpRequest object

    Returns:
        tuple: (client_ip, user_agent)
    """
    # Get client IP, checking for proxy headers
    client_ip = request.META.get('HTTP_X_FORWARDED_FOR')
    if client_ip:
        # Take the first IP if there are multiple (comma-separated)
        client_ip = client_ip.split(',')[0].strip()
    else:
        client_ip = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', 'unknown')

    # Get User-Agent
    user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')

    return client_ip, user_agent


def create_vod_response(content, content_type='video/mp4', filename=None):
    """
    Create a streaming HTTP response for VOD content.

    Args:
        content: Content to stream (file-like object or bytes)
        content_type: MIME type of the content
        filename: Optional filename for Content-Disposition header

    Returns:
        HttpResponse: Configured HTTP response for streaming
    """
    response = HttpResponse(content, content_type=content_type)

    if filename:
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Add headers for streaming
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'

    return response
