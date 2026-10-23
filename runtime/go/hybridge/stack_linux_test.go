//go:build linux

package hybridge

import (
	"runtime/debug"
	"syscall"
	"testing"
	"unsafe"
)

func TestLinuxDeepStackPool(t *testing.T) {
	guardBytes, usableBytes, totalBytes := computeDeepAllocLayout(syscall.Getpagesize())
	if deepGuardSz != guardBytes {
		t.Fatalf("deepGuardSz = %d, want %d", deepGuardSz, guardBytes)
	}
	if deepUsableSz != usableBytes {
		t.Fatalf("deepUsableSz = %d, want %d", deepUsableSz, usableBytes)
	}
	seen := make(map[int]bool)
	for range deepStacks {
		i := acquireDeep()
		defer releaseDeep(i)
		if seen[i] {
			t.Fatalf("stack %d acquired twice before release", i)
		}
		seen[i] = true
		top := deepTops[i]
		if top%uintptr(guardBytes) != 0 {
			t.Fatalf("stack %d top %#x is not aligned to OS guard page size %d", i, top, guardBytes)
		}
		// Both ends of the usable region must be writable. On 16 KiB / 64 KiB
		// linux/arm64 kernels, hardcoding a 4 KiB guard offset leaves the first
		// 12 KiB / 60 KiB of the usable stack inside the PROT_NONE page.
		mapping := deepMappings[i]
		if len(mapping) != totalBytes {
			t.Fatalf("stack %d mapping len = %d, want %d", i, len(mapping), totalBytes)
		}
		if len(mapping)-guardBytes < deepStackSz {
			t.Fatalf("stack %d usable len = %d, want >= %d", i, len(mapping)-guardBytes, deepStackSz)
		}
		if top != uintptr(unsafe.Pointer(&mapping[len(mapping)-1]))+1 {
			t.Fatal("stack top does not match the mapping end")
		}
		mapping[guardBytes] = byte(i)
		mapping[len(mapping)-1] = byte(i)
	}
}

func TestLinuxDeepStackGuard(t *testing.T) {
	i := acquireDeep()
	defer releaseDeep(i)
	old := debug.SetPanicOnFault(true)
	defer debug.SetPanicOnFault(old)
	defer func() {
		if recover() == nil {
			t.Error("write below the usable stack did not fault: guard is unprotected")
		}
	}()
	deepMappings[i][deepGuardSz-1] = 1
}
