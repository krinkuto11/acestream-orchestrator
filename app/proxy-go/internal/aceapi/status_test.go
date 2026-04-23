package aceapi

import (
	"testing"
)

func TestParseStatusLine(t *testing.T) {
	tests := []struct {
		name     string
		line     string
		expected *StatusInfo
	}{
		{
			name: "dl state",
			line: "STATUS main:dl;0;100;512;0;128;5;0;1024;0;256",
			expected: &StatusInfo{
				Status:            "dl",
				TotalProgress:     0,
				ImmediateProgress: 100,
				SpeedDown:         512,
				HttpSpeedDown:     0,
				SpeedUp:           128,
				Peers:             5,
				HttpPeers:         0,
				Downloaded:        1024,
				HttpDownloaded:    0,
				Uploaded:          256,
			},
		},
		{
			name: "wait state (normalization)",
			line: "STATUS main:wait;0;100;0;0;0;0;0;0;0;0",
			expected: &StatusInfo{
				Status: "wait",
				// After normalization, the redundant '0' at index 1 is removed.
				TotalProgress:     100,
				ImmediateProgress: 0,
			},
		},
		{
			name: "buf state (normalization)",
			line: "STATUS main:buf;0;100;50;512;0;0;0;0;100;0;0",
			expected: &StatusInfo{
				Status: "buf",
				// After normalization, indices 1-2 (0;100) are removed.
				TotalProgress:     50,
				ImmediateProgress: 512,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseStatusLine(tt.line)
			if got == nil {
				t.Fatal("got nil")
			}
			if got.Status != tt.expected.Status {
				t.Errorf("Status: got %v, want %v", got.Status, tt.expected.Status)
			}
			if got.SpeedDown != tt.expected.SpeedDown {
				t.Errorf("SpeedDown: got %v, want %v", got.SpeedDown, tt.expected.SpeedDown)
			}
			if got.Peers != tt.expected.Peers {
				t.Errorf("Peers: got %v, want %v", got.Peers, tt.expected.Peers)
			}
			if got.TotalProgress != tt.expected.TotalProgress {
				t.Errorf("TotalProgress: got %v, want %v", got.TotalProgress, tt.expected.TotalProgress)
			}
		})
	}
}
