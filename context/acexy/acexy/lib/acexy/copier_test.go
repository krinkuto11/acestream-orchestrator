package acexy

import (
	"bytes"
	"errors"
	"io"
	"testing"
	"time"
)

// slowReader simulates a stream that sends data once, then blocks
type slowReader struct {
	data      []byte
	sent      bool
	blockTime time.Duration
}

func (s *slowReader) Read(p []byte) (n int, err error) {
	if !s.sent && len(s.data) > 0 {
		// Send initial data
		n = copy(p, s.data)
		s.sent = true
		return n, nil
	}
	// Block to simulate waiting for more data
	time.Sleep(s.blockTime)
	return 0, io.EOF
}

func TestCopier_EmptyTimeout(t *testing.T) {
	// Create a slow reader that sends data then goes silent
	reader := &slowReader{
		data:      []byte("test data"),
		blockTime: 200 * time.Millisecond,
	}
	
	var buf bytes.Buffer
	copier := &Copier{
		Destination:  &buf,
		Source:       reader,
		EmptyTimeout: 50 * time.Millisecond, // Timeout faster than the block time
		BufferSize:   1024,
	}
	
	err := copier.Copy()
	
	// Should get empty timeout error
	if !errors.Is(err, ErrEmptyTimeout) {
		t.Errorf("Expected ErrEmptyTimeout, got: %v", err)
	}
	
	// Should have copied the initial data
	if buf.Len() == 0 {
		t.Error("Expected some data to be copied before timeout")
	}
	
	// Should track bytes copied
	if copier.BytesCopied() == 0 {
		t.Error("Expected BytesCopied to be greater than 0")
	}
}

func TestCopier_NormalCompletion(t *testing.T) {
	data := []byte("complete data stream")
	reader := bytes.NewReader(data)
	
	var buf bytes.Buffer
	copier := &Copier{
		Destination:  &buf,
		Source:       reader,
		EmptyTimeout: 1 * time.Second, // Long timeout, shouldn't trigger
		BufferSize:   1024,
	}
	
	err := copier.Copy()
	
	// The copier returns EOF when the source is naturally exhausted.
	// This is expected for normal completion, but not an error.
	if err != nil && !errors.Is(err, io.EOF) {
		t.Errorf("Expected nil or EOF, got: %v", err)
	}
	
	// Should have copied all data
	if buf.Len() != len(data) {
		t.Errorf("Expected %d bytes, got %d", len(data), buf.Len())
	}
	
	// Should track bytes copied
	if copier.BytesCopied() != int64(len(data)) {
		t.Errorf("Expected %d bytes copied, got %d", len(data), copier.BytesCopied())
	}
}

func TestCopier_BytesCopied(t *testing.T) {
	data := []byte("test123")
	reader := bytes.NewReader(data)
	
	var buf bytes.Buffer
	copier := &Copier{
		Destination:  &buf,
		Source:       reader,
		EmptyTimeout: 1 * time.Second,
		BufferSize:   1024,
	}
	
	_ = copier.Copy()
	
	expected := int64(len(data))
	if copier.BytesCopied() != expected {
		t.Errorf("Expected %d bytes copied, got %d", expected, copier.BytesCopied())
	}
}
