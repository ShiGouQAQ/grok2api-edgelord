package memory

import (
	"context"
	"fmt"
	"sync"
)

type epochShard struct {
	mu     sync.Mutex
	values map[string]uint64
}

// EgressEpochStore 提供单实例共享的出口代际号（epoch），行为与 Redis 实现一致：
// 组级切换、节点集变化等出口选择事件原子递增，所有读取者看到同一单调值。
type EgressEpochStore struct {
	shards [shardCount]epochShard
}

func NewEgressEpochStore() *EgressEpochStore {
	store := &EgressEpochStore{}
	for index := range store.shards {
		store.shards[index].values = make(map[string]uint64)
	}
	return store
}

// BumpEpoch 原子递增指定组的出口代际号并返回新值。内存实现本无需同步，
// 但尊重 ctx 语义：ctx 已取消时返回 ctx.Err()，与 Redis 实现行为对称。
func (s *EgressEpochStore) BumpEpoch(ctx context.Context, groupKey string) (uint64, error) {
	if groupKey == "" {
		return 0, fmt.Errorf("egress epoch group key is empty")
	}
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	shard := &s.shards[shardIndex(groupKey)]
	shard.mu.Lock()
	defer shard.mu.Unlock()
	shard.values[groupKey]++
	return shard.values[groupKey], nil
}

// GetEpoch 返回指定组的当前出口代际号，缺键返回 0。内存实现本无需同步，
// 但尊重 ctx 语义：ctx 已取消时返回 ctx.Err()，与 Redis 实现行为对称。
func (s *EgressEpochStore) GetEpoch(ctx context.Context, groupKey string) (uint64, error) {
	if groupKey == "" {
		return 0, nil
	}
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	shard := &s.shards[shardIndex(groupKey)]
	shard.mu.Lock()
	defer shard.mu.Unlock()
	return shard.values[groupKey], nil
}
