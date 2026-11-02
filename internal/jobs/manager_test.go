package jobs

import (
	"context"
	"testing"
	"time"
)

type fakeExecutor struct{}

func (fakeExecutor) RunAgent(context.Context, AgentRequest) (AgentResult, error) {
	return AgentResult{Plan: Plan{Version: "agentic-1.0", Summary: "AI plan"}, Output: "demo.mp4"}, nil
}

func TestManagerRunsQueuedJob(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	store := NewStore()
	manager := NewManager(store, fakeExecutor{}, 1, 1)
	manager.Start(ctx)
	job, err := manager.Submit(CreateRequest{Prompt: "export", Inputs: []string{"in.mp4"}, Output: "demo.mp4"})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.After(2 * time.Second)
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-deadline:
			cancel()
			manager.Wait()
			t.Fatal("job did not complete")
		case <-ticker.C:
		}
		got, _ := store.Get(job.ID)
		if got.Status == StatusCompleted {
			cancel()
			manager.Wait()
			return
		}
	}
}
