// Package egress 提供出口链路管理（节点选择、Clearance、Mihomo 出口切换）。
//
// Mihomo 出口切换设计：当 Go 节点收到 403 或 Clearance 求解失败时，出口被
// 视为超大共享池，不冷却 Go 节点本身；而是异步（fire-and-forget）触发
// Mihomo 代理组切换到下一个可用节点。切换是单飞的（single-flight）：并发
// 触发会合并为一次真实切换。切换完成后出口 IP 变化，Clearance 在下一次
// 获取时基于新 IP 重新绑定，无需显式失效。
//
// 可选出口 IP 校验：配置 ExitProbeProxyURL 后，切换在节点级验证通过之后
// 还会通过本地 Mihomo 代理端口轮询出口 IP，确认其确实变化；同一 IP 会封禁
// 目标节点并重选重试。探测失败只降级为节点级验证（不判定切换失败）。未配置
// 时行为与旧版完全一致。
package egress

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/chenyme/grok2api/backend/internal/repository"
	"golang.org/x/sync/singleflight"
)

const maxMihomoResponseBytes = 4 << 20

// mihomoSwitchLockKeyPrefix 是跨实例切换互斥锁的键前缀；键名统一为
// 前缀 + 组键名（默认配置组名）。切换锁键与共享 epoch 存储键共享组键后缀，
// 但分属不同命名空间（锁：egress-mihomo-switch:{group}，epoch：
// egress-epoch:{group}），互不冲突。
const mihomoSwitchLockKeyPrefix = "egress-mihomo-switch:"

// mihomoEpochStoreBumpTimeout 是共享 epoch 存储单次 bump 的截止时长。
// bump 在 c.mu 持锁路径上执行（保证各事件与本地代际号同序），超时兜底防止
// 共享存储故障时卡死本地切换互斥。
const mihomoEpochStoreBumpTimeout = 3 * time.Second

// mihomoAutoSwitchMinInterval 是"故障驱动的自动切换"（SwitchAndBlacklist
// Current）的最小间隔：403 环与 solve 环可能在短时间内反复触发切换，而每次
// 成功切换 bump epoch 都会使全部 clearance 失效（求解风暴）。间隔内再次
// 触发直接按 Merged 合并（静默，不加冷却）；人工/管理驱动切换
// （SwitchToOptimal/Rotate）不受限。
const mihomoAutoSwitchMinInterval = 30 * time.Second

// mihomoEpochRefreshInterval 是共享 epoch 周期同步的最小间隔（见
// refreshEpochThrottled）：SelectOptimal 与切换路径调用频繁，逐次 GetEpoch
// 会产生不必要的 Redis 往返，间隔内合并为一次。
const mihomoEpochRefreshInterval = time.Second

// perAttemptHTTPBudget 是切换锁 TTL 每轮尝试预留的 HTTP 往返预算：除
// waitExitIPChange 轮询（VerifyTimeout）外，每轮还有 SelectOptimal 拉组、
// SwitchNode PUT、verifySwitch GET 等 HTTP 调用（单次 ≤5s client 超时）。
// TTL 只按 VerifyTimeout 计算会在切换完成前提前过期，锁被第二实例抢走导致
// 并发切换。
const perAttemptHTTPBudget = 15 * time.Second

// mihomoDelayProbeTimeout 是主动延迟探测的单节点探测截止时长，同时作为
// ProbeGroupDelays 的默认 timeout 参数。
const mihomoDelayProbeTimeout = 5 * time.Second

// mihomoDelayProbeHTTPTimeout 是 ProbeGroupDelays 整组探测的 HTTP 总预算：
// mihomo 对组内全部节点逐个/并发测延迟（单节点预算 mihomoDelayProbeTimeout），
// 几十个节点的组耗时可达 5-8s+，通用 client（5s 总超时）必然提前掐断，
// 因此组级探测使用独立的长超时 client。该预算只约束 HTTP 往返，不改变
// mihomo 侧单节点探测语义。
const mihomoDelayProbeHTTPTimeout = 30 * time.Second

// mihomoDelayCacheTTL 是主动延迟探测结果的缓存有效期。状态 API 每 2s 轮询
// 一次成员延迟，若每轮都调用 Mihomo /group/{group}/delay 会打爆探测端点；
// TTL 内复用缓存，到期后下一次轮询重新探测。
const mihomoDelayCacheTTL = 45 * time.Second

// mihomoDelayCache 是单个组的延迟探测缓存条目。ok 表示最近一次探测是否
// 成功返回了可用延迟；失败也会缓存（ok=false），避免状态轮询每 2s 重试
// 一个正在失败的探测端点。
type mihomoDelayCache struct {
	delays map[string]int
	ok     bool
	at     time.Time
}

// mihomoBlacklistTTL 是黑名单条目的存活时长，对齐 Go 节点指数冷却的上限
// (min(10*time.Minute, ...))：封禁超过 TTL 后节点自动解禁，避免节点集稳定
// 时黑名单永久覆盖全部节点导致"全部节点均已被封禁"死锁。
const mihomoBlacklistTTL = 10 * time.Minute

// 出口 IP 校验默认参数（重试上限/探测 URL/验证超时），Mihomo 客户端独立取值，
// 与任意代理提供商无关；节点来源可为任意机场订阅或自建节点。
const (
	defaultExitIPProbeURL     = "https://1.1.1.1/cdn-cgi/trace"
	defaultExitRetryCap       = 3
	defaultExitVerifyTimeout  = 15 * time.Second
	mihomoExitProbeTimeout    = 8 * time.Second
	mihomoExitProbePollPeriod = time.Second
)

// MihomoSwitchResult 是切换调用的三态结果。
//
//	MihomoSwitchMerged 单飞合并：切换已在进行，本次调用未执行任何操作，
//	                       调用方不得据此应用冷却。
//	MihomoSwitchFailed 执行但失败（含同出口 IP 重试耗尽）。
//	MihomoSwitchDone   真实切换成功（节点级验证通过；配置出口 IP 校验时
//	                   还要求观察到出口 IP 变化，或探测失败降级）。
type MihomoSwitchResult int

const (
	MihomoSwitchMerged MihomoSwitchResult = iota
	MihomoSwitchFailed
	MihomoSwitchDone
)

