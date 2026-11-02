package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"agentic-video-editor/internal/api"
	"agentic-video-editor/internal/config"
	"agentic-video-editor/internal/jobs"
	"agentic-video-editor/internal/worker"
)

func main() {
	cfg := config.Load()
	if err := cfg.EnsureDirectories(); err != nil {
		slog.Error("create media directories", "error", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	store := jobs.NewStore()
	mediaWorker := worker.NewClient(cfg.WorkerURL, cfg.HTTPTimeout)
	manager := jobs.NewManager(store, mediaWorker, cfg.JobWorkers, cfg.QueueSize)
	manager.Start(ctx)

	server := api.New(cfg, manager, store, mediaWorker)
	httpServer := &http.Server{
		Addr:              cfg.Address,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go func() {
		slog.Info("Go orchestrator listening", "address", cfg.Address, "goroutines", cfg.JobWorkers, "queue_capacity", cfg.QueueSize)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("serve API", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		slog.Error("shutdown API", "error", err)
	}
	manager.Wait()
	slog.Info("orchestrator stopped")
}
