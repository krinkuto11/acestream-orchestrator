// Helper utility to help finding the AceStream ID

package acexy

import (
	"errors"
	"fmt"
)

type AceID struct {
	id       string
	infohash string
}

// Type referencing which ID is set
type AceIDType string

// Create a new `AceID` object
func NewAceID(id, infohash string) (AceID, error) {
	if id == "" && infohash == "" {
		return AceID{}, errors.New("one of `id` or `infohash` must have a value")
	}
	if id != "" && infohash != "" {
		return AceID{}, errors.New("only one of `id` or `infohash` can have a value")
	}
	return AceID{id: id, infohash: infohash}, nil
}

// Get the valid AceStream ID. If the `infohash` is set, it will be returned,
// otherwise the `id`.
func (a AceID) ID() (AceIDType, string) {
	if a.infohash != "" {
		return "infohash", a.infohash
	}
	return "id", a.id
}

// Get the AceStream ID as a string
func (a AceID) String() string {
	idType, id := a.ID()
	return fmt.Sprintf("{%s: %s}", idType, id)
}
