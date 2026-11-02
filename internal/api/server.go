package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"agentic-video-editor/internal/config"
	"agentic-video-editor/internal/jobs"
	"agentic-video-editor/internal/worker"
)

type Server struct {
	config  config.Config
	manager *jobs.Manager
	store   *jobs.Store
	worker  *worker.Client
	mux     *http.ServeMux
}

func New(cfg config.Config, manager *jobs.Manager, store *jobs.Store, mediaWorker *worker.Client) *Server {
	s := &Server{config: cfg, manager: manager, store: store, worker: mediaWorker, mux: http.NewServeMux()}
	s.routes()
	return s
}

func (s *Server) Handler() http.Handler {
	return requestLogger(s.mux)
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("POST /api/v1/uploads", s.upload)
	s.mux.HandleFunc("POST /api/v1/jobs", s.createJob)
	s.mux.HandleFunc("GET /api/v1/jobs", s.listJobs)
	s.mux.HandleFunc("GET /api/v1/jobs/{id}", s.getJob)
	s.mux.Handle("GET /api/v1/outputs/", http.StripPrefix("/api/v1/outputs/", http.FileServer(http.Dir(s.config.OutputsDir))))
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	workerStatus := "ok"
	if err := s.worker.Health(r.Context()); err != nil {
		workerStatus = "unavailable: " + err.Error()
	}
	status := http.StatusOK
	if workerStatus != "ok" {
		status = http.StatusServiceUnavailable
	}
	writeJSON(w, status, map[string]any{"status": map[bool]string{true: "ok", false: "degraded"}[status == http.StatusOK], "worker": workerStatus})
}

func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, s.config.MaxUploadSize)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "invalid multipart upload: "+err.Error())
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		writeError(w, http.StatusBadRequest, "multipart field 'file' is required")
		return
	}
	defer file.Close()
	name := safeMediaName(header.Filename)
	if name == "" {
		writeError(w, http.StatusBadRequest, "unsupported or invalid filename")
		return
	}
	destination := filepath.Join(s.config.UploadsDir, name)
	output, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if errors.Is(err, os.ErrExist) {
		writeError(w, http.StatusConflict, "a file with this name already exists")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not create upload")
		return
	}
	written, copyErr := copyUpload(output, file)
	closeErr := output.Close()
	if copyErr != nil || closeErr != nil {
		_ = os.Remove(destination)
		writeError(w, http.StatusInternalServerError, "could not save upload")
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"filename": name, "size": written})
}

func (s *Server) createJob(w http.ResponseWriter, r *http.Request) {
	var request jobs.CreateRequest
	decoder := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON: "+err.Error())
		return
	}
	if strings.TrimSpace(request.Prompt) == "" || len(request.Inputs) == 0 {
		writeError(w, http.StatusBadRequest, "prompt and at least one input are required")
		return
	}
	for i, input := range request.Inputs {
		name := filepath.Base(strings.TrimSpace(input))
		if name != input || safeMediaName(name) == "" {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("input %d has an invalid filename", i))
			return
		}
		info, err := os.Stat(filepath.Join(s.config.UploadsDir, name))
		if err != nil || info.IsDir() {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("input %q was not uploaded", name))
			return
		}
	}
	job, err := s.manager.Submit(request)
	if errors.Is(err, jobs.ErrQueueFull) {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	w.Header().Set("Location", "/api/v1/jobs/"+job.ID)
	writeJSON(w, http.StatusAccepted, job)
}

func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	job, err := s.store.Get(r.PathValue("id"))
	if errors.Is(err, jobs.ErrNotFound) {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, job)
}

func (s *Server) listJobs(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"jobs": s.store.List()})
}

func safeMediaName(name string) string {
	name = strings.TrimSpace(name)
	if name == "" || filepath.Base(name) != name || strings.ContainsAny(name, `/\\`) {
		return ""
	}
	switch strings.ToLower(filepath.Ext(name)) {
	case ".mp4", ".mov", ".mkv", ".webm", ".avi", ".srt":
		return name
	default:
		return ""
	}
}

func copyUpload(destination *os.File, source multipart.File) (int64, error) {
	return io.Copy(destination, source)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		slog.Error("write JSON response", "error", err)
	}
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func requestLogger(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		next.ServeHTTP(w, r)
		slog.Info("request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(started).Milliseconds())
	})
}
