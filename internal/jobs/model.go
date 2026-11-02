package jobs

import "time"

type Status string

const (
	StatusQueued    Status = "queued"
	StatusRunning   Status = "running"
	StatusCompleted Status = "completed"
	StatusFailed    Status = "failed"
)

type Operation struct {
	ID     string         `json:"id"`
	Type   string         `json:"type"`
	Params map[string]any `json:"params,omitempty"`
}

type Plan struct {
	Version    string      `json:"version"`
	Summary    string      `json:"summary"`
	Operations []Operation `json:"operations"`
}

type OperationResult struct {
	OperationID string         `json:"operation_id"`
	Type        string         `json:"type"`
	Artifact    string         `json:"artifact,omitempty"`
	DurationMS  int64          `json:"duration_ms"`
	Metadata    map[string]any `json:"metadata,omitempty"`
}

type Job struct {
	ID               string            `json:"id"`
	Prompt           string            `json:"prompt"`
	Inputs           []string          `json:"inputs"`
	RequestedOutput  string            `json:"requested_output"`
	Plan             Plan              `json:"plan"`
	Status           Status            `json:"status"`
	Progress         int               `json:"progress"`
	CurrentOperation string            `json:"current_operation,omitempty"`
	Results          []OperationResult `json:"results,omitempty"`
	Metadata         map[string]any    `json:"metadata,omitempty"`
	Output           string            `json:"output,omitempty"`
	Error            string            `json:"error,omitempty"`
	CreatedAt        time.Time         `json:"created_at"`
	UpdatedAt        time.Time         `json:"updated_at"`
	StartedAt        *time.Time        `json:"started_at,omitempty"`
	CompletedAt      *time.Time        `json:"completed_at,omitempty"`
}

type CreateRequest struct {
	Prompt string   `json:"prompt"`
	Inputs []string `json:"inputs"`
	Output string   `json:"output,omitempty"`
}
