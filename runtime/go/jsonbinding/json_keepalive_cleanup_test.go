package jsonbinding

import (
	"errors"
	"runtime"
	"testing"
)

func TestReleaseIdempotencyAndZeroGuard(t *testing.T) {
	var zero Value
	zero.Release()
	Value{h: &handle{}}.Release()

	v, err := Parse([]byte(`{"k": 7, "s": "ok", "a": [1, 2]}`))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if v.h.id == 0 {
		t.Fatal("expected nonzero handle id after Parse")
	}

	v.Release()
	if v.h.id != 0 {
		t.Fatalf("id after Release = %d, want 0", v.h.id)
	}
	v.Release()

	if _, err := v.Int("k"); !errors.Is(err, ErrJSON) {
		t.Fatalf("Int after Release = %v, want ErrJSON", err)
	}
	if _, err := v.String("s"); !errors.Is(err, ErrJSON) {
		t.Fatalf("String after Release = %v, want ErrJSON", err)
	}
	if _, err := v.Array("a"); !errors.Is(err, ErrJSON) {
		t.Fatalf("Array after Release = %v, want ErrJSON", err)
	}
	if err := v.PushString("x"); !errors.Is(err, ErrJSON) {
		t.Fatalf("PushString after Release = %v, want ErrJSON", err)
	}
	if err := v.Set("k", int64(1)); !errors.Is(err, ErrJSON) {
		t.Fatalf("Set after Release = %v, want ErrJSON", err)
	}
	if _, err := ToStringPretty(v); !errors.Is(err, ErrJSON) {
		t.Fatalf("ToStringPretty after Release = %v, want ErrJSON", err)
	}

	reused, err := Parse([]byte(`{"k": 99}`))
	if err != nil {
		t.Fatalf("Parse(reused): %v", err)
	}
	defer reused.Release()
	for i := 0; i < 6; i++ {
		runtime.GC()
		runtime.Gosched()
	}
	if got, err := reused.Int("k"); err != nil || got != 99 {
		t.Fatalf("reused.Int(k) after GC = %d, %v (want 99, nil)", got, err)
	}
}

func TestKeepAliveAcrossLastReferenceAccessors(t *testing.T) {
	for i := 0; i < 16; i++ {
		v, err := Parse([]byte(`{"n": 42, "s": "keepalive", "arr": ["x", "y"]}`))
		if err != nil {
			t.Fatalf("iter %d Parse: %v", i, err)
		}
		runtime.GC()
		if n, err := v.Int("n"); err != nil || n != 42 {
			t.Fatalf("iter %d Int: %d, %v", i, n, err)
		}
		runtime.GC()
		if s, err := v.String("s"); err != nil || s != "keepalive" {
			t.Fatalf("iter %d String: %q, %v", i, s, err)
		}
		runtime.GC()
		elems, err := v.Array("arr")
		if err != nil || len(elems) != 2 {
			t.Fatalf("iter %d Array: len=%d, err=%v", i, len(elems), err)
		}
		for _, el := range elems {
			el.Release()
		}

		arr := NewArray()
		if err := arr.PushString("item"); err != nil {
			t.Fatalf("iter %d PushString: %v", i, err)
		}
		runtime.GC()
		if pretty, err := ToStringPretty(arr); err != nil || pretty != "[\n  \"item\"\n]" {
			t.Fatalf("iter %d ToStringPretty: %q, %v", i, pretty, err)
		}
	}
}
