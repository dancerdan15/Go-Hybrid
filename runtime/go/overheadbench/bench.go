// Package overheadbench — the G2 call-overhead bench's no-op crossing
// (Batch J; PLAN.md "call-overhead bench vs ≤5ns budget").
//
// The LIVE shipping path: hybridge.Cross1 (fast trampoline + in-trampoline
// stackguard) into hyrt's hy_nop — the crossing AS SHIPPED, the same
// hybridge assembly and staticlib every `hyb build` links, with zero payload
// work. Spike 1 measured this design as a PROTOTYPE (2.82 ns sandbox /
// 4.36 ns worker); this package measures the LANDED artifact — the G2 gate's
// evidence that the budget holds on the real bridge, not the spike rig.
//
// The cgo broker is the sha3binding pattern verbatim: addresses resolve ONCE
// at init, zero per-call cgo; the ABI handshake aborts loudly on drift.
package overheadbench

/*
#cgo LDFLAGS: -L${SRCDIR}/../lib -lhyrt
#cgo !windows LDFLAGS: -lpthread -ldl -lm
#cgo windows LDFLAGS: -lwinpthread -lntdll -lws2_32 -lbcrypt -luserenv -lole32 -loleaut32 -lsynchronization -ladvapi32
#include <stdint.h>

extern uint64_t hy_nop(uint64_t x);
extern uint64_t hyrt_abi_word(void);
extern uint64_t hyrt_stack_alignment_probe(void);

static void* a_nop(void)      { return (void*)&hy_nop; }
static void* a_abi_word(void) { return (void*)&hyrt_abi_word; }
static void* a_align(void)    { return (void*)&hyrt_stack_alignment_probe; }
*/
import "C"

import (
	"hybrid.program/hybridge"
	"unsafe"
)

// Function addresses (C ABI), resolved once at package init.
var (
	FnNop     = unsafe.Pointer(C.a_nop())
	FnAbiWord = unsafe.Pointer(C.a_abi_word())
	FnAlign   = unsafe.Pointer(C.a_align())
)

// abiWordExpected is hyrt_abi_word() for this binding generation.
const abiWordExpected = 0x68795f61626931 // "hy_abi1"

func init() {
	if FnNop == nil || FnAbiWord == nil || FnAlign == nil {
		panic("overheadbench: unresolved hyrt C-ABI symbol address")
	}
	// SMALL class (guard 1024, row 4): the abi-word probe is a constant
	// return — zero recursion, zero heap, and must not dirty DeepPathCount
	// on a fresh 2 KiB init goroutine stack before benchmark loops begin.
	if w := hybridge.Cross1S(FnAbiWord, 0); w != abiWordExpected {
		panic("overheadbench: hyrt ABI handshake mismatch (rebuild the runtime)")
	}
}

// Nop crosses the boundary and does nothing — the per-call cost of the
// shipping trampoline, isolated. The fast path runs on the caller's
// goroutine stack (the steady-state shape the ≤5 ns budget names); the
// deep-path retry is Cross1's internal concern, unchanged from spike 3.
func Nop(x uint64) uint64 {
	return hybridge.Cross1(FnNop, uintptr(x))
}

// NopS crosses the boundary via the SMALL-class trampoline (1024 B guard,
// row 4) — the tiny-callee fast path eligible on shallow goroutine stacks.
func NopS(x uint64) uint64 {
	return hybridge.Cross1S(FnNop, uintptr(x))
}

// NopRaw — the G2 bench's diagnostic: the fast trampoline with NO Go
// wrapper retry. Returns the sentinel when headroom is short — the bench
// checks it explicitly (the shallow-goroutine scenario EXPECTS the
// sentinel; that is the finding, not a failure).
func NopRaw(x uint64) (uint64, bool) {
	v := hybridge.Cross1Raw(FnNop, uintptr(x))
	if v == ^uint64(1) {
		return 0, false
	}
	return v, true
}

// DeepPathCount reports how many crossings took the deep path (cold-path
// counter — the bench reads it after each loop to prove the fast path).
func DeepPathCount() uint64 { return hybridge.DeepPathCount.Load() }

// DeepCrossCount reports how many crossings took the always-deep route
// (CrossDeep1..4 — the class-ledger DEEP class).
func DeepCrossCount() uint64 { return hybridge.DeepCrossCount.Load() }

// StackAlignmentProbe crosses into hyrt_stack_alignment_probe via the
// SMALL-class fast trampoline and returns a 16-byte-aligned local's address
// mod 16 (0 when the callee frame satisfies the 16-byte ABI contract).
func StackAlignmentProbe() uint64 { return hybridge.Cross1S(FnAlign, 0) }
