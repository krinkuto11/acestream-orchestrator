package state

import (
	"fmt"
	"strings"
	"sync"
	"time"
)

const maxEventLogSize = 500

// EventEntry is a minimal event record for UI consumption.
type EventEntry struct {
	ID          string         `json:"id"`
	EventType   string         `json:"event_type"`
	Category    string         `json:"category"`
	Message     string         `json:"message"`
	Timestamp   time.Time      `json:"timestamp"`
	Details     map[string]any `json:"details,omitempty"`
	ContainerID string         `json:"container_id,omitempty"`
	StreamID    string         `json:"stream_id,omitempty"`
}

type eventLog struct {
	mu     sync.RWMutex
	seq    uint64
	events []EventEntry
}

var globalEventLog = &eventLog{events: []EventEntry{}}

// RecordEvent appends a new event to the shared log.
func RecordEvent(entry EventEntry) {
	globalEventLog.record(entry)
}

// GetEventsSnapshot returns filtered events and derived stats.
func GetEventsSnapshot(limit int, eventType string) (events []EventEntry, stats map[string]any) {
	return globalEventLog.snapshot(limit, eventType)
}

// ClearEvents purges the in-memory event log.
func ClearEvents() {
	globalEventLog.clear()
}

func (l *eventLog) record(entry EventEntry) {
	if entry.EventType == "" {
		entry.EventType = "system"
	}
	if entry.Category == "" {
		entry.Category = "info"
	}
	if entry.Timestamp.IsZero() {
		entry.Timestamp = time.Now().UTC()
	}

	l.mu.Lock()
	l.seq++
	entry.ID = fmt.Sprintf("ev_%d", l.seq)
	l.events = append(l.events, entry)
	if len(l.events) > maxEventLogSize {
		l.events = l.events[len(l.events)-maxEventLogSize:]
	}
	l.mu.Unlock()
}

func (l *eventLog) clear() {
	l.mu.Lock()
	l.events = nil
	l.mu.Unlock()
}

func (l *eventLog) snapshot(limit int, eventType string) (events []EventEntry, stats map[string]any) {
	if limit <= 0 {
		limit = 100
	}
	filter := strings.ToLower(strings.TrimSpace(eventType))
	if filter == "all" {
		filter = ""
	}

	l.mu.RLock()
	cached := append([]EventEntry(nil), l.events...)
	l.mu.RUnlock()

	byType := map[string]int{}
	for _, entry := range cached {
		byType[entry.EventType]++
	}

	stats = map[string]any{
		"total":   len(cached),
		"by_type": byType,
	}

	for i := len(cached) - 1; i >= 0; i-- {
		entry := cached[i]
		if filter != "" && entry.EventType != filter {
			continue
		}
		events = append(events, entry)
		if len(events) >= limit {
			break
		}
	}

	return events, stats
}