// MihomoConfig 是 Mihomo REST API 的运行时配置，由管理端设置热更新。
// 除 Enabled/APIURL/GroupName 外均为可选字段：全部留空时行为与旧版一致。
type MihomoConfig struct {
	Enabled   bool
	APIURL    string
	GroupName string

	// ExitProbeProxyURL 是本地 mihomo 代理端口（如 http://127.0.0.1:7890），
	// 出口 IP 探测经它转发；为空时跳过出口 IP 校验（纯旧版路径）。
	ExitProbeProxyURL string
	// IPProbeURL 是 IP 回显端点；默认 https://1.1.1.1/cdn-cgi/trace
	// （解析 ip= 行），也支持纯文本 IP 响应体。
	IPProbeURL string
	// MaxAttempts 是出口 IP 未变化时的重试次数上限（默认 3，加首次尝试共
	// MaxAttempts+1 次切换）。
	MaxAttempts int
	// VerifyTimeout 是每次尝试内出口 IP 轮询的截止时长（默认 15s）。
	VerifyTimeout time.Duration
	// TestGroupName 是测试组名称（如 XAI-TEST-GROUP，见
	// tools/mihomo/mihomo-dual-channel.yaml）。与 TestProxyURL 同时非空时启用
	// 双通道：Manager 为测试组创建独立 MihomoClient 实例（各自维护黑名单/
	// switching/epoch），测试组切换只作用于测试客户端自身，绝不扰动生产出口。
	// 两者皆空 = legacy 单组行为（零回归）。
	TestGroupName string
	// TestProxyURL 是测试组本地代理端口（如 http://127.0.0.1:7891，listener
	// 直连测试组）。测试客户端的出口 IP 校验经它转发；TestGroupName 为空时忽略。
	TestProxyURL string
	// DelayProbeURL 是延迟探测目标 URL（如 http://www.gstatic.com/generate_204）。
	// 非空时 SelectOptimal 在历史延迟数据缺失时调用 Mihomo /group/{group}/delay
	// 主动探测各节点延迟（select 组不产生 history）；探测失败回退首可用节点。
	// 空 = 现有行为（零回归）。
	DelayProbeURL string
}

// mihomoGroup 是 GET /proxies/{group} 的响应子集。
type mihomoGroup struct {
	All       []string                  `json:"all"`
	Now       string                    `json:"now"`
	Providers map[string]mihomoProvider `json:"providers"`
}

type mihomoProvider struct {
	Nodes []mihomoNode `json:"nodes"`
}

type mihomoNode struct {
	Name    string        `json:"name"`
	History []mihomoDelay `json:"history"`
}

type mihomoDelay struct {
	Delay int `json:"delay"`
}

// MihomoClient 管理一个 Mihomo 代理组的节点切换。
//
// 内部状态（配置、黑名单、节点集快照、单飞标志）由 mu 保护；switchCount 与
// epoch 用 atomic 便于状态 API 无锁读取。切换失败计数保留用于诊断。
type MihomoClient struct {
	mu          sync.Mutex
	logger      *slog.Logger
	client      *http.Client
	config      MihomoConfig
	blacklist   map[string]time.Time // 节点名 -> 封禁截止时间（UTC），超过 TTL 自动解禁
	lastNodeSet map[string]struct{}
	lastNow     string // 上次观察到的默认连接节点（订阅更新可能直接改写 now）
	switching   bool
	// lastAutoSwitchTime 是上次"故障驱动的自动切换"成功的时刻；switchMinInterval
	// 内的再次自动切换被节流（返回 Merged），防止 403/solve 环高频切换触发
	// clearance 风暴。人工/管理驱动切换（SwitchToOptimal/Rotate）不检查也不更新。
	lastAutoSwitchTime time.Time
	switchMinInterval  time.Duration
	// lastEpochRefresh 是上次共享 epoch 周期同步的时刻；epochRefreshInterval
	// 内合并（见 refreshEpochThrottled），避免每个请求一次 Redis 往返。
	lastEpochRefresh     time.Time
	epochRefreshInterval time.Duration
	// switchCount 是成功切换的次数（统计用途，状态 API 展示）。
	switchCount atomic.Uint64
	// epoch 是出口代际版本号：任何可能改变出口选择状态的事件（成功切换、
	// 节点集变化、默认连接节点 now 变化、清空黑名单、配置变化）都 +1。
	// Manager 用它与出口绑定 clearance 指纹：出口一变旧缓存自动作废。
	epoch atomic.Uint64
	// delayCache 是主动延迟探测结果缓存（组名 -> 条目），由 mu 保护。失效
	// 时机与 epoch 语义一致：bumpEpochLocked（切换/节点集变化/黑名单变动/
	// 配置变化）即清空；TTL 兜底防止长时间无事件时展示过期延迟。
	delayCache map[string]mihomoDelayCache
	// delayProbes 合并同一组缓存的并发主动探测（DelaySnapshot miss 后按
	// groupName 单飞），避免状态 API 每 2s 轮询/多标签页/多实例同时打爆
	// 探测端点。
	delayProbes singleflight.Group
	// epochStore 与 switchLock 是可选注入的跨实例协调组件（多实例部署由
	// Manager 注入 Redis 共享实现）；均为 nil 时保持进程本地行为，单实例
	// 部署零回归。
	epochStore repository.EgressEpochStore
	switchLock repository.DistributedLock
	// blacklistEventPublisher 是可选注入的黑名单事件发布回调（多实例部署
	// 经 Redis 事件总线广播，其他实例合并进本地黑名单）；nil 时黑名单保持
	// 进程本地（单实例部署零回归）。发布在 c.mu 持锁路径上同步执行，保证
	// 各实例合并顺序与发布顺序一致。
	blacklistEventPublisher BlacklistEventPublisher
}

// BlacklistEventPublisher 广播一次 Mihomo 黑名单操作（ban/unban/clear）供
// 其他实例合并；nil 时客户端保持进程本地黑名单（单实例零回归）。
type BlacklistEventPublisher func(context.Context, repository.EgressEvent) error

// NewMihomoClient 创建并配置一个 Mihomo 客户端。
func NewMihomoClient(cfg MihomoConfig) *MihomoClient {
	client := &MihomoClient{
		logger:               slog.Default(),
		client:               &http.Client{Timeout: 5 * time.Second},
		blacklist:            make(map[string]time.Time),
		delayCache:           make(map[string]mihomoDelayCache),
		switchMinInterval:    mihomoAutoSwitchMinInterval,
		epochRefreshInterval: mihomoEpochRefreshInterval,
	}
	client.UpdateConfig(cfg)
	return client
}

// SetLogger 设置日志器；nil 时回落全局默认。
func (c *MihomoClient) SetLogger(logger *slog.Logger) {
	if logger == nil {
		logger = slog.Default()
	}
	c.mu.Lock()
	c.logger = logger
	c.mu.Unlock()
}

// SetEpochStore 注入跨实例共享的出口代际版本号存储；nil 时回退本地 atomic
// epoch（单实例部署零回归）。
func (c *MihomoClient) SetEpochStore(store repository.EgressEpochStore) {
	c.mu.Lock()
	c.epochStore = store
	c.mu.Unlock()
}

// SetSwitchLock 注入跨实例出口切换互斥锁；nil 时回退本地无锁切换。
func (c *MihomoClient) SetSwitchLock(lock repository.DistributedLock) {
	c.mu.Lock()
	c.switchLock = lock
	c.mu.Unlock()
}

// SetBlacklistEventPublisher 注入跨实例黑名单事件发布回调；nil 时回退进程
// 本地黑名单（单实例部署零回归）。
func (c *MihomoClient) SetBlacklistEventPublisher(publisher BlacklistEventPublisher) {
	c.mu.Lock()
	c.blacklistEventPublisher = publisher
	c.mu.Unlock()
}

func (c *MihomoClient) log() *slog.Logger {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.logger == nil {
		return slog.Default()
	}
	return c.logger
}

