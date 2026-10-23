//go:build darwin || windows

package hybridge

import (
	"sync"
	"sync/atomic"
	"testing"
)

func TestReleaseDeepNegativePanics(t *testing.T) {
	defer func() {
		if r := recover(); r == nil {
			t.Fatal("releaseDeep(-1) did not panic")
		}
	}()
	releaseDeep(-1)
}

func TestReleaseDeepOutOfRangePanics(t *testing.T) {
	defer func() {
		if r := recover(); r == nil {
			t.Fatal("releaseDeep(deepStacks) did not panic")
		}
	}()
	releaseDeep(deepStacks)
}

func TestReleaseDeepDoubleFreePanics(t *testing.T) {
	i := acquireDeep()
	releaseDeep(i)
	defer func() {
		if r := recover(); r == nil {
			t.Fatal("double releaseDeep did not panic")
		}
		if got := atomic.LoadUint64(&deepBits); got != 0 {
			t.Fatalf("deepBits after double release panic = %#x, want 0", got)
		}
	}()
	releaseDeep(i)
}

func TestAcquireReleaseDeepConcurrent(t *testing.T) {
	const workers = 32
	const iterations = 16
	var active [deepStacks]atomic.Int32
	var wg sync.WaitGroup
	wg.Add(workers)
	for range workers {
		go func() {
			defer wg.Done()
			for range iterations {
				i := acquireDeep()
				if active[i].Add(1) != 1 {
					t.Errorf("stack %d held concurrently by multiple goroutines", i)
				}
				active[i].Add(-1)
				releaseDeep(i)
			}
		}()
	}
	wg.Wait()
	if got := atomic.LoadUint64(&deepBits); got != 0 {
		t.Fatalf("deepBits after concurrent pool cycle = %#x, want 0", got)
	}
}
