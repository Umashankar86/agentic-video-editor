package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"agentic-video-editor/internal/jobs"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(baseURL string, timeout time.Duration) *Client {
	return &Client{baseURL: strings.TrimRight(baseURL, "/"), http: &http.Client{Timeout: timeout}}
}

func (c *Client) RunAgent(ctx context.Context, agentRequest jobs.AgentRequest) (jobs.AgentResult, error) {
	body, err := json.Marshal(agentRequest)
	if err != nil {
		return jobs.AgentResult{}, fmt.Errorf("encode agent request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/agent/run", bytes.NewReader(body))
	if err != nil {
		return jobs.AgentResult{}, fmt.Errorf("create agent request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(req)
	if err != nil {
		return jobs.AgentResult{}, fmt.Errorf("call agent worker: %w", err)
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return jobs.AgentResult{}, fmt.Errorf("read agent response: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return jobs.AgentResult{}, fmt.Errorf("agent worker returned %s: %s", response.Status, strings.TrimSpace(string(payload)))
	}
	var result jobs.AgentResult
	if err := json.Unmarshal(payload, &result); err != nil {
		return jobs.AgentResult{}, fmt.Errorf("decode agent response: %w", err)
	}
	if result.Output == "" {
		return jobs.AgentResult{}, fmt.Errorf("agent worker returned an empty output")
	}
	return result, nil
}

func (c *Client) Health(ctx context.Context) error {
	healthCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(healthCtx, http.MethodGet, c.baseURL+"/healthz", nil)
	if err != nil {
		return err
	}
	response, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("worker health returned %s", response.Status)
	}
	return nil
}