// UpdateConfig 热更新配置，镜像 Manager.UpdateClearanceConfig 的模式。
// API 地址或分组变化时，旧节点的黑名单与节点集快照随之作废。
// 出口 IP 校验无持久化状态（探测客户端按次创建），无需额外清理。
func (c *MihomoClient) UpdateConfig(cfg MihomoConfig) {
	cfg.APIURL = strings.TrimRight(strings.TrimSpace(cfg.APIURL), "/")
	cfg.GroupName = strings.TrimSpace(cfg.GroupName)
	cfg.TestGroupName = strings.TrimSpace(cfg.TestGroupName)
	cfg.TestProxyURL = strings.TrimSpace(cfg.TestProxyURL)
	if cfg.IPProbeURL == "" {
		cfg.IPProbeURL = defaultExitIPProbeURL
	}
	if cfg.MaxAttempts <= 0 {
		cfg.MaxAttempts = defaultExitRetryCap
	}
	if cfg.VerifyTimeout <= 0 {
		cfg.VerifyTimeout = defaultExitVerifyTimeout
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !cfg.Enabled || cfg.APIURL == "" || cfg.GroupName == "" {
		c.config = MihomoConfig{}
		clear(c.delayCache)
		return
	}
	previous := c.config
	changed := previous.APIURL != cfg.APIURL || previous.GroupName != cfg.GroupName
	c.config = cfg
	// 探测结果随配置变化作废：探测 URL 或组变更后旧延迟无意义。
	clear(c.delayCache)
	if changed {
		clear(c.blacklist)
		c.lastNodeSet = nil
		c.lastNow = ""
		c.bumpEpochLocked()
	}
}

// SwitchCount 返回成功切换的次数（未切换过为 0）。
func (c *MihomoClient) SwitchCount() uint64 {
	return c.switchCount.Load()
}

// Epoch 返回出口代际版本号：任何可能改变出口选择状态的事件都会使其 +1，
// Manager 用它作 clearance 绑定指纹的组成部分，出口一变旧缓存自动作废。
func (c *MihomoClient) Epoch() uint64 {
	return c.epoch.Load()
}

// bumpEpochLocked 递增出口代际版本号并清空延迟探测缓存（两者同生命周期：
// 任何出口选择状态变化都使旧探测结果失效）。注入了 epochStore 时以共享
// 存储为权威：BumpEpoch 成功用返回值追平本地 atomic（max 语义，只前向）——
// 本地在降级 bump 期间可能已领先共享值（例如连续两次失败 bump 后共享仍
// 落后），此时若直接 Store(shared) 会把本地拉回历史值，旧出口 clearance
// 指纹复活；只有 shared > local 才写入。失败时保留本地 +1 降级（记 Warn，
// 绝不失败）——代际号语义允许短暂不一致，下一次成功 bump 或周期刷新追平。
// 调用方须持有 c.mu（保证各事件的本地代际号与共享存储同序）。
func (c *MihomoClient) bumpEpochLocked() {
	clear(c.delayCache)
	if c.epochStore == nil {
		c.epoch.Add(1)
		return
	}
	groupKey := c.config.GroupName
	ctx, cancel := context.WithTimeout(context.Background(), mihomoEpochStoreBumpTimeout)
	defer cancel()
	shared, err := c.epochStore.BumpEpoch(ctx, groupKey)
	if err != nil {
		logger := c.logger
		if logger == nil {
			logger = slog.Default()
		}
		logger.Warn("mihomo_epoch_store_bump_failed", "group", groupKey, "error", err)
		c.epoch.Add(1)
		return
	}
	if shared > c.epoch.Load() {
		c.epoch.Store(shared)
	}
}

// refreshEpochThrottled 合并并节流周期性的共享 epoch 同步：最近
// epochRefreshInterval 内已同步过则跳过（mu 保护，并发调用单飞），避免
// SelectOptimal/切换高频路径逐次 GetEpoch 的 Redis 往返。多实例部署下其他
// 实例切换出口会使共享 epoch 领先本地镜像，周期刷新让 clearance 指纹收敛；
// 单实例（无 epochStore）为 no-op。best-effort：失败仅记 Debug（内部处理）。
func (c *MihomoClient) refreshEpochThrottled() {
	c.mu.Lock()
	if !c.lastEpochRefresh.IsZero() && time.Since(c.lastEpochRefresh) < c.epochRefreshInterval {
		c.mu.Unlock()
		return
	}
	c.lastEpochRefresh = time.Now()
	c.mu.Unlock()
	c.RefreshEpochFromStore(context.Background())
}

// RefreshEpochFromStore 从共享 epoch 存储拉取当前代际号并追平本地 atomic
// 镜像（只前向，不倒退）：降级 bump 或他实例 bump 后本地镜像可能落后于
// 共享值，定期刷新使各实例 clearance 指纹收敛一致。best-effort：未注入
// epochStore 直接返回；读取失败记 Debug 并返回，下次成功 bump 或刷新追平。
func (c *MihomoClient) RefreshEpochFromStore(ctx context.Context) {
	c.mu.Lock()
	store := c.epochStore
	groupKey := c.config.GroupName
	c.mu.Unlock()
	if store == nil {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, mihomoEpochStoreBumpTimeout)
	defer cancel()
	shared, err := store.GetEpoch(ctx, groupKey)
	if err != nil {
		c.log().Debug("mihomo_epoch_store_refresh_failed", "group", groupKey, "error", err)
		return
	}
	for {
		local := c.epoch.Load()
		if shared <= local {
			return
		}
		if c.epoch.CompareAndSwap(local, shared) {
			return
		}
	}
}

// BannedNodes 返回黑名单节点的排序快照，供状态 API 展示。
func (c *MihomoClient) BannedNodes() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now().UTC()
	names := make([]string, 0, len(c.blacklist))
	for name, until := range c.blacklist {
		if until.After(now) {
			names = append(names, name)
		} else {
			delete(c.blacklist, name)
		}
	}
	sort.Strings(names)
	return names
}

// ClearBlacklist 清空黑名单并返回被清空的节点数量。
// 供管理端"清空黑名单"操作使用：节点集刷新或配置变更也会触发清空，
// 此处是显式恢复被误封节点的手动入口。
func (c *MihomoClient) ClearBlacklist() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	count := len(c.blacklist)
	if count > 0 {
		clear(c.blacklist)
		c.bumpEpochLocked()
		c.publishBlacklistEventLocked(repository.EgressEventBlacklistCleared, "")
	}
	return count
}

// BanNode 将节点加入黑名单（TTL mihomoBlacklistTTL，到期自动解禁），返回
// 当前黑名单节点数。已封禁节点重复封禁只刷新 TTL。黑名单只影响
// SelectOptimal/SelectOptimalInGroup 的候选集，不直接切换节点；质量守护用它
// 封禁测试组内探测失败的成员。
func (c *MihomoClient) BanNode(name string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	name = strings.TrimSpace(name)
	if name == "" {
		return len(c.blacklist)
	}
	if _, exists := c.blacklist[name]; !exists {
		c.bumpEpochLocked()
	}
	c.blacklist[name] = time.Now().UTC().Add(mihomoBlacklistTTL)
	c.publishBlacklistEventLocked(repository.EgressEventNodeBanned, name)
	return len(c.blacklist)
}

