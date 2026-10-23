package hybench

import "testing"

func TestConstantSampleQuantileMonotonicity(t *testing.T) {
	samples := []int64{
		2048,
		2050,
		4096,
		65536,
		1_000_000,
		1_000_000_000,
		1 << 40,
		1 << 62,
	}
	for _, sample := range samples {
		h := New()
		for i := 0; i < 100; i++ {
			h.Record(sample)
		}
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
		if p50 != sample || p90 != sample || p99 != sample {
			t.Fatalf("sample=%d: quantiles (%d, %d, %d) must equal constant sample %d",
				sample, p50, p90, p99, sample)
		}
	}
}

func TestQuantileSweepMonotonicity(t *testing.T) {
	h := New()
	inputs := []int64{2048, 2048, 2048, 3000, 10_000, 250_000, 1_000_000}
	for _, v := range inputs {
		h.Record(v)
	}
	minV := h.ValueAt(0.0)
	maxV := h.ValueAt(1.0)
	if minV != 2048 || maxV != 1_000_000 {
		t.Fatalf("bounds = [%d, %d], want [2048, 1000000]", minV, maxV)
	}

	prev := minV
	for step := 0; step <= 100; step++ {
		f := float64(step) / 100.0
		cur := h.ValueAt(f)
		if cur < prev {
			t.Fatalf("non-monotonic quantile at f=%.2f: got %d < prev %d", f, cur, prev)
		}
		if cur < minV || cur > maxV {
			t.Fatalf("quantile at f=%.2f out of [minVal, maxVal]: got %d not in [%d, %d]",
				f, cur, minV, maxV)
		}
		prev = cur
	}
}

func TestZeroValueHistogramMinMaxAndQuantiles(t *testing.T) {
	var h Histogram
	h.Record(2048)
	h.Record(4096)

	if got := h.ValueAt(0.0); got != 2048 {
		t.Fatalf("zero-value Histogram ValueAt(0.0) = %d, want 2048", got)
	}
	if got := h.P50(); got != 2049 {
		t.Fatalf("zero-value Histogram P50() = %d, want 2049", got)
	}
	if got := h.ValueAt(1.0); got != 4096 {
		t.Fatalf("zero-value Histogram ValueAt(1.0) = %d, want 4096", got)
	}

	var hZeroFirst Histogram
	hZeroFirst.Record(0)
	hZeroFirst.Record(500)
	if got := hZeroFirst.ValueAt(0.0); got != 0 {
		t.Fatalf("zero-first Histogram ValueAt(0.0) = %d, want 0", got)
	}
	if got := hZeroFirst.ValueAt(1.0); got != 500 {
		t.Fatalf("zero-first Histogram ValueAt(1.0) = %d, want 500", got)
	}
}
