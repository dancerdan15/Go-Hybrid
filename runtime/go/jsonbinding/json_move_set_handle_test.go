package jsonbinding

import (
	"errors"
	"runtime"
	"testing"
)

func moveChildIntoParent(t *testing.T, parent Value) uint64 {
	t.Helper()
	child, err := Parse([]byte(`{"n": 42}`))
	if err != nil {
		t.Fatalf("Parse(child): %v", err)
	}
	movedID := child.h.id
	if movedID == 0 {
		t.Fatal("expected nonzero child handle id before move")
	}
	if err := parent.Set("child", child); err != nil {
		t.Fatalf("parent.Set(child): %v", err)
	}
	if child.h.id != 0 {
		t.Fatalf("moved child handle id = %d, want 0", child.h.id)
	}
	return movedID
}

func TestMovedSetHandleStopsCleanupOnSlotReuse(t *testing.T) {
	for i := 0; i < 6; i++ {
		runtime.GC()
		runtime.Gosched()
	}
	parent := NewObject()
	defer parent.Release()

	movedID := moveChildIntoParent(t, parent)
	reused, err := Parse([]byte(`{"live": 123, "tag": "kept"}`))
	if err != nil {
		t.Fatalf("Parse(reused): %v", err)
	}
	defer reused.Release()

	extra := NewObject()
	defer extra.Release()
	if err := extra.Set("k", int64(7)); err != nil {
		t.Fatalf("extra.Set(k): %v", err)
	}
	if reused.h.id != movedID && extra.h.id != movedID {
		t.Fatalf("neither reused (%d) nor extra (%d) recycled moved slot %d", reused.h.id, extra.h.id, movedID)
	}

	for i := 0; i < 8; i++ {
		runtime.GC()
		runtime.Gosched()
	}
	if got, err := reused.Int("live"); err != nil || got != 123 {
		t.Fatalf("reused.Int(live) after GC = %d, %v (want 123, nil)", got, err)
	}
	if tag, err := reused.String("tag"); err != nil || tag != "kept" {
		t.Fatalf("reused.String(tag) after GC = %q, %v (want \"kept\", nil)", tag, err)
	}
	if got, err := extra.Int("k"); err != nil || got != 7 {
		t.Fatalf("extra.Int(k) after GC = %d, %v (want 7, nil)", got, err)
	}
	wantParent := "{\n  \"child\": {\n    \"n\": 42\n  }\n}"
	if pretty, err := ToStringPretty(parent); err != nil || pretty != wantParent {
		t.Fatalf("ToStringPretty(parent) = %q, %v (want %q)", pretty, err, wantParent)
	}
}

func TestScalarSetAndFailedSetStopCleanupOnSlotReuse(t *testing.T) {
	doc := NewObject()
	defer doc.Release()
	for i := 0; i < 8; i++ {
		if err := doc.Set("s", "boxed"); err != nil {
			t.Fatalf("doc.Set(s): %v", err)
		}
		if err := doc.Set("i", int64(i)); err != nil {
			t.Fatalf("doc.Set(i): %v", err)
		}
	}
	arr := NewArray()
	defer arr.Release()
	if err := arr.Set("bad", "scalar"); !errors.Is(err, ErrJSON) {
		t.Fatalf("Array.Set(scalar) = %v, want ErrJSON", err)
	}
	survivor := NewObject()
	defer survivor.Release()
	if err := survivor.Set("ok", "yes"); err != nil {
		t.Fatalf("survivor.Set: %v", err)
	}
	for i := 0; i < 8; i++ {
		runtime.GC()
		runtime.Gosched()
	}
	if got, err := survivor.String("ok"); err != nil || got != "yes" {
		t.Fatalf("survivor.String(ok) = %q, %v (want \"yes\", nil)", got, err)
	}
	if got, err := doc.Int("i"); err != nil || got != 7 {
		t.Fatalf("doc.Int(i) = %d, %v (want 7, nil)", got, err)
	}
}
