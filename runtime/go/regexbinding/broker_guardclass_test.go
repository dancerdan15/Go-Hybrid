package regexbinding

import (
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"runtime"
	"testing"
	"unsafe"
)

// TestBrokerInitGuardClassContract ensures broker.go's package init probes
// FnAbiWord through the SMALL-class trampoline (Cross1S), matching
// sha3binding and interfaces/descriptors.md row 4.
func TestBrokerInitGuardClassContract(t *testing.T) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "broker.go", nil, 0)
	if err != nil {
		t.Fatalf("ParseFile(broker.go): %v", err)
	}

	var foundCross1S bool
	for _, decl := range f.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "init" {
			continue
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			pkg, ok := sel.X.(*ast.Ident)
			if !ok || pkg.Name != "hybridge" {
				return true
			}
			if sel.Sel.Name == "Cross1" {
				t.Fatalf("broker.go init() calls hybridge.Cross1 (DEFAULT 4096 guard); must use hybridge.Cross1S (SMALL 1024 guard)")
			}
			if sel.Sel.Name == "Cross1S" {
				foundCross1S = true
			}
			return true
		})
	}
	if !foundCross1S {
		t.Fatal("broker.go init() did not call hybridge.Cross1S(FnAbiWord, 0)")
	}
}

// TestPinStringStackPinnerAndZeroAllocEmpty verifies that pinString uses a
// caller-owned stack runtime.Pinner (0 heap allocations on empty strings) and
// pins non-empty string bytes across the borrow lifetime.
func TestPinStringStackPinnerAndZeroAllocEmpty(t *testing.T) {
	allocs := testing.AllocsPerRun(100, func() {
		var p runtime.Pinner
		ptr, n := pinString("", &p)
		p.Unpin()
		if ptr != 0 || n != 0 {
			t.Fatalf("pinString empty = (%x, %d), want (0, 0)", ptr, n)
		}
	})
	if allocs != 0 {
		t.Fatalf("pinString(\"\", &p) allocs = %v, want 0", allocs)
	}

	var p runtime.Pinner
	const sample = `^\d{3}-\d{4}$`
	ptr, n := pinString(sample, &p)
	defer p.Unpin()
	if ptr == 0 || n != uintptr(len(sample)) {
		t.Fatalf("pinString(%q) = (%x, %d), want non-zero ptr and len %d", sample, ptr, n, len(sample))
	}
	got := unsafe.String(*(**byte)(unsafe.Pointer(&ptr)), int(n))
	if got != sample {
		t.Fatalf("pinned slice = %q, want %q", got, sample)
	}
}

// TestDecodeMatchStatusStrictContract verifies that decodeMatchStatus accepts
// only the C-ABI boolean words {0, 1} and rejects both negative error codes
// and non-boolean positive words with ErrRegex.
func TestDecodeMatchStatusStrictContract(t *testing.T) {
	if got, err := decodeMatchStatus(0); err != nil || got {
		t.Fatalf("decodeMatchStatus(0) = (%v, %v), want (false, nil)", got, err)
	}
	if got, err := decodeMatchStatus(1); err != nil || !got {
		t.Fatalf("decodeMatchStatus(1) = (%v, %v), want (true, nil)", got, err)
	}

	for _, bad := range []int64{-1, -2, math.MinInt64, 2, 3, 255, math.MaxInt64} {
		got, err := decodeMatchStatus(bad)
		if !errors.Is(err, ErrRegex) || got {
			t.Fatalf("decodeMatchStatus(%d) = (%v, %v), want (false, ErrRegex)", bad, got, err)
		}
	}
}
