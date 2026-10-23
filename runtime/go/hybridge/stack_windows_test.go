//go:build windows

package hybridge

import (
	"runtime/debug"
	"testing"
	"unsafe"
)

func winStackBytes(base uintptr, size int) []byte {
	p := *(**byte)(unsafe.Pointer(&base))
	return unsafe.Slice(p, size)
}

func TestWindowsDeepStackPool(t *testing.T) {
	seen := make(map[int]bool)
	total := deepStackSz + pageSize
	for range deepStacks {
		i := acquireDeep()
		defer releaseDeep(i)
		if seen[i] {
			t.Fatalf("stack %d acquired twice before release", i)
		}
		seen[i] = true
		base := deepBases[i]
		if base == 0 {
			t.Fatalf("stack %d base address is zero", i)
		}
		top := deepTops[i]
		if top&uintptr(15) != 0 {
			t.Fatalf("stack %d top %#x is not 16-byte ABI aligned", i, top)
		}
		if top%uintptr(pageSize) != 0 {
			t.Fatalf("stack %d top %#x is not page aligned", i, top)
		}
		if top != base+uintptr(total) {
			t.Fatalf("stack %d top %#x does not match base+total %#x", i, top, base+uintptr(total))
		}
		mapping := winStackBytes(base, total)
		mapping[pageSize] = byte(i)
		mapping[len(mapping)-1] = byte(i)
	}
}

func TestWindowsDeepStackGuard(t *testing.T) {
	i := acquireDeep()
	defer releaseDeep(i)
	old := debug.SetPanicOnFault(true)
	defer debug.SetPanicOnFault(old)
	defer func() {
		if recover() == nil {
			t.Error("write below the usable stack did not fault: guard is unprotected")
		}
	}()
	mapping := winStackBytes(deepBases[i], deepStackSz+pageSize)
	mapping[pageSize-1] = 1
}
