package hybench

import (
	"math"
	"testing"
)

func TestExtremeExponent62NoOverflow(t *testing.T) {
	extremes := []int64{
		1 << 62,
		(1 << 62) + (1 << 52),
		math.MaxInt64 - (1 << 52),
		math.MaxInt64 - 1,
		math.MaxInt64,
	}
	for _, sample := range extremes {
		h := New()
		h.Record(sample)

		minV := h.ValueAt(0.0)
		p50 := h.P50()
		p90 := h.P90()
		p99 := h.P99()
		maxV := h.ValueAt(1.0)

		if minV != sample {
			t.Fatalf("sample=%d: ValueAt(0.0)=%d, want %d", sample, minV, sample)
		}
		if maxV != sample {
			t.Fatalf("sample=%d: ValueAt(1.0)=%d, want %d", sample, maxV, sample)
		}
		if p50 <= 0 || p90 <= 0 || p99 <= 0 {
			t.Fatalf("sample=%d: signed overflow in quantiles (%d, %d, %d)",
				sample, p50, p90, p99)
		}
		if p50 != sample || p90 != sample || p99 != sample {
			t.Fatalf("sample=%d: quantiles (%d, %d, %d) != %d",
				sample, p50, p90, p99, sample)
		}
	}

	h := New()
	h.Record(1 << 62)
	h.Record(math.MaxInt64)
	if got := h.ValueAt(0.0); got != 1<<62 {
		t.Fatalf("minVal = %d, want %d", got, int64(1)<<62)
	}
	if got := h.P99(); got != math.MaxInt64 {
		t.Fatalf("P99 = %d, want MaxInt64 (%d)", got, int64(math.MaxInt64))
	}
}

func TestValueAtNaNAndInfFractions(t *testing.T) {
	empty := New()
	if got := empty.ValueAt(math.NaN()); got != 0 {
		t.Fatalf("empty ValueAt(NaN) = %d, want 0", got)
	}

	h := New()
	h.Record(100)
	h.Record(2048)
	h.Record(1_000_000)

	if got := h.ValueAt(math.NaN()); got != 0 {
		t.Fatalf("populated ValueAt(NaN) = %d, want 0", got)
	}
	if got := h.ValueAt(math.Inf(-1)); got != 100 {
		t.Fatalf("ValueAt(-Inf) = %d, want 100", got)
	}
	if got := h.ValueAt(math.Inf(1)); got != 1_000_000 {
		t.Fatalf("ValueAt(+Inf) = %d, want 1000000", got)
	}
}
