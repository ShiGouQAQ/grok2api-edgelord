package memory

import (
	"context"
	"errors"
	"sync"
	"testing"
)

var errNonMonotonicEpoch = errors.New("bump 返回值未单调递增")

func TestEgressEpochStoreBumpAndGet(t *testing.T) {
	ctx := context.Background()
	store := NewEgressEpochStore()
	if epoch, err := store.GetEpoch(ctx, "group"); err != nil || epoch != 0 {
		t.Fatalf("缺键 epoch = %d, err = %v", epoch, err)
	}
	if epoch, err := store.BumpEpoch(ctx, "group"); err != nil || epoch != 1 {
		t.Fatalf("首次 bump = %d, err = %v", epoch, err)
	}
	if epoch, err := store.BumpEpoch(ctx, "group"); err != nil || epoch != 2 {
		t.Fatalf("二次 bump = %d, err = %v", epoch, err)
	}
	if epoch, err := store.GetEpoch(ctx, "group"); err != nil || epoch != 2 {
		t.Fatalf("读取 epoch = %d, err = %v", epoch, err)
	}
}

func TestEgressEpochStoreGroupIsolation(t *testing.T) {
	ctx := context.Background()
	store := NewEgressEpochStore()
	for _, key := range []string{"mihomo:web", "mihomo:build", "mihomo:console"} {
		if _, err := store.BumpEpoch(ctx, key); err != nil {
			t.Fatalf("bump %q: %v", key, err)
		}
	}
	if epoch, err := store.BumpEpoch(ctx, "mihomo:web"); err != nil || epoch != 2 {
		t.Fatalf("mihomo:web 独立递增 = %d, err = %v", epoch, err)
	}
	if epoch, err := store.GetEpoch(ctx, "mihomo:build"); err != nil || epoch != 1 {
		t.Fatalf("mihomo:build 不受影响 = %d, err = %v", epoch, err)
	}
}

func TestEgressEpochStoreConcurrentBumpsAreMonotonic(t *testing.T) {
	ctx := context.Background()
	store := NewEgressEpochStore()
	const workers = 16
	const bumpsPerWorker = 200
	start := make(chan struct{})
	results := make(chan uint64, workers)
	errors := make(chan error, workers)
	var group sync.WaitGroup
	for range workers {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			var last uint64
			for range bumpsPerWorker {
				epoch, err := store.BumpEpoch(ctx, "group")
				if err != nil {
					errors <- err
					return
				}
				if epoch <= last {
					errors <- errNonMonotonicEpoch
					return
				}
				last = epoch
			}
			results <- last
		}()
	}
	close(start)
	group.Wait()
	close(results)
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	maxEpoch := uint64(0)
	for epoch := range results {
		if epoch > maxEpoch {
			maxEpoch = epoch
		}
	}
	if maxEpoch != workers*bumpsPerWorker {
		t.Fatalf("并发 bump 最大 epoch = %d, want %d", maxEpoch, workers*bumpsPerWorker)
	}
	if epoch, err := store.GetEpoch(ctx, "group"); err != nil || epoch != maxEpoch {
		t.Fatalf("并发后读取 epoch = %d, err = %v", epoch, err)
	}
}
