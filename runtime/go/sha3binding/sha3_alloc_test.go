package sha3binding

import (
	"bytes"
	"encoding/hex"
	"testing"
)

func TestBrokerSymbolAddressesNonNil(t *testing.T) {
	if FnDigest256 == nil {
		t.Fatal("FnDigest256 C-ABI address is nil")
	}
	if FnStreamChecksum == nil {
		t.Fatal("FnStreamChecksum C-ABI address is nil")
	}
	if FnAbiWord == nil {
		t.Fatal("FnAbiWord C-ABI address is nil")
	}
}

func TestDigest256AllocationBudget(t *testing.T) {
	payload := []byte("hybrid-ffi-pinned-borrow-allocation-budget")
	allocs := testing.AllocsPerRun(100, func() {
		out, err := Digest256(payload)
		if err != nil || len(out) != 32 {
			t.Fatalf("Digest256 failed: len=%d err=%v", len(out), err)
		}
	})
	if allocs > 1 {
		t.Fatalf("Digest256 allocated %.2f objects/op, want <= 1 (E5 contract)", allocs)
	}

	emptyAllocs := testing.AllocsPerRun(100, func() {
		out, err := Digest256(nil)
		if err != nil || len(out) != 32 {
			t.Fatalf("Digest256(nil) failed: len=%d err=%v", len(out), err)
		}
	})
	if emptyAllocs > 1 {
		t.Fatalf("Digest256(nil) allocated %.2f objects/op, want <= 1 (E5 contract)", emptyAllocs)
	}
}

func TestDigest256SliceInvariants(t *testing.T) {
	emptySlice := make([]byte, 0)
	gotEmpty, err := Digest256(emptySlice)
	if err != nil {
		t.Fatalf("Digest256([]byte{}): %v", err)
	}
	gotNil, err := Digest256(nil)
	if err != nil {
		t.Fatalf("Digest256(nil): %v", err)
	}
	if !bytes.Equal(gotEmpty, gotNil) {
		t.Fatalf("empty-slice digest %x != nil-slice digest %x", gotEmpty, gotNil)
	}

	framing := []byte("xxxxabcxxxx")
	snapshot := append([]byte(nil), framing...)
	sub := framing[4:7]
	gotSub, err := Digest256(sub)
	if err != nil {
		t.Fatalf("Digest256(sub): %v", err)
	}
	if !bytes.Equal(framing, snapshot) {
		t.Fatal("Digest256 mutated caller backing array")
	}
	if gotHex := hex.EncodeToString(gotSub); gotHex != vectors[1].want {
		t.Fatalf("Digest256(subslice \"abc\") = %s, want %s", gotHex, vectors[1].want)
	}

	gotSub[0] ^= 0xff
	again, err := Digest256(sub)
	if err != nil {
		t.Fatalf("Digest256(sub) retry: %v", err)
	}
	if gotHex := hex.EncodeToString(again); gotHex != vectors[1].want {
		t.Fatalf("mutating returned digest corrupted subsequent Digest256: got %s", gotHex)
	}
}