// UnbanNode 将节点移出黑名单（解禁），返回剩余黑名单节点数。
func (c *MihomoClient) UnbanNode(name string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	name = strings.TrimSpace(name)
	if _, exists := c.blacklist[name]; exists {
		delete(c.blacklist, name)
		c.bumpEpochLocked()
		c.publishBlacklistEventLocked(repository.EgressEventNodeUnbanned, name)
	}
	return len(c.blacklist)
}

// publishBlacklistEventLocked 广播一次黑名单操作到共享事件总线（其他实例
// 经 MergeBlacklistEvent 合并）。调用方须持有 c.mu；同步发布保证各实例
// 合并顺序与发布顺序一致。best-effort：失败仅记 Warn，绝不阻断本地黑名单
// 操作（与 bumpEpochLocked 的降级语义一致）。
func (c *MihomoClient) publishBlacklistEventLocked(kind repository.EgressEventKind, nodeName string) {
	publisher := c.blacklistEventPublisher
	if publisher == nil {
		return
	}
	event := repository.EgressEvent{Kind: kind, Group: c.config.GroupName, NodeName: nodeName}
	ctx, cancel := context.WithTimeout(context.Background(), mihomoEpochStoreBumpTimeout)
	defer cancel()
	if err := publisher(ctx, event); err != nil {
		logger := c.logger
		if logger == nil {
			logger = slog.Default()
		}
		logger.Warn("mihomo_blacklist_event_publish_failed", "kind", string(kind), "group", event.Group, "node", nodeName, "error", err)
	}
}

// MergeBlacklistEvent 将其他实例广播的黑名单事件合并进本地黑名单（幂等；
// 只增/只清，不删除对方刚加的封禁）：
//   - node_banned：加入（已存在则刷新 TTL）；
//   - node_unbanned：仅当事件晚于本地封禁时刻（本地封禁时刻 ≈ 截止时刻 -
//     TTL）才解禁，防止过期的解禁事件删掉刚收到的封禁；
//   - blacklist_cleared：清空。
//
// 组名不匹配（测试组等其他客户端）时忽略。本地不 bump epoch：封禁源实例
// 已通过共享 epochStore 递增，周期刷新（refreshEpochThrottled）让本地镜像
// 收敛，clearance 指纹保持一致。
func (c *MihomoClient) MergeBlacklistEvent(event repository.EgressEvent) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.config.GroupName == "" || event.Group != c.config.GroupName {
		return
	}
	switch event.Kind {
	case repository.EgressEventNodeBanned:
		if strings.TrimSpace(event.NodeName) != "" {
			c.blacklist[event.NodeName] = time.Now().UTC().Add(mihomoBlacklistTTL)
		}
	case repository.EgressEventNodeUnbanned:
		until, exists := c.blacklist[event.NodeName]
		if !exists {
			return
		}
		if event.PublishedAt.IsZero() || event.PublishedAt.After(until.Add(-mihomoBlacklistTTL)) {
			delete(c.blacklist, event.NodeName)
		}
	case repository.EgressEventBlacklistCleared:
		clear(c.blacklist)
	}
}

// GetGroupNodes 获取代理组当前的全部节点列表（GET /proxies/{group} 的 all）。
// 非 200 或连接失败返回错误。
func (c *MihomoClient) GetGroupNodes(ctx context.Context) ([]string, error) {
	group, err := c.fetchGroup(ctx)
	if err != nil {
		return nil, err
	}
	return group.All, nil
}

// GetCurrentNode 获取代理组当前生效的节点（GET 响应的 now 字段）。
func (c *MihomoClient) GetCurrentNode(ctx context.Context) (string, error) {
	group, err := c.fetchGroup(ctx)
	if err != nil {
		return "", err
	}
	return group.Now, nil
}

