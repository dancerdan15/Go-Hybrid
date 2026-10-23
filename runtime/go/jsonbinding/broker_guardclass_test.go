package jsonbinding

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
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
