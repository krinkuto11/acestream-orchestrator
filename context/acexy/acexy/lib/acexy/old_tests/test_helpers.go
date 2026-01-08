// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package acexy

import (
	"fmt"
	"io"
)

// mockWriter is a test helper that implements io.Writer for testing purposes
type mockWriter struct {
	data []byte
}

func (m *mockWriter) Write(p []byte) (n int, err error) {
	m.data = append(m.data, p...)
	return len(p), nil
}

// Ensure mockWriter implements io.Writer
var _ io.Writer = (*mockWriter)(nil)

// parseInt is a helper function to parse port strings in tests
func parseInt(port string) int {
	var portInt int
	fmt.Sscanf(port, "%d", &portInt)
	return portInt
}
