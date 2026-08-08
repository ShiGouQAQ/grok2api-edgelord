package egress

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	domain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// mihomoSyncRepositoryStub 内存版 MihomoSyncRepository，记录创建/启停调用
// 便于断言同步语义。
type mihomoSyncRepositoryStub struct {
	nodes         []domain.Node
	sources       []domain.SubscriptionSource
	createdNodes  []domain.Node
	enabledCalls  [][]uint64
	disabledCalls [][]uint64
	nextNodeID    uint64
	nextSourceID  uint64
}

func (s *mihomoSyncRepositoryStub) ListEgressNodes(_ context.Context, scope domain.Scope, _ repository.SortQuery) ([]domain.Node, error) {
	if scope == "" {
		return append([]domain.Node(nil), s.nodes...), nil
	}
	result := make([]domain.Node, 0, len(s.nodes))
	for _, node := range s.nodes {
		if node.Scope == scope {
			result = append(result, node)
		}
	}
	return result, nil
}

func (s *mihomoSyncRepositoryStub) GetEgressNode(_ context.Context, id uint64) (domain.Node, error) {
	for _, node := range s.nodes {
		if node.ID == id {
			return node, nil
		}
	}
	return domain.Node{}, repository.ErrNotFound
}

func (s *mihomoSyncRepositoryStub) CreateEgressNode(_ context.Context, value domain.Node) (domain.Node, error) {
	s.nextNodeID++
	value.ID = s.nextNodeID
	s.nodes = append(s.nodes, value)
	return value, nil
}

func (s *mihomoSyncRepositoryStub) UpdateEgressNode(_ context.Context, value domain.Node) (domain.Node, error) {
	for index := range s.nodes {
		if s.nodes[index].ID == value.ID {
			s.nodes[index] = value
			return value, nil
		}
	}
	return domain.Node{}, repository.ErrNotFound
}

func (s *mihomoSyncRepositoryStub) DeleteEgressNode(_ context.Context, id uint64) error {
	for index := range s.nodes {
		if s.nodes[index].ID == id {
			s.nodes = append(s.nodes[:index], s.nodes[index+1:]...)
			return nil
		}
	}
	return repository.ErrNotFound
}

func (s *mihomoSyncRepositoryStub) CreateEgressNodes(_ context.Context, values []domain.Node) (int, error) {
	for _, value := range values {
		s.nextNodeID++
		value.ID = s.nextNodeID
		s.nodes = append(s.nodes, value)
		s.createdNodes = append(s.createdNodes, value)
	}
	return len(values), nil
}

func (s *mihomoSyncRepositoryStub) UpdateEgressNodesEnabled(_ context.Context, ids []uint64, enabled bool) (int, error) {
	updated := 0
	for _, id := range ids {
		for index := range s.nodes {
			if s.nodes[index].ID == id && s.nodes[index].Enabled != enabled {
				s.nodes[index].Enabled = enabled
				updated++
			}
		}
	}
	if enabled {
		s.enabledCalls = append(s.enabledCalls, append([]uint64(nil), ids...))
	} else {
		s.disabledCalls = append(s.disabledCalls, append([]uint64(nil), ids...))
	}
	return updated, nil
}

func (s *mihomoSyncRepositoryStub) ListEgressSources(_ context.Context) ([]domain.SubscriptionSource, error) {
	return append([]domain.SubscriptionSource(nil), s.sources...), nil
}

func (s *mihomoSyncRepositoryStub) CreateEgressSource(_ context.Context, value domain.SubscriptionSource) (domain.SubscriptionSource, error) {
	s.nextSourceID++
	value.ID = s.nextSourceID
	s.sources = append(s.sources, value)
	return value, nil
}

func (s *mihomoSyncRepositoryStub) nodeByName(name string) (domain.Node, bool) {
	for _, node := range s.nodes {
		if node.Name == name {
			return node, true
		}
	}
	return domain.Node{}, false
}

func newMihomoSyncerForTest(t *testing.T, repo *mihomoSyncRepositoryStub, guardStatePath string) *MihomoSyncer {
	t.Helper()
	cipher, err := security.NewCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if err != nil {
		t.Fatal(err)
	}
	return NewMihomoSyncer(repo, cipher, guardStatePath, "http://127.0.0.1:7891")
}