// SwitchNode 将代理组切换到指定节点（PUT /proxies/{group}，body {"name": node}）。
// 204 视为成功，其余状态码与连接错误返回错误。
func (c *MihomoClient) SwitchNode(ctx context.Context, name string) error {
	cfg, ok := c.configSnapshot()
	if !ok {
		return errors.New("Mihomo 未启用或未配置")
	}
	if strings.TrimSpace(name) == "" {
		return errors.New("Mihomo 切换目标节点为空")
	}
	endpoint := cfg.APIURL + "/proxies/" + url.PathEscape(cfg.GroupName)
	payload, err := json.Marshal(map[string]string{"name": name})
	if err != nil {
		return fmt.Errorf("编码 Mihomo 切换请求: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, endpoint, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("创建 Mihomo 切换请求: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := c.client.Do(request)
	if err != nil {
		return fmt.Errorf("调用 Mihomo 切换 API: %w", err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode != http.StatusNoContent {
		return fmt.Errorf("Mihomo 切换节点 %q 失败: HTTP %d", name, response.StatusCode)
	}
	return nil
}

// SelectOptimal 根据节点延迟历史选择最优节点。
//
// 规则（与 Python 版语义一致）：排除黑名单节点，可选排除当前节点；延迟取
// providers 各节点 history 最后一条，-1/0 视为不可用；没有任何延迟数据时
// 返回第一个可用节点。全部被黑名单覆盖时不自动清空重试 —— 黑名单由
// updateBlacklistOnNodeChangeLocked（节点集变化）或 UpdateConfig（配置变化）
// 清空，避免失败的切换触发源不断重复轮换同一批节点。
func (c *MihomoClient) SelectOptimal(ctx context.Context, excludeCurrent bool) (string, error) {
	return c.selectOptimalInGroup(ctx, "", excludeCurrent)
}

// SelectOptimalInGroup 在指定组内选择最优节点（groupName 为空时使用客户端
// 当前配置组）。规则与 SelectOptimal 一致，供按名操作组（双通道测试组）复用。
func (c *MihomoClient) SelectOptimalInGroup(ctx context.Context, groupName string, excludeCurrent bool) (string, error) {
	return c.selectOptimalInGroup(ctx, groupName, excludeCurrent)
}

func (c *MihomoClient) selectOptimalInGroup(ctx context.Context, groupName string, excludeCurrent bool) (string, error) {
	// P1-4：周期追平共享 epoch 镜像（他实例 bump 后本实例收敛）；节流合并，
	// best-effort，绝不阻塞选路。
	c.refreshEpochThrottled()
	group, err := c.fetchGroupNamed(ctx, groupName)
	if err != nil {
		return "", err
	}
	if len(group.All) == 0 {
		return "", errors.New("Mihomo 分组没有可用节点")
	}
	c.mu.Lock()
	c.updateBlacklistOnNodeChangeLocked(group.All, group.Now)
	now := time.Now().UTC()
	banned := make(map[string]struct{}, len(c.blacklist))
	for name, until := range c.blacklist {
		if until.After(now) {
			banned[name] = struct{}{}
		} else {
			delete(c.blacklist, name)
		}
	}
	c.mu.Unlock()

	available := mihomoAvailable(group.All, group.Now, banned, excludeCurrent)
	if len(available) == 0 {
		// 全部被黑名单覆盖：保持封禁，等待节点集刷新或配置变更后再重试，
		// 而不是立即清空黑名单导致切换循环无终止。
		return "", errors.New("Mihomo 全部节点均已被封禁，等待节点集刷新或配置变更")
	}

	latencies := make(map[string]int, len(available))
	for _, provider := range group.Providers {
		for _, node := range provider.Nodes {
			if len(node.History) == 0 {
				continue
			}
			delay := node.History[len(node.History)-1].Delay
			if delay <= 0 {
				continue // -1/0 表示不可用
			}
			if !mihomoContains(available, node.Name) {
				continue
			}
			if current, exists := latencies[node.Name]; !exists || delay < current {
				latencies[node.Name] = delay
			}
		}
	}
	if len(latencies) == 0 {
		// 没有历史延迟数据（select 组不产生 history）：配置了 DelayProbeURL
		// 时主动探测一次，仍无数据则回退第一个可用节点（保持旧版语义）。
		if fresh, ok := c.delayProbe(ctx, groupName, available); ok {
			for name, delay := range fresh {
				if current, exists := latencies[name]; !exists || delay < current {
					latencies[name] = delay
				}
			}
		}
	}
	if len(latencies) == 0 {
		// 没有任何延迟数据：直接取第一个可用节点。
		return available[0], nil
	}
	best, bestDelay := "", int(^uint(0)>>1)
	for name, delay := range latencies {
		if delay < bestDelay {
			best, bestDelay = name, delay
		}
	}
	return best, nil
}

// SwitchAndBlacklistCurrent 将当前节点加入黑名单并切换到最优节点。
//
// 单飞：同一时刻只允许一次切换进行，并发调用直接合并（返回 Merged）。
// 节流：故障驱动的自动切换受 switchMinInterval 限频，间隔内再次触发直接
// 返回 Merged（静默，不加冷却），防止 403/solve 环高频切换触发 clearance
// 风暴。
// 返回 MihomoSwitchDone 表示真实发生了切换（版本号 +1）；MihomoSwitchFailed
// 表示执行失败（含同出口 IP 重试耗尽），此时调用方应回退 Go 节点冷却；
// MihomoSwitchMerged 表示未执行任何操作，调用方不得应用冷却。reason 记录在
// 日志中用于定位切换来源。
func (c *MihomoClient) SwitchAndBlacklistCurrent(ctx context.Context, reason string) MihomoSwitchResult {
	_, result := c.doSwitch(ctx, reason, true, true)
	return result
}

// SwitchToOptimal 手动切换到当前最优节点（不封禁当前节点）。
//
// 与 SwitchAndBlacklistCurrent 的区别仅在于不动黑名单、不受自动切换节流
// 限制；单飞语义一致，并发调用直接合并（返回空串、Merged）。返回
// (目标节点, 切换结果)。
func (c *MihomoClient) SwitchToOptimal(ctx context.Context, reason string) (string, MihomoSwitchResult) {
	return c.doSwitch(ctx, reason, false, false)
}

// Rotate 将组切换到当前节点之外的健康成员（排除黑名单），并验证出口 IP
// 变化。用于守护进程轮换被判定失效的当前出口：当前节点被永久封禁，成功
// 后返回新生效的节点名（GetCurrentNode），失败/合并返回 ("", result)。
// 单飞语义与 SwitchAndBlacklistCurrent 一致。
func (c *MihomoClient) Rotate(ctx context.Context, reason string) (string, MihomoSwitchResult) {
	_, result := c.doSwitch(ctx, reason, true, false)
	if result != MihomoSwitchDone {
		return "", result
	}
	name, err := c.GetCurrentNode(ctx)
	if err != nil {
		// 切换已由 verifySwitch 确认生效，此处拉取失败只影响名称回报。
		c.log().Warn("mihomo_rotate_get_current", "reason", reason, "error", err)
		return "", MihomoSwitchDone
	}
	return name, MihomoSwitchDone
}

// SwitchTestGroup 将组切换到指定节点（显式目标，不经过 SelectOptimal）。
// 质量守护在测试组上逐节点探测时使用：目标明确，无需延迟择优。单飞语义与
// doSwitch 一致；切换只在本客户端自身递增 switchCount/epoch —— 双通道下
// 测试客户端是独立实例，测试切换绝不作用于生产出口指纹。
func (c *MihomoClient) SwitchTestGroup(ctx context.Context, name, reason string) (string, MihomoSwitchResult) {
	c.mu.Lock()
	if c.switching {
		c.mu.Unlock()
		c.log().Info("mihomo_switch_merged", "reason", reason)
		return "", MihomoSwitchMerged
	}
	c.switching = true
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		c.switching = false
		c.mu.Unlock()
	}()

	if _, ok := c.configSnapshot(); !ok {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "config", "error", "Mihomo 未启用或未配置")
		return "", MihomoSwitchFailed
	}
	if strings.TrimSpace(name) == "" {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "target", "error", "切换目标节点为空")
		return "", MihomoSwitchFailed
	}
	if err := c.SwitchNode(ctx, name); err != nil {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "switch", "target", name, "error", err)
		return "", MihomoSwitchFailed
	}
	if err := c.verifySwitch(ctx, name); err != nil {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "verify", "target", name, "error", err)
		return "", MihomoSwitchFailed
	}
	c.switchCount.Add(1)
	c.mu.Lock()
	c.bumpEpochLocked()
	c.mu.Unlock()
	c.log().Info("mihomo_test_switch", "reason", reason, "to", name, "switch_count", c.switchCount.Load(), "epoch", c.epoch.Load())
	return name, MihomoSwitchDone
}

