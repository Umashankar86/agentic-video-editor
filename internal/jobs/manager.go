package jobs

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var ErrQueueFull = errors.New("job queue is full")

type AgentExecutor interface {
	RunAgent(context.Context, AgentRequest) (AgentResult, error)
}

type AgentRequest struct {
	JobID  string   `json:"job_id"`
	Prompt string   `json:"prompt"`
	Inputs []string `json:"inputs"`
	Output string   `json:"output"`
}

type AgentResult struct {
	Plan     Plan              `json:"plan"`
	Results  []OperationResult `json:"results"`
	Output   string            `json:"output"`
	Metadata map[string]any    `json:"metadata,omitempty"`
}

// Manager owns the asynchronous pipeline. A buffered channel is the queue;
// each consumer is a goroutine, making concurrency bounded and observable.
type Manager struct {
	store       *Store
	executor    AgentExecutor
	queue       chan string
	workerCount int
	wg          sync.WaitGroup
}

func NewManager(store *Store, executor AgentExecutor, workerCount, queueSize int) *Manager {
	return &Manager{
		store: store, executor: executor,
		queue: make(chan string, queueSize), workerCount: workerCount,
	}
}

func (m *Manager) Start(ctx context.Context) {
	for i := 1; i <= m.workerCount; i++ {
		m.wg.Add(1)
		go m.consume(ctx, i)
	}
}

func (m *Manager) Wait() { m.wg.Wait() }

func (m *Manager) Submit(request CreateRequest) (Job, error) {
	id, err := newID()
	if err != nil {
		return Job{}, err
	}
	if strings.TrimSpace(request.Prompt) == "" || len(request.Inputs) == 0 {
		return Job{}, errors.New("prompt and at least one input are required")
	}
	now := time.Now().UTC()
	job := Job{
		ID: id, Prompt: strings.TrimSpace(request.Prompt), Inputs: request.Inputs,
		RequestedOutput: sanitizeOutput(request.Output, id),
		Plan:            Plan{Version: "agentic-1.0", Summary: "Awaiting OpenRouter supervisor plan"},
		Status:          StatusQueued, Progress: 0, CreatedAt: now, UpdatedAt: now,
	}

	m.store.Create(job)
	select {
	case m.queue <- id:
		return job, nil
	default:
		m.store.Delete(id)
		return Job{}, ErrQueueFull
	}
}

func (m *Manager) consume(ctx context.Context, workerNumber int) {
	defer m.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case id := <-m.queue:
			slog.Info("goroutine picked up agentic job", "worker", workerNumber, "job_id", id)
			m.execute(ctx, id)
		}
	}
}

func (m *Manager) execute(ctx context.Context, id string) {
	job, err := m.store.Get(id)
	if err != nil {
		slog.Error("queued job disappeared", "job_id", id, "error", err)
		return
	}
	started := time.Now().UTC()
	_ = m.store.Update(id, func(job *Job) {
		job.Status = StatusRunning
		job.StartedAt = &started
		job.Progress = 5
		job.CurrentOperation = "video_understanding_and_planning"
	})

	artifacts := make([]string, len(job.Inputs))
	for i, input := range job.Inputs {
		artifacts[i] = filepath.ToSlash(filepath.Join("uploads", filepath.Base(input)))
	}
	result, err := m.executor.RunAgent(ctx, AgentRequest{
		JobID: job.ID, Prompt: job.Prompt, Inputs: artifacts, Output: job.RequestedOutput,
	})
	if err != nil {
		m.fail(id, "LangGraph agent pipeline", err)
		return
	}
	completed := time.Now().UTC()
	_ = m.store.Update(id, func(job *Job) {
		job.Plan = result.Plan
		job.Results = result.Results
		job.Metadata = result.Metadata
		job.Output = result.Output
		job.Status = StatusCompleted
		job.Progress = 100
		job.CurrentOperation = ""
		job.CompletedAt = &completed
	})
}

func (m *Manager) fail(id, operation string, err error) {
	completed := time.Now().UTC()
	_ = m.store.Update(id, func(job *Job) {
		job.Status = StatusFailed
		job.Error = fmt.Sprintf("%s failed: %v", operation, err)
		job.CurrentOperation = ""
		job.CompletedAt = &completed
	})
	slog.Error("job failed", "job_id", id, "operation", operation, "error", err)
}

func newID() (string, error) {
	buffer := make([]byte, 8)
	if _, err := rand.Read(buffer); err != nil {
		return "", fmt.Errorf("create job id: %w", err)
	}
	return hex.EncodeToString(buffer), nil
}

func sanitizeOutput(name, id string) string {
	name = filepath.Base(strings.TrimSpace(name))
	if name == "." || name == "" {
		return id + ".mp4"
	}
	ext := strings.ToLower(filepath.Ext(name))
	switch ext {
	case ".mp4", ".mov", ".mkv", ".webm":
		return name
	default:
		return name + ".mp4"
	}
}
