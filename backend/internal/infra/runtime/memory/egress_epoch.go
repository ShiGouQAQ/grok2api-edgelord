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

func (s *EgressEpochStore) BumpEpoch(_ context.Context, groupKey string) (uint64, error) {
	if groupKey == "" {
		return 0, fmt.Errorf("egress epoch group key is empty")
	}
	shard := &s.shards[shardIndex(groupKey)]
	shard.mu.Lock()
	defer shard.mu.Unlock()
	shard.values[groupKey]++
	return shard.values[groupKey], nil
}

func (s *EgressEpochStore) GetEpoch(_ context.Context, groupKey string) (uint64, error) {
	if groupKey == "" {
		return 0, nil
	}
	shard := &s.shards[shardIndex(groupKey)]
	shard.mu.Lock()
	defer shard.mu.Unlock()
	return shard.values[groupKey], nil
}