func writeGuardState(t *testing.T, disabledNodeIDs ...uint64) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "state.json")
	nodes := make(map[string]mihomoGuardNodeState, len(disabledNodeIDs))
	for _, id := range disabledNodeIDs {
		nodes[strconv.FormatUint(id, 10)] = mihomoGuardNodeState{DisabledByGuard: true}
	}
	data, err := json.Marshal(map[string]any{"version": 1, "nodes": nodes})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestMihomoSyncCreatesNewMembersDisabled(t *testing.T) {
	repo := &mihomoSyncRepositoryStub{}
	syncer := newMihomoSyncerForTest(t, repo, "")
	created, disabled, err := syncer.Sync(context.Background(), []string{"US-01", "JP-02"}, "XAI-TEST-GROUP")
	if err != nil {
		t.Fatal(err)
	}
	if created != 2 || disabled != 0 {
		t.Fatalf("created=%d disabled=%d, want 2/0", created, disabled)
	}
	// 新建成员必须默认禁用，且带 mihomo: 前缀 SourceKey（IsMihomoSynced 依据）。
	for _, name := range []string{"US-01", "JP-02"} {
		node, found := repo.nodeByName(name)
		if !found {
			t.Fatalf("member %q not created", name)
		}
		if node.Enabled {
			t.Fatalf("member %q must be created disabled", name)
		}
		if !node.IsMihomoSynced() {
			t.Fatalf("member %q missing mihomo: source key %q", name, node.SourceKey)
		}
		if node.Scope != domain.ScopeBuild {
			t.Fatalf("member %q scope = %q, want grok_build", name, node.Scope)
		}
		if node.EncryptedProxyURL == "" {
			t.Fatalf("member %q missing proxy url", name)
		}
	}
	if len(repo.enabledCalls) != 0 {
		t.Fatalf("new members must not be enabled in the same run, enabledCalls=%v", repo.enabledCalls)
	}
}

func TestMihomoSyncSkipsGuardOwnedEnable(t *testing.T) {
	repo := &mihomoSyncRepositoryStub{}
	cipher, err := security.NewCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if err != nil {
		t.Fatal(err)
	}
	encryptedProxy, err := cipher.Encrypt("socks5://127.0.0.1:7891")
	if err != nil {
		t.Fatal(err)
	}
	// 预置一个禁用成员（模拟上一轮已创建），守护将其隔离（disabled_by_guard）。
	repo.nodes = []domain.Node{{
		ID: 1, Name: "US-01", Scope: domain.ScopeBuild, Enabled: false,
		SourceID: 1, SourceKey: "mihomo:XAI-TEST-GROUP:US-01", EncryptedProxyURL: encryptedProxy,
	}}
	repo.sources = []domain.SubscriptionSource{{ID: 1, Name: mihomoSyncSourceName, Scope: domain.ScopeBuild}}
	guardStatePath := writeGuardState(t, 1)
	syncer := newMihomoSyncerForTest(t, repo, guardStatePath)

	created, disabled, err := syncer.Sync(context.Background(), []string{"US-01"}, "XAI-TEST-GROUP")
	if err != nil {
		t.Fatal(err)
	}
	if created != 0 || disabled != 0 {
		t.Fatalf("created=%d disabled=%d, want 0/0", created, disabled)
	}
	// guard-owned 节点不得被定向 enable。
	if len(repo.enabledCalls) != 0 {
		t.Fatalf("guard-owned node must not be enabled, enabledCalls=%v", repo.enabledCalls)
	}
	node, _ := repo.nodeByName("US-01")
	if node.Enabled {
		t.Fatal("guard-owned node must stay disabled")
	}
}

func TestMihomoSyncEnablesNonGuardOwnedMember(t *testing.T) {
	repo := &mihomoSyncRepositoryStub{}
	cipher, err := security.NewCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if err != nil {
		t.Fatal(err)
	}
	encryptedProxy, err := cipher.Encrypt("socks5://127.0.0.1:7891")
	if err != nil {
		t.Fatal(err)
	}
	repo.nodes = []domain.Node{{
		ID: 1, Name: "US-01", Scope: domain.ScopeBuild, Enabled: false,
		SourceID: 1, SourceKey: "mihomo:XAI-TEST-GROUP:US-01", EncryptedProxyURL: encryptedProxy,
	}}
	repo.sources = []domain.SubscriptionSource{{ID: 1, Name: mihomoSyncSourceName, Scope: domain.ScopeBuild}}
	// guardStatePath 为空：无 guard-owned 节点。
	syncer := newMihomoSyncerForTest(t, repo, "")

	created, disabled, err := syncer.Sync(context.Background(), []string{"US-01"}, "XAI-TEST-GROUP")
	if err != nil {
		t.Fatal(err)
	}
	if created != 0 || disabled != 0 {
		t.Fatalf("created=%d disabled=%d, want 0/0", created, disabled)
	}
	if len(repo.enabledCalls) != 1 || len(repo.enabledCalls[0]) != 1 || repo.enabledCalls[0][0] != 1 {
		t.Fatalf("expected one enable call for node 1, got %v", repo.enabledCalls)
	}
	node, _ := repo.nodeByName("US-01")
	if !node.Enabled {
		t.Fatal("non guard-owned member should be enabled")
	}
}

