//go:build linux

package hybridge

// Pooled guarded Rust stacks (Spike 3): the deep-path crossing runs Rust on a
// 1 MiB mmap'd stack with a native-size guard page, OUTSIDE the Go heap (never
// scanned by the GC). Slots are exclusive per crossing via a 64-bit atomic
// bitmap — the fix for the shared-stack collision that Spike 1's single
// mmap stack exhibited under concurrent cleanups (two goroutines on different
// Ms crossing at the same instant overwrite each other's Rust frames).
//
// The fast path does not use these stacks at all: shallow callees run on the
// caller's own goroutine stack after a stackguard headroom check.

import (
	"math/bits"
	"runtime"
	"sync/atomic"
	"syscall"
	"unsafe"
)

const (
	deepStacks      = 64      // pool size (one bit each)
	deepStackSz     = 1 << 20 // 1 MiB minimum usable stack per slot
	defaultPageSize = 4096    // fallback guard page size
)

var (
	deepMappings [deepStacks][]byte  // retain the guarded mappings for the pool lifetime
	deepTops     [deepStacks]uintptr // stack tops (base + guard + usable)
	deepBits     uint64              // bitmap: bit i set = stack i in use
	deepGuardSz  int                 // active OS guard page size in bytes
	deepUsableSz int                 // active page-aligned usable stack size in bytes
)

// computeDeepAllocLayout calculates the page-aligned guard page size, usable
// stack bytes (>= deepStackSz), and total mmap length for a given OS page size.
// Linux on arm64 commonly runs with 16 KiB or 64 KiB pages; hardcoding 4096
// leaves total unaligned to the OS page size and causes mprotect(PROT_NONE) on
// the bottom page to consume 12 KiB–60 KiB of the usable stack.
func computeDeepAllocLayout(osPageSize int) (guardBytes, usableBytes, totalBytes int) {
	guardBytes = osPageSize
	if guardBytes <= 0 {
		guardBytes = defaultPageSize
	}
	usableBytes = deepStackSz
	if rem := usableBytes % guardBytes; rem != 0 {
		usableBytes += guardBytes - rem
	}
	totalBytes = guardBytes + usableBytes
	return guardBytes, usableBytes, totalBytes
}

func init() {
	guardBytes, usableBytes, totalBytes := computeDeepAllocLayout(syscall.Getpagesize())
	deepGuardSz = guardBytes
	deepUsableSz = usableBytes
	for i := 0; i < deepStacks; i++ {
		b, err := syscall.Mmap(-1, 0, totalBytes, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_PRIVATE|syscall.MAP_ANON)
		if err != nil {
			panic("hybridge: mmap deep stack: " + err.Error())
		}
		// guard page at the bottom (stack grows down toward it)
		if err := syscall.Mprotect(b[:guardBytes], syscall.PROT_NONE); err != nil {
			_ = syscall.Munmap(b)
			panic("hybridge: mprotect guard page: " + err.Error())
		}
		deepMappings[i] = b
		deepTops[i] = uintptr(unsafe.Pointer(&b[0])) + uintptr(totalBytes)
	}
}

// acquireDeep returns the index of an exclusively-held pooled stack.
func acquireDeep() int {
	for {
		old := atomic.LoadUint64(&deepBits)
		if old == ^uint64(0) {
			runtime.Gosched() // pool exhausted (64 concurrent deep calls): back off
			continue
		}
		i := bits.TrailingZeros64(^old)
		b := uint64(1) << uint(i)
		if atomic.CompareAndSwapUint64(&deepBits, old, old|b) {
			return i
		}
	}
}

// releaseDeep frees a pooled stack acquired by acquireDeep.
func releaseDeep(i int) {
	if i < 0 || i >= deepStacks {
		return
	}
	atomic.AndUint64(&deepBits, ^(uint64(1) << uint(i)))
}
