package acexy

import (
	"bufio"
	"errors"
	"io"
	"log/slog"
	"sync/atomic"
	"time"
)

// ErrEmptyTimeout is returned when the copier times out waiting for data
var ErrEmptyTimeout = errors.New("stream empty timeout: no data received within timeout period")

// Copier is an implementation that copies the data from the source to the destination.
// It has an empty timeout that is used to determine when the source is empty - this is,
// it has no more data to read after the timeout.
type Copier struct {
	// The destination to copy the data to.
	Destination io.Writer
	// The source to copy the data from.
	Source io.Reader
	// The timeout to use when the source is empty.
	EmptyTimeout time.Duration
	// The buffer size to use when copying the data.
	BufferSize int

	/**! Private Data */
	timer          *time.Timer
	bufferedWriter *bufio.Writer
	bytesCopied    int64
	timedOut       atomic.Bool
}

// Starts copying the data from the source to the destination.
func (c *Copier) Copy() error {
	c.bufferedWriter = bufio.NewWriterSize(c.Destination, c.BufferSize)
	c.timer = time.NewTimer(c.EmptyTimeout)
	done := make(chan struct{})
	defer close(done)

	go func() {
		for {
			c.timer.Reset(c.EmptyTimeout)
			select {
			case <-done:
				slog.Debug("Done copying", "source", c.Source, "destination", c.Destination)
				return
			case <-c.timer.C:
				// On timeout, mark as timed out and close the source to interrupt io.Copy
				// We don't flush here to avoid race conditions with the main goroutine,
				// which may still be writing data. Flushing only happens in the main goroutine.
				c.timedOut.Store(true)
				slog.Info("Stream empty timeout triggered", "empty_timeout", c.EmptyTimeout, "bytes_copied", atomic.LoadInt64(&c.bytesCopied))
				// Close source to interrupt the io.Copy operation
				if closer, ok := c.Source.(io.Closer); ok {
					slog.Debug("Closing source due to empty timeout", "source", c.Source)
					closer.Close()
				}
				// Close destination to signal end of stream
				if closer, ok := c.Destination.(io.Closer); ok {
					slog.Debug("Closing destination due to empty timeout", "destination", c.Destination)
					closer.Close()
				}
				return
			}
		}
	}()

	_, err := io.Copy(c, c.Source)
	
	// Flush the buffer when copy completes (EOF or error)
	// This ensures buffered data is written before returning
	if ferr := c.bufferedWriter.Flush(); ferr != nil {
		slog.Debug("Error flushing buffer", "error", ferr)
		if err == nil {
			err = ferr
		}
	}
	
	// If the timeout occurred, return ErrEmptyTimeout instead of the underlying error
	if c.timedOut.Load() {
		slog.Debug("Returning empty timeout error", "underlying_error", err)
		return ErrEmptyTimeout
	}
	
	return err
}

// Write writes the data to the destination. It also resets the timer if there is data to write.
func (c *Copier) Write(p []byte) (n int, err error) {
	if len(p) == 0 {
		return 0, nil
	}
	if c.timer == nil || c.bufferedWriter == nil {
		return 0, io.ErrClosedPipe
	}
	// Reset the timer, since we have data to write
	c.timer.Reset(c.EmptyTimeout)
	// Write the data to the destination
	n, err = c.bufferedWriter.Write(p)
	atomic.AddInt64(&c.bytesCopied, int64(n))
	return n, err
}

// BytesCopied returns the total number of bytes copied
func (c *Copier) BytesCopied() int64 {
	return atomic.LoadInt64(&c.bytesCopied)
}
