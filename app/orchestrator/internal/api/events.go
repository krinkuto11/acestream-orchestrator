package api

import (
	"fmt"
	"strings"
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

func (s *ProxyServer) recordEvent(entry EventEntry) {
	if entry.EventType == "" {
		entry.EventType = "system"
	}
	if entry.Category == "" {
		entry.Category = "info"
	}
	if entry.Timestamp.IsZero() {
		entry.Timestamp = time.Now().UTC()
	}

	s.eventMu.Lock()
	s.eventSeq++
	entry.ID = fmt.Sprintf("ev_%d", s.eventSeq)
	s.events = append(s.events, entry)
	if len(s.events) > maxEventLogSize {
		s.events = s.events[len(s.events)-maxEventLogSize:]
	}
	s.eventMu.Unlock()
}

func (s *ProxyServer) clearEvents() {
	s.eventMu.Lock()
	s.events = nil
	s.eventMu.Unlock()
}

func (s *ProxyServer) getEventsSnapshot(limit int, eventType string) (events []EventEntry, stats map[string]any) {
	if limit <= 0 {
		limit = 100
	}
	filter := strings.ToLower(strings.TrimSpace(eventType))
	if filter == "all" {
		filter = ""
	}

	s.eventMu.RLock()
	cached := append([]EventEntry(nil), s.events...)
	s.eventMu.RUnlock()

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
