import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.proxy.utils import SyncHunter

def test_sync_hunter_hardened():
    # Test 1: PUSI Alignment (align_to_frame=True)
    # We want to see it skipping packets that don't have the PUSI bit set
    hunter = SyncHunter(required_confirmations=2, align_to_frame=True)
    
    # Packet 1: Valid sync but NO PUSI (0x47 0x00 ...)
    p1 = b'\x47\x00' + b'A' * 186
    # Packet 2: Valid sync AND PUSI (0x47 0x40 ...)
    p2 = b'\x47\x40' + b'B' * 186
    # Packet 3: Valid sync but NO PUSI
    p3 = b'\x47\x00' + b'C' * 186
    # Packet 4: Valid sync AND PUSI
    p4 = b'\x47\x40' + b'D' * 186
    # Packet 5: Valid sync
    p5 = b'\x47\x00' + b'E' * 186

    # Feed p1 (No PUSI) -> Should NOT lock
    res = hunter.feed(p1)
    assert not hunter.is_locked
    assert res == b""
    print("Test 1.1 (Wait for PUSI) Passed")

    # Feed p2, p3, p4, p5 -> Should lock on p2 because it has PUSI and p3 follows
    res = hunter.feed(p2 + p3 + p4 + p5)
    assert hunter.is_locked
    assert res.startswith(b'\x47\x40') # Starts with p2
    assert len(res) == 188 * 4
    print("Test 1.2 (Lock on PUSI) Passed")

    # Test 2: Self-Healing (Sync Loss)
    # Feed junk to trigger sync loss
    junk = b'CORRUPT' * 20 # 140 bytes of garbage
    res = hunter.feed(junk)
    # It should detect invalid syncs and eventually drop lock
    # MAX_INVALID_SYNC = 5. Junk is 140 bytes. 140 / 188 < 5, but the first check at local[0] will fail.
    # Wait, it checks every 188 bytes.
    
    # Let's feed more systematic junk to be sure
    res = hunter.feed(b'\xFF' * (188 * 6))
    assert not hunter.is_locked
    print("Test 2.1 (Self-Healing / Lock Drop) Passed")

    # Test 3: Re-Locking after drop
    res = hunter.feed(p4 + p5)
    assert hunter.is_locked
    assert res.startswith(b'\x47\x40')
    print("Test 3 (Re-Lock) Passed")

if __name__ == "__main__":
    test_sync_hunter_hardened()
