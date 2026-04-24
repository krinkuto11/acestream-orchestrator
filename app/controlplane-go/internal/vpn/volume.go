package vpn

import (
	"archive/tar"
	"bytes"
	"context"
	"io"
	"log/slog"
	"os"
	"time"

	dockerclient "github.com/docker/docker/client"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/api/types/volume"
)

const (
	GluetunVolumeName = "acestream-gluetun-servers"
	helperImage       = "qmcgaw/gluetun"
)

// SyncServersToVolume copies servers.json from srcPath into the shared Docker
// named volume so newly-provisioned Gluetun containers receive the up-to-date
// catalog on startup.
//
// It creates the volume if needed, spins up a dormant helper container, and
// uses put_archive to write the file — no host paths required.
func SyncServersToVolume(ctx context.Context, srcPath string) error {
	data, err := os.ReadFile(srcPath)
	if err != nil {
		return err
	}

	tarData := makeTar("servers.json", data)

	cli, err := dockerclient.NewClientWithOpts(
		dockerclient.FromEnv,
		dockerclient.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return err
	}
	defer cli.Close()

	if err := ensureVolume(ctx, cli); err != nil {
		return err
	}

	// Create a dormant helper container that mounts the volume.
	// ContainerCreate works on stopped containers; we never start it.
	resp, err := cli.ContainerCreate(ctx,
		&container.Config{Image: helperImage},
		&container.HostConfig{
			Binds: []string{GluetunVolumeName + ":/gluetun-data:rw"},
		},
		nil, nil, "",
	)
	if err != nil {
		return err
	}

	containerID := resp.ID
	defer func() {
		rmCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = cli.ContainerRemove(rmCtx, containerID, container.RemoveOptions{Force: true})
	}()

	if err := cli.CopyToContainer(ctx, containerID, "/gluetun-data", bytes.NewReader(tarData), container.CopyToContainerOptions{}); err != nil {
		return err
	}

	slog.Info("Synced servers.json to Docker volume",
		"volume", GluetunVolumeName,
		"bytes", len(data),
	)
	return nil
}

// ensureVolume creates the named volume if it does not already exist.
func ensureVolume(ctx context.Context, cli *dockerclient.Client) error {
	vols, err := cli.VolumeList(ctx, volume.ListOptions{
		Filters: filters.NewArgs(filters.Arg("name", GluetunVolumeName)),
	})
	if err != nil {
		return err
	}
	for _, v := range vols.Volumes {
		if v.Name == GluetunVolumeName {
			return nil
		}
	}
	_, err = cli.VolumeCreate(ctx, volume.CreateOptions{
		Name:   GluetunVolumeName,
		Driver: "local",
	})
	if err != nil {
		return err
	}
	slog.Info("Created Docker volume", "name", GluetunVolumeName)
	return nil
}

// makeTar packs content into an in-memory tar archive named filename.
func makeTar(filename string, content []byte) []byte {
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	hdr := &tar.Header{
		Name:    filename,
		Size:    int64(len(content)),
		Mode:    0o644,
		ModTime: time.Now(),
	}
	_ = tw.WriteHeader(hdr)
	_, _ = io.Copy(tw, bytes.NewReader(content))
	_ = tw.Close()
	return buf.Bytes()
}
