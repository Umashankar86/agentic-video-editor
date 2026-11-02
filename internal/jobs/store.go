package jobs

import (
	"errors"
	"sort"
	"sync"
	"time"
)

var ErrNotFound = errors.New("job not found")

type Store struct {
	mu   sync.RWMutex
	jobs map[string]Job
}

func NewStore() *Store {
	return &Store{jobs: make(map[string]Job)}
}

func (s *Store) Create(job Job) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.jobs[job.ID] = cloneJob(job)
}

func (s *Store) Delete(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.jobs, id)
}

func (s *Store) Get(id string) (Job, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	job, ok := s.jobs[id]
	if !ok {
		return Job{}, ErrNotFound
	}
	return cloneJob(job), nil
}

func (s *Store) List() []Job {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]Job, 0, len(s.jobs))
	for _, job := range s.jobs {
		result = append(result, cloneJob(job))
	}
	sort.Slice(result, func(i, j int) bool {
		return result[i].CreatedAt.After(result[j].CreatedAt)
	})
	return result
}

func (s *Store) Update(id string, update func(*Job)) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	job, ok := s.jobs[id]
	if !ok {
		return ErrNotFound
	}
	update(&job)
	job.UpdatedAt = time.Now().UTC()
	s.jobs[id] = cloneJob(job)
	return nil
}

func cloneJob(job Job) Job {
	job.Inputs = append([]string(nil), job.Inputs...)
	job.Plan.Operations = append([]Operation(nil), job.Plan.Operations...)
	job.Results = append([]OperationResult(nil), job.Results...)
	if job.Metadata != nil {
		job.Metadata = cloneMap(job.Metadata)
	}
	return job
}

func cloneMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}