// doSwitch 是切换的唯一状态机入口：单飞 → （可选）自动切换节流 → 取当前节点
// → 选最优 → （可选）封禁当前 → 切换 → 节点级验证（第一道闸）→ 出口 IP 校验
// （可选第二道闸：同 IP 封禁目标节点并重选重试，耗尽返回 Failed；探测失败
// 降级为节点级验证通过）。节点级验证通过后立即提交代际号（缩短"出口已变但
// epoch 未提交"窗口），同 IP 重试循环中只提交一次；重试耗尽返回 Failed 时
// 代际号已提交（出口选择确实变更），与"Failed 不 bump"的旧语义不同。
//
// throttled 标记调用是否为"故障驱动的自动切换"（SwitchAndBlacklistCurrent）：
// 为 true 时受 switchMinInterval 节流且成功后记录 lastAutoSwitchTime；
// 人工/管理驱动（SwitchToOptimal/Rotate）传 false 不受限。
//
// 单飞顺序：先本地 switching 标志（防同实例并发切换），后分布式锁（防跨
// 实例并发切换，注入了 switchLock 时）。释放顺序逆序：defer 后注册先执行，
// 即先释放分布式锁，再清除本地单飞标志。未抢到锁（另一实例在切）按 Merged
// 语义处理（不执行、不冷却）；锁获取失败 fail-closed 返回 Failed，绝不双
// 实例并发切换。
func (c *MihomoClient) doSwitch(ctx context.Context, reason string, blacklistCurrent, throttled bool) (string, MihomoSwitchResult) {
	c.mu.Lock()
	if throttled && !c.lastAutoSwitchTime.IsZero() && time.Since(c.lastAutoSwitchTime) < c.switchMinInterval {
		c.mu.Unlock()
		c.log().Info("mihomo_switch_throttled", "reason", reason, "interval", c.switchMinInterval.String())
		return "", MihomoSwitchMerged
	}
	if c.switching {
		c.mu.Unlock()
		c.log().Info("mihomo_switch_merged", "reason", reason)
		return "", MihomoSwitchMerged
	}
	c.switching = true
	groupKey := c.config.GroupName
	distLock := c.switchLock
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		c.switching = false
		c.mu.Unlock()
	}()

	cfg, ok := c.configSnapshot()
	if !ok {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "config", "error", "Mihomo 未启用或未配置")
		return "", MihomoSwitchFailed
	}

	if distLock != nil {
		// 跨实例互斥：ttl 必须覆盖最坏切换窗口 = (MaxAttempts+1) 次尝试 ×
		// 每轮耗时（waitExitIPChange 轮询至 VerifyTimeout 截止 + 每轮
		// SelectOptimal/SwitchNode/verifySwitch 的 HTTP 往返
		// perAttemptHTTPBudget），再加 clearanceLockGrace 余量（含切换前
		// 的 GetCurrentNode 与初始出口 IP 探测）；ttl 只按 VerifyTimeout
		// 计算会在切换完成前提前过期，锁被第二实例抢走导致并发切换。
		ttl := time.Duration(max(1, cfg.MaxAttempts)+1)*(cfg.VerifyTimeout+perAttemptHTTPBudget) + clearanceLockGrace
		release, acquired, err := distLock.Acquire(ctx, mihomoSwitchLockKeyPrefix+groupKey, ttl)
		if err != nil {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "switch_lock", "group", groupKey, "error", err)
			return "", MihomoSwitchFailed
		}
		if !acquired {
			c.log().Info("mihomo_switch_merged", "reason", reason, "stage", "switch_lock", "group", groupKey)
			return "", MihomoSwitchMerged
		}
		defer release() // 后注册：先于本地单飞清除执行
	}
	current, err := c.GetCurrentNode(ctx)
	if err != nil {
		c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "get_current", "error", err)
		return "", MihomoSwitchFailed
	}

	// 出口 IP 校验：切换前记录旧 IP。探测失败（返回空串）不得判定切换失败，
	// 降级为仅节点级验证。
	verifyIP := cfg.ExitProbeProxyURL != ""
	oldIP := ""
	if verifyIP {
		oldIP = c.probeExitIP(ctx, cfg)
		if oldIP == "" {
			verifyIP = false
			c.log().Info("mihomo_switch_exit_probe_skipped", "reason", reason, "error", "exit IP probe unavailable, degrading to node-level verification")
		}
	}

	var node string
	epochCommitted := false
	for attempt := 1; ; attempt++ {
		node, err = c.SelectOptimal(ctx, true)
		if err != nil {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "select", "current", current, "error", err)
			return "", MihomoSwitchFailed
		}
		if blacklistCurrent && current != "" && current != node {
			// 不单独 bump：本轮随后必然 bumpEpochLocked（epochCommitted 提交），
			// 封禁引起的候选集变化与出口变化同代际，重复 bump 只会多作废一次
			// clearance。与公共 BanNode 的"封禁即 bump"差异是有意的。
			// 事件照发（发布本身不 bump）：其他实例经 MergeBlacklistEvent
			// 收敛本地黑名单，保持与公共 BanNode 一致的跨实例可见性。
			c.mu.Lock()
			c.blacklist[current] = time.Now().UTC().Add(mihomoBlacklistTTL)
			c.publishBlacklistEventLocked(repository.EgressEventNodeBanned, current)
			c.mu.Unlock()
		}
		if err := c.SwitchNode(ctx, node); err != nil {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "switch", "current", current, "target", node, "error", err)
			return "", MihomoSwitchFailed
		}
		if err := c.verifySwitch(ctx, node); err != nil {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "verify", "current", current, "target", node, "error", err)
			return "", MihomoSwitchFailed
		}
		if !epochCommitted {
			// G7：SwitchNode PUT 已生效且节点级验证通过，出口选择即已变更，
			// 立即提交代际号，缩短"出口已变但 epoch 未提交"窗口——窗口内
			// ensureClearance 仍用旧 epoch 计算指纹，提交后被判定不新鲜而
			// 浪费一次求解。同 IP 重试循环中仅提交一次。
			c.switchCount.Add(1)
			c.mu.Lock()
			c.bumpEpochLocked()
			c.mu.Unlock()
			epochCommitted = true
		}
		if !verifyIP {
			break
		}
		newIP, sawValid := c.waitExitIPChange(ctx, cfg, oldIP)
		if ctx.Err() != nil {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "exit_ip", "current", current, "target", node, "error", ctx.Err())
			return "", MihomoSwitchFailed
		}
		if !sawValid || newIP != oldIP {
			// 探测全程失败（降级为仅节点级验证）或出口 IP 已变化：成功。
			break
		}
		// 出口 IP 未变化：封禁目标节点并重选重试。首次提交的 bump 已覆盖
		// 本轮封禁的候选集变化（与公共 BanNode 的差异是有意的，同上）。
		if attempt > cfg.MaxAttempts {
			c.log().Warn("mihomo_switch_failed", "reason", reason, "stage", "exit_ip", "current", current, "target", node, "old_ip", oldIP, "error", "exit IP did not change after retries")
			return "", MihomoSwitchFailed
		}
		c.mu.Lock()
		c.blacklist[node] = time.Now().UTC().Add(mihomoBlacklistTTL)
		c.publishBlacklistEventLocked(repository.EgressEventNodeBanned, node)
		c.mu.Unlock()
		c.log().Warn("mihomo_switch_same_ip_retry", "reason", reason, "attempt", attempt, "target", node, "old_ip", oldIP)
	}
	// P1-4：追平共享 epoch（他实例可能刚 bump 过共享值）；节流合并，best-effort。
	c.refreshEpochThrottled()
	if throttled {
		c.mu.Lock()
		c.lastAutoSwitchTime = time.Now()
		c.mu.Unlock()
	}
	c.log().Info("mihomo_switch", "reason", reason, "from", current, "to", node, "switch_count", c.switchCount.Load(), "epoch", c.epoch.Load())
	return node, MihomoSwitchDone
}

