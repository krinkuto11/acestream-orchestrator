package main

import (
	"errors"
	"io"
	"testing"
)

func TestClassifyDisconnectReason_ClientDisconnects(t *testing.T) {
	tests := []struct {
		name           string
		err            error
		expectedReason string
		expectedDetail string
	}{
		{
			name:           "broken pipe",
			err:            errors.New("write tcp: broken pipe"),
			expectedReason: "client_disconnected",
			expectedDetail: "client closed connection (broken pipe)",
		},
		{
			name:           "connection reset by peer",
			err:            errors.New("read tcp: connection reset by peer"),
			expectedReason: "client_disconnected",
			expectedDetail: "client reset connection (RST packet received)",
		},
		{
			name:           "connection reset",
			err:            errors.New("connection reset"),
			expectedReason: "client_disconnected",
			expectedDetail: "connection reset by network or client",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			reason, detail := classifyDisconnectReason(tt.err)
			if reason != tt.expectedReason {
				t.Errorf("Expected reason %s, got %s", tt.expectedReason, reason)
			}
			if detail != tt.expectedDetail {
				t.Errorf("Expected detail %s, got %s", tt.expectedDetail, detail)
			}
		})
	}
}

func TestClassifyDisconnectReason_Timeouts(t *testing.T) {
	tests := []struct {
		name           string
		err            error
		expectedReason string
		expectedDetail string
	}{
		{
			name:           "empty timeout",
			err:            errors.New("stream empty timeout: no data received within timeout period"),
			expectedReason: "empty_timeout",
			expectedDetail: "stream closed due to inactivity (no data received within timeout period)",
		},
		{
			name:           "i/o timeout",
			err:            errors.New("read tcp: i/o timeout"),
			expectedReason: "timeout",
			expectedDetail: "I/O operation timed out",
		},
		{
			name:           "deadline exceeded",
			err:            errors.New("context deadline exceeded"),
			expectedReason: "timeout",
			expectedDetail: "deadline exceeded (context timeout or read/write timeout)",
		},
		{
			name:           "generic timeout",
			err:            errors.New("operation timeout"),
			expectedReason: "timeout",
			expectedDetail: "operation timed out",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			reason, detail := classifyDisconnectReason(tt.err)
			if reason != tt.expectedReason {
				t.Errorf("Expected reason %s, got %s", tt.expectedReason, reason)
			}
			if detail != tt.expectedDetail {
				t.Errorf("Expected detail %s, got %s", tt.expectedDetail, detail)
			}
		})
	}
}

func TestClassifyDisconnectReason_NetworkErrors(t *testing.T) {
	tests := []struct {
		name           string
		err            error
		expectedReason string
		expectedDetail string
	}{
		{
			name:           "network unreachable",
			err:            errors.New("network is unreachable"),
			expectedReason: "network_error",
			expectedDetail: "network is unreachable",
		},
		{
			name:           "no route to host",
			err:            errors.New("no route to host"),
			expectedReason: "network_error",
			expectedDetail: "no route to host",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			reason, detail := classifyDisconnectReason(tt.err)
			if reason != tt.expectedReason {
				t.Errorf("Expected reason %s, got %s", tt.expectedReason, reason)
			}
			if detail != tt.expectedDetail {
				t.Errorf("Expected detail %s, got %s", tt.expectedDetail, detail)
			}
		})
	}
}

func TestClassifyDisconnectReason_EOF(t *testing.T) {
	tests := []struct {
		name           string
		err            error
		expectedReason string
		expectedDetail string
	}{
		{
			name:           "io.EOF",
			err:            io.EOF,
			expectedReason: "eof",
			expectedDetail: "unexpected EOF from source stream",
		},
		{
			name:           "unexpected EOF",
			err:            errors.New("unexpected EOF"),
			expectedReason: "eof",
			expectedDetail: "unexpected EOF during read",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			reason, detail := classifyDisconnectReason(tt.err)
			if reason != tt.expectedReason {
				t.Errorf("Expected reason %s, got %s", tt.expectedReason, reason)
			}
			if detail != tt.expectedDetail {
				t.Errorf("Expected detail %s, got %s", tt.expectedDetail, detail)
			}
		})
	}
}

func TestClassifyDisconnectReason_ClosedPipe(t *testing.T) {
	tests := []struct {
		name           string
		err            error
		expectedReason string
		expectedDetail string
	}{
		{
			name:           "io.ErrClosedPipe",
			err:            io.ErrClosedPipe,
			expectedReason: "closed_pipe",
			expectedDetail: "write to closed pipe",
		},
		{
			name:           "use of closed network connection",
			err:            errors.New("use of closed network connection"),
			expectedReason: "closed_connection",
			expectedDetail: "attempted to use closed network connection",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			reason, detail := classifyDisconnectReason(tt.err)
			if reason != tt.expectedReason {
				t.Errorf("Expected reason %s, got %s", tt.expectedReason, reason)
			}
			if detail != tt.expectedDetail {
				t.Errorf("Expected detail %s, got %s", tt.expectedDetail, detail)
			}
		})
	}
}

func TestClassifyDisconnectReason_Nil(t *testing.T) {
	reason, detail := classifyDisconnectReason(nil)
	if reason != "completed" {
		t.Errorf("Expected reason completed for nil error, got %s", reason)
	}
	if detail != "stream finished normally" {
		t.Errorf("Expected detail 'stream finished normally' for nil error, got %s", detail)
	}
}

func TestClassifyDisconnectReason_UnknownError(t *testing.T) {
	err := errors.New("some unknown error")
	reason, detail := classifyDisconnectReason(err)
	if reason != "error" {
		t.Errorf("Expected reason error for unknown error, got %s", reason)
	}
	if detail != "unclassified error: some unknown error" {
		t.Errorf("Expected detail to include error message, got %s", detail)
	}
}