func TestMihomoSyncDisablesRemovedMemberWithoutDelete(t *testing.T) {
	repo := &mihomoSyncRepositoryStub{}
	cipher, err := security.NewCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if err != nil {
		t.Fatal(err)
	}
	encryptedProxy, err := cipher.Encrypt("socks5://127.0.0.1:7891")
	if err != nil {
		t.Fatal(err)
	}
	repo.nodes = []domain.Node{
		{ID: 1, Name: "US-01", Scope: domain.ScopeBuild, Enabled: true,
			SourceID: 1, SourceKey: "mihomo:XAI-TEST-GROUP:US-01", EncryptedProxyURL: encryptedProxy},
		{ID: 2, Name: "JP-02", Scope: domain.ScopeBuild, Enabled: true,
			SourceID: 1, SourceKey: "mihomo:XAI-TEST-GROUP:JP-02", EncryptedProxyURL: encryptedProxy},
	}
	repo.sources = []domain.SubscriptionSource{{ID: 1, Name: mihomoSyncSourceName, Scope: domain.ScopeBuild}}
	syncer := newMihomoSyncerForTest(t, repo, "")

	// JP-02 退出测试组。
	created, disabled, err := syncer.Sync(context.Background(), []string{"US-01"}, "XAI-TEST-GROUP")
	if err != nil {
		t.Fatal(err)
	}
	if created != 0 || disabled != 1 {
		t.Fatalf("created=%d disabled=%d, want 0/1", created, disabled)
	}
	if len(repo.disabledCalls) != 1 || repo.disabledCalls[0][0] != 2 {
		t.Fatalf("expected disable call for node 2, got %v", repo.disabledCalls)
	}
	// 成员绝不删除，只是禁用。
	if _, found := repo.nodeByName("JP-02"); !found {
		t.Fatal("removed member must not be deleted")
	}
	if node, _ := repo.nodeByName("JP-02"); node.Enabled {
		t.Fatal("removed member must be disabled")
	}
}

func TestMihomoSyncIsIdempotent(t *testing.T) {
	repo := &mihomoSyncRepositoryStub{}
	syncer := newMihomoSyncerForTest(t, repo, "")
	ctx := context.Background()
	if _, _, err := syncer.Sync(ctx, []string{"US-01"}, "XAI-TEST-GROUP"); err != nil {
		t.Fatal(err)
	}
	// 第二次同步不应重复创建。
	created, disabled, err := syncer.Sync(ctx, []string{"US-01"}, "XAI-TEST-GROUP")
	if err != nil {
		t.Fatal(err)
	}
	if created != 0 || disabled != 0 {
		t.Fatalf("second sync created=%d disabled=%d, want 0/0", created, disabled)
	}
	nodes, err := repo.ListEgressNodes(ctx, "", repository.SortQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 1 {
		t.Fatalf("expected exactly one node, got %d", len(nodes))
	}
}

func TestMihomoSyncMissingGuardStateMeansNoOwned(t *testing.T) {
	// state.json 不存在 → 无 guard-owned，成员应被启用。
	repo := &mihomoSyncRepositoryStub{}
	cipher, err := security.NewCipher("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if err != nil {
		t.Fatal(err)
	}
	encryptedProxy, err := cipher.Encrypt("socks5://127.0.0.1:7891")
	if err != nil {
		t.Fatal(err)
	}
	repo.nodes = []domain.Node{{
		ID: 1, Name: "US-01", Scope: domain.ScopeBuild, Enabled: false,
		SourceID: 1, SourceKey: "mihomo:XAI-TEST-GROUP:US-01", EncryptedProxyURL: encryptedProxy,
	}}
	repo.sources = []domain.SubscriptionSource{{ID: 1, Name: mihomoSyncSourceName, Scope: domain.ScopeBuild}}
	missingPath := filepath.Join(t.TempDir(), "does-not-exist.json")
	syncer := newMihomoSyncerForTest(t, repo, missingPath)
	if _, _, err := syncer.Sync(context.Background(), []string{"US-01"}, "XAI-TEST-GROUP"); err != nil {
		t.Fatal(err)
	}
	if len(repo.enabledCalls) != 1 {
		t.Fatalf("missing guard state must not block enable, enabledCalls=%v", repo.enabledCalls)
	}
}

func TestMihomoMemberKeyStaysWithinColumnLimit(t *testing.T) {
	// source_key 列上限 64 字节：超长成员名必须截断且唯一。
	longName := "very-long-member-name-" + string(make([]byte, 80))
	key := mihomoMemberKey("XAI-TEST-GROUP", longName)
	if len(key) > 64 {
		t.Fatalf("mihomoMemberKey length = %d, want <= 64", len(key))
	}
	if !strings.HasPrefix(key, "mihomo:") {
		t.Fatalf("mihomoMemberKey %q must keep mihomo: prefix", key)
	}
	other := mihomoMemberKey("XAI-TEST-GROUP", longName+"-different")
	if key == other {
		t.Fatal("different long member names must not collide")
	}
}