// ProbeGroupDelays 调用 Mihomo REST API（GET /group/{group}/delay）探测组内
// 全部节点的延迟（url 为延迟测试目标，timeout 为单节点探测截止时长），返回
// 节点名 -> 毫秒 的映射；探测失败（不可达/超时）的节点为 0 或缺失。整个请求
// 失败返回错误。对 select 组只记录延迟、不改变当前选择（mihomo 仅对
// URLTest/Fallback 自动组清除 fixed 选择），因此对生产出口零扰动。
//
// 探测预算以本函数的独立 30s client 为准，剥离调用方 deadline
// （context.WithoutCancel）：调用方常带短超时（如状态 API 的 3s ctx），
// 而整组探测跨几十个节点需 5-8s，继承调用方 deadline 会必然掐断探测。
func (c *MihomoClient) ProbeGroupDelays(ctx context.Context, groupName, probeURL string, timeout time.Duration) (map[string]int, error) {
	cfg, ok := c.configSnapshot()
	if !ok {
		return nil, errors.New("Mihomo 未启用或未配置")
	}
	if strings.TrimSpace(groupName) == "" {
		groupName = cfg.GroupName
	}
	if timeout <= 0 {
		timeout = mihomoDelayProbeTimeout
	}
	endpoint := cfg.APIURL + "/group/" + url.PathEscape(groupName) + "/delay?url=" + url.QueryEscape(strings.TrimSpace(probeURL)) + "&timeout=" + strconv.Itoa(int(timeout/time.Millisecond))
	request, err := http.NewRequestWithContext(context.WithoutCancel(ctx), http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("创建 Mihomo 延迟探测请求: %w", err)
	}
	// 整组探测跨几十个节点（单节点预算 mihomoDelayProbeTimeout），总耗时
	// 远超通用 client 的 5s 总超时，使用独立的宽松预算 client。
	probeClient := &http.Client{Timeout: mihomoDelayProbeHTTPTimeout}
	response, err := probeClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("调用 Mihomo 延迟探测 API: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxMihomoResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("读取 Mihomo 延迟响应: %w", err)
	}
	if len(body) > maxMihomoResponseBytes {
		return nil, errors.New("Mihomo 响应过大")
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Mihomo 返回 HTTP %d", response.StatusCode)
	}
	var delays map[string]int
	if err := json.Unmarshal(body, &delays); err != nil {
		return nil, fmt.Errorf("解析 Mihomo 延迟响应: %w", err)
	}
	return delays, nil
}

// delayProbe 在配置了 DelayProbeURL 时对组内可用节点做一次主动延迟探测，
// 返回可用（delay>0 且未被黑名单排除）节点的最新延迟；未配置或探测失败
// 时 ok 为 false，调用方回退历史/首可用节点。
func (c *MihomoClient) delayProbe(ctx context.Context, groupName string, available []string) (map[string]int, bool) {
	cfg, ok := c.configSnapshot()
	if !ok || strings.TrimSpace(cfg.DelayProbeURL) == "" {
		return nil, false
	}
	delays, err := c.ProbeGroupDelays(ctx, groupName, cfg.DelayProbeURL, mihomoDelayProbeTimeout)
	if err != nil {
		c.log().Warn("mihomo_delay_probe_failed", "group", groupName, "error", err)
		return nil, false
	}
	fresh := make(map[string]int, len(available))
	for name, delay := range delays {
		if delay <= 0 || !mihomoContains(available, name) {
			continue
		}
		if current, exists := fresh[name]; !exists || delay < current {
			fresh[name] = delay
		}
	}
	if len(fresh) == 0 {
		return nil, false
	}
	return fresh, true
}

// ClearDelays 清空延迟探测缓存（delayCache），使下一次 DelaySnapshot 必然
// 缓存 miss 并发起实时探测。供管理端"手动测活/刷新延迟"入口使用：状态 API
// 默认优先复用 TTL 内缓存，手动刷新需先清缓存才能拿到最新延迟。
func (c *MihomoClient) ClearDelays() {
	c.mu.Lock()
	clear(c.delayCache)
	c.mu.Unlock()
}

// DelaySnapshot 返回组内可用成员（available 限定候选集）的延迟快照，供
// 状态 API 展示：优先复用缓存（TTL mihomoDelayCacheTTL，节流状态轮询），
// 缓存过期或缺失时调用一次主动探测（需配置 DelayProbeURL）。并发探测按
// groupName 单飞合并（delayProbes），同一窗口只发起一次真实探测；探测
// 发起后 epoch 变化（切换/黑名单变动已清缓存）则丢弃结果不写回缓存，
// 避免陈旧数据重新填充。探测失败或全部节点不可用时 ok 为 false；失败也
// 会短时缓存，避免每轮轮询重试打爆探测端点。groupName 为空时使用客户端
// 当前配置组。
func (c *MihomoClient) DelaySnapshot(ctx context.Context, groupName string, available []string) (map[string]int, bool) {
	if len(available) == 0 {
		return nil, false
	}
	cfg, ok := c.configSnapshot()
	if !ok {
		return nil, false
	}
	if strings.TrimSpace(groupName) == "" {
		groupName = cfg.GroupName
	}
	c.mu.Lock()
	entry, hit := c.delayCache[groupName]
	c.mu.Unlock()
	if hit && time.Since(entry.at) < mihomoDelayCacheTTL {
		return entry.delays, entry.ok
	}
	if strings.TrimSpace(cfg.DelayProbeURL) == "" {
		return nil, false
	}
	// 缓存 miss：探测段按组单飞，合并并发调用（状态 API 每 2s 轮询、
	// 多标签页/多实例）的同窗口真实探测；过滤 available 留在单飞之外，
	// 各调用方按自己的候选集取子集。
	probeEpoch := c.epoch.Load()
	raw, err, _ := c.delayProbes.Do(groupName, func() (any, error) {
		return c.ProbeGroupDelays(ctx, groupName, cfg.DelayProbeURL, mihomoDelayProbeTimeout)
	})
	var fresh map[string]int
	if err != nil {
		c.log().Warn("mihomo_delay_probe_failed", "group", groupName, "error", err)
	} else {
		fresh = make(map[string]int, len(available))
		for name, delay := range raw.(map[string]int) {
			if delay <= 0 || !mihomoContains(available, name) {
				continue
			}
			if current, exists := fresh[name]; !exists || delay < current {
				fresh[name] = delay
			}
		}
	}
	ok = len(fresh) > 0
	// 探测期间 epoch 变化：结果对应的出口状态已过期（bumpEpochLocked 已
	// 清空缓存），丢弃不写缓存，避免陈旧数据重新填充。
	if c.epoch.Load() == probeEpoch {
		c.mu.Lock()
		c.delayCache[groupName] = mihomoDelayCache{delays: fresh, ok: ok, at: time.Now()}
		c.mu.Unlock()
	}
	return fresh, ok
}

// verifySwitch 切换后拉取一次组状态确认 now 已是目标节点。Mihomo 对 PUT
// 返回 204 不代表选择器立即生效（URLTest 组可能异步重新选点）；验证失败
// 时上层按未生效处理（回退 Go 节点冷却）。
func (c *MihomoClient) verifySwitch(ctx context.Context, target string) error {
	now, err := c.GetCurrentNode(ctx)
	if err != nil {
		return fmt.Errorf("验证 Mihomo 切换结果: %w", err)
	}
	if now != target {
		return fmt.Errorf("Mihomo 切换后当前节点为 %q，期望 %q", now, target)
	}
	return nil
}

