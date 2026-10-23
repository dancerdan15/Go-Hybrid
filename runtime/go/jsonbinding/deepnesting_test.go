package jsonbinding

// Row 4 (per-callee stack budgets) soundness regression tests.
//
// serde_json (pinned 1.0.140) RECURSES on value nesting: from_str with
// remaining_depth = 128 (measured ceiling: depth 127 parses, 128 fails),
// and clone/drop/serialize walk the tree. The class ledger (json.go header
// + interfaces/descriptors.md) routes every fn that can touch a nested
// value through the DEEP pool; these tests pin that routing with
// DeepCrossCount as the oracle (DeepPathCount only sees fast-path RETRIES —
// the always-deep route never touches the fast trampoline), and exercise
// the depth ceiling end-to-end.

import (
	"errors"
	"strings"
	"testing"

	"hybrid.program/hybridge"
)

// maxDepth is serde_json's measured nesting ceiling at the pinned version:
// 127 parses, 128 fails (the probe that pinned this lives in the worklog).
const maxDepth = 127

func deepArray(n int) []byte {
	return []byte(strings.Repeat("[", n) + strings.Repeat("]", n))
}

// TestSerdeDepthCeiling: the ceiling is a serde_json fact, not our choice —
// document it as a test so a serde_json bump that changes the limit shows up
// as a diff here, next to the class ledger that cites the number.
func TestSerdeDepthCeiling(t *testing.T) {
	if _, err := Parse(deepArray(maxDepth)); err != nil {
		t.Fatalf("depth %d must parse (measured ceiling): %v", maxDepth, err)
	}
	if _, err := Parse(deepArray(maxDepth + 1)); err == nil {
		t.Fatalf("depth %d must FAIL (serde_json recursion limit) — if this passes, the pinned serde_json changed its remaining_depth; update the class ledger's citation",
			maxDepth+1)
	}
}

// TestDeepNestingParsesViaDeepPath: a ceiling-depth parse must (a) succeed
// and (b) take the ALWAYS-deep pool. Pre-row-4 the parse crossed via the
// default 4096-guard fast path — unsound for exactly this input shape on
// shallow stacks (127 deep × serde's parse frames ≫ 4096 B).
func TestDeepNestingParsesViaDeepPath(t *testing.T) {
	before := hybridge.DeepCrossCount.Load()
	v, err := Parse(deepArray(maxDepth))
	if err != nil {
		t.Fatalf("Parse(%d-deep) failed: %v", maxDepth, err)
	}
	if after := hybridge.DeepCrossCount.Load(); after == before {
		t.Fatal("Parse did not take the ALWAYS-deep route — the class ledger regressed (json.go routes Parse through CrossDeep3)")
	}

	s, err := ToStringPretty(v)
	if err != nil {
		t.Fatalf("ToStringPretty(%d-deep) failed: %v", maxDepth, err)
	}
	// Pretty output is LONGER than the compact form (newlines + per-level
	// indentation); the exact length is formatter detail, not contract.
	if len(s) < 2*maxDepth {
		t.Fatalf("round-trip length = %d, want >= %d (compact form)", len(s), 2*maxDepth)
	}
	// Explicit release through the DEEP route (the leak canary asserts zero
	// live objects across the suite; zeroing the id alone would leak this one).
	v.Release()
}

// TestDeepNestingOnConstrainedStack: the same worst case, executed on a
// goroutine whose stack has been padded so the remaining headroom is small —
// the shape where a fast-path parse of a deep value would overflow. The
// DEEP-class routes (1 MiB pooled stacks) are the fix; success here is the
// property, DeepCrossCount the mechanism's proof.
func TestDeepNestingOnConstrainedStack(t *testing.T) {
	done := make(chan error, 1)
	go func() {
		// Pad the goroutine's stack so the remaining headroom is small (the
		// 127-deep parse's worst-case frame total would not fit in what a
		// guarded fast path could prove — the DEEP pool is where this work
		// belongs, and the ledger routes it there by RULE).
		var pad [1024]byte
		var grow func(depth int) byte
		grow = func(depth int) byte {
			if depth == 0 {
				return pad[0]
			}
			pad[depth%1024]++
			return grow(depth-1) + pad[(depth+1)%1024]
		}
		_ = grow(30) // ~30 KiB of Go frames

		before := hybridge.DeepCrossCount.Load()
		v, err := Parse(deepArray(maxDepth))
		if err != nil {
			done <- errors.New("step parse: " + err.Error())
			return
		}
		if hybridge.DeepCrossCount.Load() == before {
			done <- errors.New("step parse: the ALWAYS-deep route did not engage on the constrained goroutine")
			return
		}
		if _, err := ToStringPretty(v); err != nil {
			done <- errors.New("step pretty: " + err.Error())
			return
		}
		arr, err := v.Array("nope") // absent key — error path, no crash
		if err == nil || arr != nil {
			done <- errors.New("absent key must error")
			return
		}
		v.Release()
		done <- nil
	}()
	if err := <-done; err != nil {
		t.Fatalf("deep-nesting work on a constrained stack failed: %v", err)
	}
}
