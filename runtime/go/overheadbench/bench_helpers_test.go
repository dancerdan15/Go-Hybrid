package overheadbench

import (
	"testing"

	"hybrid.program/hybridge"
)

func TestBrokerSymbolAddressesNonNil(t *testing.T) {
	if FnNop == nil {
		t.Fatal("FnNop C-ABI address is nil")
	}
	if FnAbiWord == nil {
		t.Fatal("FnAbiWord C-ABI address is nil")
	}
	if FnAlign == nil {
		t.Fatal("FnAlign C-ABI address is nil")
	}
	if got := hybridge.Cross1S(FnAbiWord, 0); got != abiWordExpected {
		t.Fatalf("Cross1S(FnAbiWord, 0) = %#x, want %#x", got, abiWordExpected)
	}
}

func TestNopSPayloadFidelity(t *testing.T) {
	payloads := []uint64{0, 1, 7, 42, 1 << 32, 0x68795f61626931, ^uint64(0) >> 1, ^uint64(0)}
	for _, x := range payloads {
		if got := NopS(x); got != x {
			t.Fatalf("NopS(%#x) = %#x, want %#x", x, got, x)
		}
		if got := Nop(x); got != x {
			t.Fatalf("Nop(%#x) = %#x, want %#x", x, got, x)
		}
	}
}

func TestStackAlignmentProbeHelper(t *testing.T) {
	for i := 0; i < 64; i++ {
		if got := StackAlignmentProbe(); got != 0 {
			t.Fatalf("StackAlignmentProbe() iteration %d = %d, want 0 (16-byte aligned frame)", i, got)
		}
	}
}

func TestDeepCrossCountAccessor(t *testing.T) {
	before := DeepCrossCount()
	if got := hybridge.CrossDeep1(FnNop, 0xdeadbeef); got != 0xdeadbeef {
		t.Fatalf("CrossDeep1(FnNop, 0xdeadbeef) = %#x", got)
	}
	after := DeepCrossCount()
	if after != before+1 {
		t.Fatalf("DeepCrossCount() = %d after 1 CrossDeep1 call, want %d", after, before+1)
	}
}
