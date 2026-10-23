package jsonbinding

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"

	"hybrid.program/hybridge"
)

const fastStackNeedDefault = 4096

// TestAbiWordSmallClassOnShallowStack verifies that FnAbiWord (classified as
// SMALL in interfaces/descriptors.md) executes on the fast path via Cross1S
// inside the (SmallGuard, 4096) shallow-stack window where Cross1 falls back
// to the pooled deep stack and increments DeepPathCount.
func TestAbiWordSmallClassOnShallowStack(t *testing.T) {
	var pad [1024]byte
	var sink byte

	var hit func()
	var descend func(depth int)
	descend = func(depth int) {
		pad[depth%1024]++
		sink = pad[depth%1024]
		h := hybridge.SPHeadroomForTest()
		if h < hybridge.SmallGuard+512 {
			t.Fatalf("descended past window (headroom %d < %d)", h, hybridge.SmallGuard+512)
		}
		if h < fastStackNeedDefault-512 {
			hit()
			return
		}
		descend(depth + 1)
	}
	hit = func() {
		h := hybridge.SPHeadroomForTest()
		if h >= fastStackNeedDefault-256 || h <= hybridge.SmallGuard+256 {
			t.Fatalf("hit outside disagreement window: headroom %d", h)
		}

		before := hybridge.DeepPathCount.Load()
		if got := hybridge.Cross1S(FnAbiWord, 0); got != abiWordExpected {
			t.Fatalf("Cross1S(FnAbiWord) = 0x%x, want 0x%x", got, uint64(abiWordExpected))
		}
		if after := hybridge.DeepPathCount.Load(); after != before {
			t.Fatalf("Cross1S(FnAbiWord) took deep path at headroom %d (count %d -> %d)", h, before, after)
		}

		if got := hybridge.Cross1(FnAbiWord, 0); got != abiWordExpected {
			t.Fatalf("Cross1(FnAbiWord) = 0x%x, want 0x%x", got, uint64(abiWordExpected))
		}
		if after := hybridge.DeepPathCount.Load(); after != before+1 {
			t.Fatalf("Cross1(FnAbiWord) did not take deep path at headroom %d (count %d -> %d)", h, before, after)
		}
	}
	descend(0)
	_ = sink
}

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
