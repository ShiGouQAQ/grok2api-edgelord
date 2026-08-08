package egress

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"sync"

	domain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

const (
	// mihomoSyncSourceName 是 Mihomo 测试组成员同步使用的专属 source 行名称。
	// 该 source 从不参与订阅拉取（Enabled=false 且无 URL），仅作为成员节点
	// 的归属标记；首版单测试组约定一个 source，多组需扩展为按组映射。
	mihomoSyncSourceName = "mihomo-test-group"
	// mihomoDefaultTestProxyURL 是同步成员测试通道出口的默认值；
	// 生产部署应经 NewMihomoSyncer 传入实际可达地址（如 http://grok-mihomo:7891，
	// 容器环境 127.0.0.1 指向本容器回环而非 mihomo）。
	mihomoDefaultTestProxyURL = "http://127.0.0.1:7891"
	// mihomoMaxGuardStateBytes 镜像 transport 层 qualityGuardState 读取上限。
	mihomoMaxGuardStateBytes = 8 << 20
)

// MihomoSyncRepository 是同步器所需的持久化入口：在基础节点读写之上追加
// 批量创建、批量启停与订阅源管理。relational.EgressRepository 全部实现，
// 无需新增 repository 方法。
type MihomoSyncRepository interface {
	repository.EgressRepository
	CreateEgressNodes(context.Context, []domain.Node) (int, error)
	UpdateEgressNodesEnabled(context.Context, []uint64, bool) (int, error)
	ListEgressSources(context.Context) ([]domain.SubscriptionSource, error)
	CreateEgressSource(context.Context, domain.SubscriptionSource) (domain.SubscriptionSource, error)
}

// MihomoSyncer 把 Mihomo 测试组成员同步为 DB egress 节点（默认 Enabled=false，
// 仅质量守卫经 ForcedEgressNodeID 探测），保证成员增删自动反映到节点池。
// 节点一律不删除，成员退出时只禁用。
type MihomoSyncer struct {
	repo           MihomoSyncRepository
	cipher         *security.Cipher
	guardStatePath string
	testProxyURL   string
	mu             sync.Mutex
	lastErr        error
}

// NewMihomoSyncer 构造同步器；guardStatePath 为空或文件缺失时视为无
// guard-owned 节点。testProxyURL 为空时回退默认值。
func NewMihomoSyncer(repo MihomoSyncRepository, cipher *security.Cipher, guardStatePath string, testProxyURL string) *MihomoSyncer {
	if testProxyURL = strings.TrimSpace(testProxyURL); testProxyURL == "" {
		testProxyURL = mihomoDefaultTestProxyURL
	}
	return &MihomoSyncer{repo: repo, cipher: cipher, guardStatePath: strings.TrimSpace(guardStatePath), testProxyURL: testProxyURL}
}

// LastError 返回最近一次 Sync 的错误（幂等查询，不触发重试）。
func (s *MihomoSyncer) LastError() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.lastErr
}

// Sync 幂等同步测试组成员。created 为本轮新建节点数，disabled 为本轮因
// 成员退出而禁用的节点数；两者都可能为 0（纯更新轮）。
func (s *MihomoSyncer) Sync(ctx context.Context, members []string, groupName string) (created, disabled int, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastErr = nil

	names := dedupeMihomoMembers(members)
	if len(names) == 0 || strings.TrimSpace(groupName) == "" {
		return 0, 0, nil
	}
	source, err := s.ensureSource(ctx)
	if err != nil {
		s.lastErr = err
		return 0, 0, err
	}
	guardOwned, err := s.readGuardOwnedNodes()
	if err != nil {
		// 守护状态不可读时不启用任何节点（fail-closed），避免覆盖守护隔离。
		s.lastErr = err
		return 0, 0, err
	}
	existing, err := s.egressNodesForSource(ctx, source.ID)
	if err != nil {
		s.lastErr = err
		return 0, 0, err
	}
	byName := make(map[string]domain.Node, len(existing))
	for _, node := range existing {
		byName[node.Name] = node
	}

	// upsert：已有行只补 SourceKey/Name，绝不改 enabled；新成员默认禁用。
	createNodes := make([]domain.Node, 0, len(names))
	for _, name := range names {
		node, found := byName[name]
		if found {
			updated, updateErr := s.alignExisting(ctx, source, node, groupName, name)
			if updateErr != nil {
				s.lastErr = updateErr
				return 0, 0, updateErr
			}
			byName[name] = updated
			continue
		}
		encryptedProxy, encryptErr := s.cipher.Encrypt(s.testProxyURL)
		if encryptErr != nil {
			s.lastErr = encryptErr
			return 0, 0, encryptErr
		}
		createNodes = append(createNodes, domain.Node{
			Name: name, Scope: domain.ScopeBuild, Enabled: false,
			SourceID: source.ID, SourceKey: mihomoMemberKey(groupName, name),
			EncryptedProxyURL: encryptedProxy, Health: 1, ProbeStatus: domain.ProbeStatusUnknown,
		})
	}
	if len(createNodes) > 0 {
		created, err = s.repo.CreateEgressNodes(ctx, createNodes)
		if err != nil {
			s.lastErr = err
			return 0, 0, err
		}
	}

	// 定向 enable：已有成员且 DB 行禁用且非 guard-owned → 启用；guard-owned
	// 跳过（守护隔离是唯一权威）。新建成员不在 byName 中（其 ID 由仓储分配，
	// 本轮未知），保持禁用留出守护探测窗口。
	enableIDs := make([]uint64, 0, len(names))
	for _, name := range names {
		node, found := byName[name]
		if !found || node.Enabled {
			continue
		}
		if guardOwned[node.ID] {
			continue
		}
		enableIDs = append(enableIDs, node.ID)
	}
	if len(enableIDs) > 0 {
		if _, err := s.repo.UpdateEgressNodesEnabled(ctx, enableIDs, true); err != nil {
			s.lastErr = err
			return 0, 0, err
		}
	}

	// 清理：source 归属但成员已不在列表 → 只禁用绝不删除。
	staleIDs := make([]uint64, 0, len(existing))
	memberSet := make(map[string]struct{}, len(names))
	for _, name := range names {
		memberSet[name] = struct{}{}
	}
	for _, node := range existing {
		if _, stillMember := memberSet[node.Name]; stillMember {
			continue
		}
		staleIDs = append(staleIDs, node.ID)
	}
	if len(staleIDs) > 0 {
		disabled, err = s.repo.UpdateEgressNodesEnabled(ctx, staleIDs, false)
		if err != nil {
			s.lastErr = err
			return created, 0, err
		}
	}
	return created, disabled, nil
}