// probeExitIP 通过本地 mihomo 代理端口探测当前出口 IP（GET IPProbeURL）。
// 任何失败（代理不可达、非 200、解析不出 IP）都返回 ""；探测失败不得判定
// 切换失败，由调用方降级为仅节点级验证。
func (c *MihomoClient) probeExitIP(ctx context.Context, cfg MihomoConfig) string {
	proxyURL, err := url.Parse(cfg.ExitProbeProxyURL)
	if err != nil {
		return ""
	}
	probeClient := &http.Client{
		Timeout:   mihomoExitProbeTimeout,
		Transport: &http.Transport{Proxy: http.ProxyURL(proxyURL)},
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, cfg.IPProbeURL, nil)
	if err != nil {
		return ""
	}
	response, err := probeClient.Do(request)
	if err != nil {
		return ""
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 8192))
	if err != nil || response.StatusCode != http.StatusOK {
		return ""
	}
	return mihomoParseExitIP(string(body))
}

// waitExitIPChange 以 1s 间隔轮询出口 IP 直到其不同于 oldIP 或截止时间
// (VerifyTimeout) 到期。返回 (最后观察到的合法 IP, 是否至少观察到一次合法
// IP)：IP 变化立即返回新 IP；截止时仍为旧 IP 返回 (oldIP, true)；全程探测
// 失败返回 ("", false)。调用方须先检查 ctx.Err()。
//
// 重试循环以本地 deadline 为界（每轮探测前先检查），不依赖 ctx：部分调用方
// （如 Manager.maybeMihomoFallback）传入 context.Background()，ctx.Err() 永不
// 触发，只有本地 deadline 保证循环有界。
func (c *MihomoClient) waitExitIPChange(ctx context.Context, cfg MihomoConfig, oldIP string) (string, bool) {
	deadline := time.Now().Add(cfg.VerifyTimeout)
	lastIP, sawValid := "", false
	for {
		if time.Now().After(deadline) {
			return lastIP, sawValid
		}
		ip := c.probeExitIP(ctx, cfg)
		if ip != "" {
			sawValid = true
			lastIP = ip
			if ip != oldIP {
				return ip, true
			}
		}
		if ctx.Err() != nil {
			return lastIP, sawValid
		}
		if time.Now().After(deadline) {
			return lastIP, sawValid
		}
		time.Sleep(mihomoExitProbePollPeriod)
	}
}

// mihomoParseExitIP 从探测响应体解析出口 IP：优先逐行匹配 /cdn-cgi/trace
// 风格的 "ip=..." 行，否则尝试把整个响应体当作纯文本 IP 解析。
func mihomoParseExitIP(body string) string {
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimRight(line, "\r")
		if value, ok := strings.CutPrefix(line, "ip="); ok {
			if ip := strings.TrimSpace(value); ip != "" {
				return ip
			}
		}
	}
	if ip := net.ParseIP(strings.TrimSpace(body)); ip != nil {
		return ip.String()
	}
	return ""
}

// fetchGroup 拉取并解析代理组完整响应。
func (c *MihomoClient) fetchGroup(ctx context.Context) (mihomoGroup, error) {
	return c.fetchGroupNamed(ctx, "")
}

// fetchGroupNamed 拉取并解析指定代理组完整响应；groupName 为空时使用客户端
// 当前配置组。
func (c *MihomoClient) fetchGroupNamed(ctx context.Context, groupName string) (mihomoGroup, error) {
	cfg, ok := c.configSnapshot()
	if !ok {
		return mihomoGroup{}, errors.New("Mihomo 未启用或未配置")
	}
	if strings.TrimSpace(groupName) == "" {
		groupName = cfg.GroupName
	}
	endpoint := cfg.APIURL + "/proxies/" + url.PathEscape(groupName)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return mihomoGroup{}, fmt.Errorf("创建 Mihomo 请求: %w", err)
	}
	response, err := c.client.Do(request)
	if err != nil {
		return mihomoGroup{}, fmt.Errorf("调用 Mihomo API: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxMihomoResponseBytes+1))
	if err != nil {
		return mihomoGroup{}, fmt.Errorf("读取 Mihomo 响应: %w", err)
	}
	if len(body) > maxMihomoResponseBytes {
		return mihomoGroup{}, errors.New("Mihomo 响应过大")
	}
	if response.StatusCode != http.StatusOK {
		return mihomoGroup{}, fmt.Errorf("Mihomo 返回 HTTP %d", response.StatusCode)
	}
	var group mihomoGroup
	if err := json.Unmarshal(body, &group); err != nil {
		return mihomoGroup{}, fmt.Errorf("解析 Mihomo 响应: %w", err)
	}
	return group, nil
}

// configSnapshot 返回当前配置；配置未启用或缺失关键字段时 ok 为 false。
func (c *MihomoClient) configSnapshot() (MihomoConfig, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	cfg := c.config
	return cfg, cfg.Enabled && cfg.APIURL != "" && cfg.GroupName != ""
}

// updateBlacklistOnNodeChangeLocked 在节点集或默认连接节点变化时清空黑名单
// 并递增出口代际版本号：节点集变化（如订阅更新）意味着旧封禁失效，且上游
// 可能直接改写 now（默认连接节点），出口 IP 随之变化，旧 clearance 必然作废。
// 首次调用只建立快照不递增。调用方须持有 c.mu。
func (c *MihomoClient) updateBlacklistOnNodeChangeLocked(all []string, now string) {
	current := make(map[string]struct{}, len(all))
	for _, name := range all {
		current[name] = struct{}{}
	}
	first := c.lastNodeSet == nil
	nodeSetChanged := false
	if first {
		c.lastNodeSet = current
	} else if len(c.lastNodeSet) != len(current) {
		nodeSetChanged = true
	} else {
		for name := range current {
			if _, exists := c.lastNodeSet[name]; !exists {
				nodeSetChanged = true
				break
			}
		}
	}
	nowChanged := c.lastNow != "" && now != "" && now != c.lastNow
	if nodeSetChanged {
		clear(c.blacklist)
		c.lastNodeSet = current
	}
	if !first && (nodeSetChanged || nowChanged) {
		c.bumpEpochLocked()
	}
	if now != "" {
		c.lastNow = now
	}
}

// mihomoAvailable 过滤出未被黑名单覆盖的节点；excludeCurrent 时从候选中
// 剔除当前节点（仅当存在时）。
func mihomoAvailable(all []string, now string, banned map[string]struct{}, excludeCurrent bool) []string {
	available := make([]string, 0, len(all))
	for _, name := range all {
		if _, isBanned := banned[name]; !isBanned {
			available = append(available, name)
		}
	}
	if excludeCurrent && now != "" {
		available = mihomoWithout(available, now)
	}
	return available
}

// mihomoWithout 返回去除指定节点后的新切片（保持原顺序）。
func mihomoWithout(nodes []string, name string) []string {
	filtered := make([]string, 0, len(nodes))
	for _, node := range nodes {
		if node != name {
			filtered = append(filtered, node)
		}
	}
	return filtered
}

// mihomoContains 判断切片是否包含指定节点。
func mihomoContains(nodes []string, name string) bool {
	for _, node := range nodes {
		if node == name {
			return true
		}
	}
	return false
}
