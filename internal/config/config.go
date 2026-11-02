package config

import (
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type Config struct {
	Address       string
	MediaRoot     string
	UploadsDir    string
	WorkDir       string
	OutputsDir    string
	WorkerURL     string
	JobWorkers    int
	QueueSize     int
	MaxUploadSize int64
	HTTPTimeout   time.Duration
}

func Load() Config {
	root := getenv("MEDIA_ROOT", ".")
	return Config{
		Address:       ":" + getenv("PORT", "8080"),
		MediaRoot:     root,
		UploadsDir:    filepath.Join(root, "uploads"),
		WorkDir:       filepath.Join(root, "work"),
		OutputsDir:    filepath.Join(root, "outputs"),
		WorkerURL:     getenv("WORKER_URL", "http://localhost:8000"),
		JobWorkers:    getenvInt("JOB_WORKERS", 2),
		QueueSize:     getenvInt("JOB_QUEUE_SIZE", 100),
		MaxUploadSize: int64(getenvInt("MAX_UPLOAD_MB", 1024)) << 20,
		HTTPTimeout:   time.Duration(getenvInt("HTTP_TIMEOUT_MINUTES", 60)) * time.Minute,
	}
}

func (c Config) EnsureDirectories() error {
	for _, dir := range []string{c.UploadsDir, c.WorkDir, c.OutputsDir} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	return nil
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func getenvInt(key string, fallback int) int {
	value, err := strconv.Atoi(os.Getenv(key))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}