// alignExisting 更新已有成员行：组改名时刷新 SourceKey，名称变化时刷新
// Name；代理配置保持既有值，Enabled 绝对不动。
func (s *MihomoSyncer) alignExisting(ctx context.Context, source domain.SubscriptionSource, node domain.Node, groupName, name string) (domain.Node, error) {
	expectedKey := mihomoMemberKey(groupName, name)
	if node.SourceKey == expectedKey && node.Name == name {
		return node, nil
	}
	node.SourceKey = expectedKey
	node.Name = name
	return s.repo.UpdateEgressNode(ctx, node)
}

// egressNodesForSource 返回归属于指定 source 的全部节点（ListEgressNodes
// 支持空 scope 全量返回）。
func (s *MihomoSyncer) egressNodesForSource(ctx context.Context, sourceID uint64) ([]domain.Node, error) {
	all, err := s.repo.ListEgressNodes(ctx, "", repository.SortQuery{})
	if err != nil {
		return nil, err
	}
	result := make([]domain.Node, 0, len(all))
	for _, node := range all {
		if node.SourceID == sourceID {
			result = append(result, node)
		}
	}
	return result, nil
}

// ensureSource 查找或创建专属 source 行（按名称幂等，名称唯一索引保证
// 并发下不重复）。
func (s *MihomoSyncer) ensureSource(ctx context.Context) (domain.SubscriptionSource, error) {
	sources, err := s.repo.ListEgressSources(ctx)
	if err != nil {
		return domain.SubscriptionSource{}, err
	}
	for _, source := range sources {
		if source.Name == mihomoSyncSourceName {
			return source, nil
		}
	}
	created, err := s.repo.CreateEgressSource(ctx, domain.SubscriptionSource{
		Name: mihomoSyncSourceName, Scope: domain.ScopeBuild, Enabled: false,
	})
	if err != nil {
		return domain.SubscriptionSource{}, err
	}
	return created, nil
}

// mihomoMemberKey 生成成员节点键。source_key 列有 64 字节上限（models.go
// chk_egress_nodes_source_key），且 (source_id, source_key) 复合唯一索引要求
// 每个成员独立键：mihomo:组:成员 天然满足，超长用哈希后缀截断保证唯一。
func mihomoMemberKey(groupName, memberName string) string {
	key := "mihomo:" + groupName + ":" + memberName
	if len(key) <= 64 {
		return key
	}
	sum := sha256.Sum256([]byte(key))
	// 55 + 1 + 8 = 64：保留可读前缀 + 哈希后缀，规避复合唯一索引冲突。
	return key[:55] + ":" + fmt.Sprintf("%x", sum[:4])
}

// dedupeMihomoMembers 去除空白与重复成员名，保持输入顺序。
func dedupeMihomoMembers(members []string) []string {
	seen := make(map[string]struct{}, len(members))
	result := make([]string, 0, len(members))
	for _, member := range members {
		name := strings.TrimSpace(member)
		if name == "" {
			continue
		}
		if _, exists := seen[name]; exists {
			continue
		}
		seen[name] = struct{}{}
		result = append(result, name)
	}
	return result
}

// mihomoGuardNodeState 镜像 transport/http/egress 的 qualityGuardNodeState
// 子集：同步器只关心 disabled_by_guard 标记。
type mihomoGuardNodeState struct {
	DisabledByGuard bool `json:"disabled_by_guard"`
}

// readGuardOwnedNodes 读取质量守护 state.json 中被守护禁用的节点 ID 集合。
// 文件缺失/路径为空 → 无 owned；格式非法 → 报错（调用方 fail-closed）。
func (s *MihomoSyncer) readGuardOwnedNodes() (map[uint64]bool, error) {
	if s.guardStatePath == "" {
		return nil, nil
	}
	file, err := os.Open(s.guardStatePath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, mihomoMaxGuardStateBytes+1))
	if err != nil || len(data) > mihomoMaxGuardStateBytes {
		return nil, errors.New("质量守护状态不可读")
	}
	var state struct {
		Nodes map[string]mihomoGuardNodeState `json:"nodes"`
	}
	if json.Unmarshal(data, &state) != nil || state.Nodes == nil {
		return nil, errors.New("质量守护状态格式无效")
	}
	owned := make(map[uint64]bool, len(state.Nodes))
	for nodeID, nodeState := range state.Nodes {
		if !nodeState.DisabledByGuard {
			continue
		}
		id, parseErr := strconv.ParseUint(nodeID, 10, 64)
		if parseErr == nil {
			owned[id] = true
		}
	}
	return owned, nil
}
