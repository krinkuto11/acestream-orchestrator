import time
import pytest
import threading
from unittest.mock import MagicMock, patch

# Correct imports for the AceStream Orchestrator repository
from app.proxy.stream_generator import StreamGenerator

class FastMockBuffer:
    """
    Simulates an Orchestrator buffer that has ALREADY downloaded 5 chunks.
    A greedy client would download this in 0.01 seconds. 
    A paced client (Leaky Bucket) should take ~3 seconds for 3 chunks.
    """
    def __init__(self, chunk_count=5, source_rate=1.0):
        self.chunks = [b"FAKE_TS_DATA_" * 65536 for _ in range(chunk_count)]
        self.index = chunk_count
        self.source_rate = source_rate
        self.lock = threading.Lock()

    def get_source_rate(self):
        return self.source_rate

    def get_chunks_with_cursor(self, local_index):
        with self.lock:
            if local_index < self.index:
                # Return chunks from local_index to live edge
                return self.chunks[local_index:], self.index
            return [], self.index

@pytest.fixture
def mock_orchestrator_server():
    """Mocks the global server state and configs for the Orchestrator"""
    
    # We must patch ConfigHelper and client_tracking_service so the test 
    # doesn't hang waiting for Redis or actual pre-buffers.
    with patch('app.proxy.stream_generator.ConfigHelper') as mock_config, \
         patch('app.proxy.server.ProxyServer.get_instance') as mock_get_instance, \
         patch('app.services.client_tracker.client_tracking_service') as mock_tracker:
        
        # Bypass startup holds
        mock_config.channel_init_grace_period.return_value = 0.0
        mock_config.proxy_prebuffer_seconds.return_value = 0.0
        mock_config.initial_data_wait_timeout.return_value = 5.0
        mock_config.initial_data_check_interval.return_value = 0.1
        
        # FIX: Provide mock values for no_data checks to prevent TypeError
        mock_config.no_data_timeout_checks.return_value = 60
        mock_config.no_data_check_interval.return_value = 1.0
        
        mock_instance = MagicMock()
        mock_client_manager = MagicMock()
        
        # Inject our instant-fill mock buffer (1 chunk/sec speed)
        mock_buffer = FastMockBuffer(chunk_count=5, source_rate=1.0)
        
        mock_instance.stream_buffers = {"test_stream": mock_buffer}
        mock_instance.client_managers = {"test_stream": mock_client_manager}
        mock_instance.stream_managers = {
            "test_stream": MagicMock(control_mode="api", connected=True)
        }
        
        mock_get_instance.return_value = mock_instance
        yield mock_instance, mock_buffer

class TestOrchestratorBufferControl:

    def test_client_cannot_swallow_buffer(self, mock_orchestrator_server):
        """
        CRITICAL TEST: Proves that the Orchestrator paces the client.
        Even if 5 chunks are instantly available in Redis/RAM, the client MUST NOT
        be allowed to download them instantly. They must be paced to the source_rate.
        """
        mock_server, mock_buffer = mock_orchestrator_server

        # 1. Initialize Orchestrator's StreamGenerator
        generator = StreamGenerator(
            content_id="test_stream",
            client_id="greedy_test_client",
            client_ip="127.0.0.1",
            client_user_agent="VLC/3.0.21"
        )
        
        # Disable initial burst (hoarding) to strictly test the pacing math
        generator.pacing_burst_chunks = 0
        
        # Ensure setup succeeds
        assert generator._setup_streaming() is True
        
        # FIX: The orchestrator starts clients at the absolute live edge (index 5)
        # by default. We must force them to index 0 so they actually have historical 
        # chunks in the buffer to try and "swallow".
        generator.local_index = 0

        # 2. Track time taken to consume the buffer
        start_time = time.time()
        chunks_received = 0
        
        # 3. Simulate a greedy client reading as fast as possible in a tight loop
        gen_iter = generator.generate()
        
        # Attempt to consume 3 chunks from the generator
        for _ in range(3):
            chunk = next(gen_iter)
            assert len(chunk) > 0
            chunks_received += 1

        elapsed_time = time.time() - start_time

        # 4. The Mathematical Assertion
        # If the client swallowed the buffer, elapsed_time would be ~0.001s.
        # Because source_rate is 1.0 chunk/sec, receiving 3 chunks (with burst=0) 
        # MUST have forced `_maybe_apply_client_pacing` to `time.sleep` 
        # for roughly 2.0 to 3.0 seconds total.
        
        assert chunks_received == 3, "Client did not receive the expected chunks."
        assert elapsed_time >= 2.0, (
            f"FAILURE: Orchestrator allowed the client to swallow the buffer! "
            f"3 chunks were consumed in only {elapsed_time:.3f} seconds. "
            f"Leaky Bucket pacing is broken or missing."
        )
        
        print(f"\n[SUCCESS] Pacing verified: 3 chunks took {elapsed_time:.2f}s to download.")

if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])