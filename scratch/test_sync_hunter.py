import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.proxy.utils import SyncHunter

def test_sync_hunter_alignment():
    hunter = SyncHunter(required_confirmations=3)
    
    # Create valid TS packets
    packet1 = b'\x47' + b'A' * 187
    packet2 = b'\x47' + b'B' * 187
    packet3 = b'\x47' + b'C' * 187
    packet4 = b'\x47' + b'D' * 187
    
    # Test 1: Perfectly aligned input
    data = packet1 + packet2 + packet3 + packet4
    result = hunter.feed(data)
    assert len(result) == 188 * 4
    assert result[:1] == b'\x47'
    assert result[188:189] == b'\x47'
    print("Test 1 (Aligned) Passed")
    
    # Test 2: Misaligned input (junk at the start)
    hunter.reset()
    junk = b'SOME JUNK DATA'
    data = junk + packet1 + packet2 + packet3 + packet4
    
    # Feeding just junk + 2 packets shouldn't lock yet (needs 3)
    result = hunter.feed(data[:len(junk) + 188*2])
    assert result == b""
    print("Test 2 (Incomplete) Passed")
    
    # Feed the rest
    result = hunter.feed(data[len(junk) + 188*2:])
    assert len(result) == 188 * 4
    assert result.startswith(b'\x47')
    print("Test 2 (Misaligned) Passed")

    # Test 3: False sync (0x47 in payload)
    hunter.reset()
    junk = b'JUNK\x47RANDOM' # 0x47 at offset 4
    # Real packets start after some more junk to ensure offset 4 doesn't align
    data = junk + b'MOREJUNK' + packet1 + packet2 + packet3 + packet4
    
    result = hunter.feed(data)
    print(f"Test 3 result length: {len(result)} (expected {188*4})")
    if len(result) != 188 * 4:
        print(f"Buffer length after failed lock attempt: {len(hunter.buffer)}")
        print(f"Hunter state: locked={hunter.is_locked}")
    
    # Total data length: 4 (JUNK) + 1 (0x47) + 6 (RANDOM) + 8 (MOREJUNK) + 188*4 = 19 + 752 = 771
    # Real packets start at index 19.
    # The hunter should skip everything until index 19.
    assert len(result) == 188 * 4
    assert result.startswith(b'\x47')
    assert result[1:2] == b'A'
    print("Test 3 (False Sync) Passed")

if __name__ == "__main__":
    test_sync_hunter_alignment()
