//go:build linux

package hybridge

import (
	"sync/atomic"
	"testing"
)

func TestComputeDeepAllocLayout(t *testing.T) {
	cases := []struct {
		name       string
		osPageSize int
		wantGuard  int
		wantUsable int
		wantTotal  int
	}{
		{
			name:       "standard_4k_page",
			osPageSize: 4096,
			wantGuard:  4096,
			wantUsable: 1 << 20,
			wantTotal:  (1 << 20) + 4096,
		},
		{
			name:       "arm64_16k_page",
			osPageSize: 16384,
			wantGuard:  16384,
			wantUsable: 1 << 20,
			wantTotal:  (1 << 20) + 16384,
		},
		{
			name:       "arm64_64k_page",
			osPageSize: 65536,
			wantGuard:  65536,
			wantUsable: 1 << 20,
			wantTotal:  (1 << 20) + 65536,
		},
		{
			name:       "zero_fallback_to_4k",
			osPageSize: 0,
			wantGuard:  defaultPageSize,
			wantUsable: 1 << 20,
			wantTotal:  (1 << 20) + defaultPageSize,
		},
		{
			name:       "negative_fallback_to_4k",
			osPageSize: -4096,
			wantGuard:  defaultPageSize,
			wantUsable: 1 << 20,
			wantTotal:  (1 << 20) + defaultPageSize,
		},
		{
			name:       "non_divisor_page_size_rounds_up_usable",
			osPageSize: 12288,
			wantGuard:  12288,
			wantUsable: 1056768,
			wantTotal:  1069056,
		},
		{
			name:       "huge_2m_page_size_rounds_up_usable",
			osPageSize: 2 << 20,
			wantGuard:  2 << 20,
			wantUsable: 2 << 20,
			wantTotal:  4 << 20,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			guard, usable, total := computeDeepAllocLayout(tc.osPageSize)
			if guard != tc.wantGuard {
				t.Fatalf("guardBytes = %d, want %d", guard, tc.wantGuard)
			}
			if usable != tc.wantUsable {
				t.Fatalf("usableBytes = %d, want %d", usable, tc.wantUsable)
			}
			if total != tc.wantTotal {
				t.Fatalf("totalBytes = %d, want %d", total, tc.wantTotal)
			}
			if usable < deepStackSz {
				t.Fatalf("usableBytes = %d < deepStackSz (%d)", usable, deepStackSz)
			}
			if usable%guard != 0 {
				t.Fatalf("usableBytes (%d) is not a multiple of guardBytes (%d)", usable, guard)
			}
			if total%guard != 0 {
				t.Fatalf("totalBytes (%d) is not a multiple of guardBytes (%d)", total, guard)
			}
		})
	}
}

func TestReleaseDeepBoundsAndBitmap(t *testing.T) {
	slot := acquireDeep()
	if slot < 0 || slot >= deepStacks {
		t.Fatalf("acquireDeep returned out-of-range slot %d", slot)
	}
	mask := uint64(1) << uint(slot)
	before := atomic.LoadUint64(&deepBits)
	if before&mask == 0 {
		t.Fatalf("expected bit %d to be set in deepBits (%#x)", slot, before)
	}

	// Out-of-bounds indices must be ignored without altering deepBits.
	for _, invalid := range []int{-64, -1, deepStacks, deepStacks + 1, 1024} {
		releaseDeep(invalid)
		if got := atomic.LoadUint64(&deepBits); got != before {
			t.Fatalf("releaseDeep(%d) modified deepBits: got %#x, want %#x", invalid, got, before)
		}
	}

	releaseDeep(slot)
	after := atomic.LoadUint64(&deepBits)
	if after&mask != 0 {
		t.Fatalf("expected bit %d to be cleared after releaseDeep(%d), deepBits=%#x", slot, slot, after)
	}
}
