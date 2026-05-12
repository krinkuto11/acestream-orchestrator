package docker

import (
	"bufio"
	"context"
	"encoding/binary"
	"io"
	"strconv"
	"strings"

	"github.com/docker/docker/api/types/container"

	"github.com/acestream/acestream/internal/controlplane/engine"
)

// GetContainerLogs fetches the tail of logs from a container.
// tail: number of lines ("100", "all", etc.)
// sinceUnix: if > 0, only return logs after this Unix timestamp
func GetContainerLogs(ctx context.Context, containerID, tail string, timestamps bool, sinceUnix int64) ([]string, error) {
	cli, err := engine.NewDockerClientExported()
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	if tail == "" {
		tail = "100"
	}

	opts := container.LogsOptions{
		ShowStdout: true,
		ShowStderr: true,
		Tail:       tail,
		Timestamps: timestamps,
	}
	if sinceUnix > 0 {
		opts.Since = strconv.FormatInt(sinceUnix, 10)
	}

	rc, err := cli.ContainerLogs(ctx, containerID, opts)
	if err != nil {
		return nil, err
	}
	defer rc.Close()

	return parseMuxedLogs(rc)
}

// parseMuxedLogs reads Docker's multiplexed log stream (8-byte header per frame).
func parseMuxedLogs(r io.Reader) ([]string, error) {
	var lines []string
	hdr := make([]byte, 8)
	for {
		_, err := io.ReadFull(r, hdr)
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			break
		}
		if err != nil {
			return lines, err
		}
		size := binary.BigEndian.Uint32(hdr[4:8])
		if size == 0 {
			continue
		}
		buf := make([]byte, size)
		if _, err := io.ReadFull(r, buf); err != nil {
			return lines, err
		}
		scanner := bufio.NewScanner(strings.NewReader(string(buf)))
		for scanner.Scan() {
			lines = append(lines, scanner.Text())
		}
	}
	return lines, nil
}
