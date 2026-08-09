package egress

import (
	"strings"
	"time"
)

type Mode string

const (
	ModeDirect Mode = "direct"
	ModeSingle Mode = "single"
	ModePool   Mode = "pool"
)

const LastErrorTransport = "transport error"

type Scope string

const (
	ScopeBuild        Scope = "grok_build"
	ScopeWeb          Scope = "grok_web"
	ScopeConsole      Scope = "grok_console"
	ScopeWebAsset     Scope = "grok_web_asset"
	ScopeConsoleAsset Scope = "grok_console_asset"
)

// NodeType 区分普通出口节点与 Mihomo 组通道节点。通道节点由操作员显式
// 声明（ProxyURL 指向本地 Mihomo 端口），多个节点共享同一条组出口，质量
// 守卫必须隐藏它们（探测/隔离单节点无效），转而操作 Mihomo 实际成员。
type NodeType string

const (
	// NodeTypeStandard 是普通独立出口节点（零值，兼容历史数据）。
	NodeTypeStandard NodeType = ""
	// NodeTypeMihomo 是 Mihomo 组通道节点：流量经本地 Mihomo 混合端口
	// 进入组出口，探测与隔离必须以组为单位而非节点。
	NodeTypeMihomo NodeType = "mihomo"
)

// Normalized 将历史空值/未知值归一到标准节点。
func (value NodeType) Normalized() NodeType {
	switch value {
	case NodeTypeStandard, NodeTypeMihomo:
		return value
	default:
		return NodeTypeStandard
	}
}

type Node struct {
	ID                          uint64
	Name                        string
	Scope                       Scope
	Type                        NodeType
	Enabled                     bool
	ProxyPool                   bool
	SourceID                    uint64
	SourceKey                   string
	AccountCapacity             int
	EncryptedProxyURL           string
	UserAgent                   string
	EncryptedCloudflareCookie   string
	ClearanceRefreshedAt        *time.Time
	ClearanceFingerprint        string
	ClearanceBindingFingerprint string
	Health                      float64
	FailureCount                int
	CooldownUntil               *time.Time
	LastError                   string
	ProbeStatus                 ProbeStatus
	LastProbedAt                *time.Time
	ProbeLatencyMS              int
	ExitIP                      string
	ProbeError                  string
	ProbeProvider               ProbeProvider
	IPv4Probe                   ProbeFamilyResult
	IPv6Probe                   ProbeFamilyResult
	AssignedAccountCount        int
	CreatedAt                   time.Time
	UpdatedAt                   time.Time
}

// IsMihomoSynced 报告该节点是否由 Mihomo 测试组成员同步器维护。同步节点
// 默认禁用、仅供质量守卫逐成员探测，绝不可作为生产出口或固定回退节点。
func (n Node) IsMihomoSynced() bool {
	return strings.HasPrefix(n.SourceKey, "mihomo:")
}

// IsMihomoChannel 报告该节点是否为操作员显式声明的 Mihomo 组通道节点。
// 通道节点共享同一条组出口，质量守卫必须隐藏它们（探测/隔离单节点无效）。
func (n Node) IsMihomoChannel() bool {
	return n.Type == NodeTypeMihomo
}

// IsMihomoSynced 报告该公开节点是否由 Mihomo 测试组成员同步器维护（与
// Node.IsMihomoSynced 同判据，供 handler 等只持有 PublicNode 的调用方使用）。
func (n PublicNode) IsMihomoSynced() bool {
	return strings.HasPrefix(n.SourceKey, "mihomo:")
}

type PublicNode struct {
	ID                   uint64
	Name                 string
	Scope                Scope
	Type                 NodeType
	Enabled              bool
	ProxyConfigured      bool
	ProxyPool            bool
	SourceID             uint64
	SourceKey            string
	AccountCapacity      int
	UserAgent            string
	CookieConfigured     bool
	AccountBoundProxy    bool
	Health               float64
	FailureCount         int
	CooldownUntil        *time.Time
	LastError            string
	ProbeStatus          ProbeStatus
	LastProbedAt         *time.Time
	ProbeLatencyMS       int
	ExitIP               string
	ProbeError           string
	ProbeProvider        ProbeProvider
	IPv4Probe            ProbeFamilyResult
	IPv6Probe            ProbeFamilyResult
	AssignedAccountCount int
	CreatedAt            time.Time
	UpdatedAt            time.Time
}

type ProbeStatus string

const (
	ProbeStatusUnknown   ProbeStatus = "unknown"
	ProbeStatusHealthy   ProbeStatus = "healthy"
	ProbeStatusUnhealthy ProbeStatus = "unhealthy"
)

func (value ProbeStatus) IsValid() bool {
	switch value {
	case ProbeStatusUnknown, ProbeStatusHealthy, ProbeStatusUnhealthy:
		return true
	default:
		return false
	}
}

// ProbeResult contains only operational metadata. It never stores or exposes
// proxy credentials.
type ProbeResult struct {
	Status    ProbeStatus
	TestedAt  time.Time
	LatencyMS int
	ExitIP    string
	Error     string
	Provider  ProbeProvider
	IPv4      ProbeFamilyResult
	IPv6      ProbeFamilyResult
}

// ProbeFamilyResult stores one address family's independent connectivity
// result. A zero TestedAt represents a family that has not been tested yet.
type ProbeFamilyResult struct {
	Status    ProbeStatus
	TestedAt  time.Time
	LatencyMS int
	ExitIP    string
	Error     string
}

// SubscriptionSource stores a write-only remote proxy subscription. The URL
// remains encrypted at rest and must never be returned by management APIs.
type SubscriptionSource struct {
	ID                     uint64
	Name                   string
	Scope                  Scope
	Enabled                bool
	EncryptedURL           string
	RefreshIntervalSeconds int
	DefaultAccountCapacity int
	LastSyncedAt           *time.Time
	NextSyncAt             *time.Time
	LastSyncImported       int
	LastSyncError          string
	CreatedAt              time.Time
	UpdatedAt              time.Time
}

type PublicSubscriptionSource struct {
	ID                     uint64
	Name                   string
	Scope                  Scope
	Enabled                bool
	URLConfigured          bool
	RefreshIntervalSeconds int
	DefaultAccountCapacity int
	LastSyncedAt           *time.Time
	NextSyncAt             *time.Time
	LastSyncImported       int
	LastSyncError          string
	Managed                bool
	CreatedAt              time.Time
	UpdatedAt              time.Time
}

// FallbackMode controls what happens when no primary egress node can be
// acquired for a request scope. The default is deliberately none so upgrades
// preserve the existing fail-closed behavior.
type FallbackMode string

const (
	FallbackModeNone   FallbackMode = "none"
	FallbackModeDirect FallbackMode = "direct"
	FallbackModeFixed  FallbackMode = "fixed"
)

func (value FallbackMode) IsValid() bool {
	switch value {
	case FallbackModeNone, FallbackModeDirect, FallbackModeFixed:
		return true
	default:
		return false
	}
}

// Normalized maps the zero value left by pre-fallback database rows to the
// conservative disabled mode.
func (value FallbackMode) Normalized() FallbackMode {
	if value == "" {
		return FallbackModeNone
	}
	return value
}

type FallbackConfig struct {
	Mode   FallbackMode
	NodeID uint64
}

type ProbeProvider string

const (
	ProbeProviderIPInfo     ProbeProvider = "ipinfo"
	ProbeProviderCloudflare ProbeProvider = "cloudflare"
)

func (value ProbeProvider) IsValid() bool {
	return value == ProbeProviderIPInfo || value == ProbeProviderCloudflare
}

func (value ProbeProvider) Normalized() ProbeProvider {
	if !value.IsValid() {
		return ProbeProviderCloudflare
	}
	return value
}

// OperationsConfig controls background probe, account assignment, and egress
// fallback work. It defaults to a conservative disabled state for mutations
// and fallback routing.
type OperationsConfig struct {
	ProbeProvider             ProbeProvider
	ProbeIntervalSeconds      int
	AutoAssignEnabled         bool
	AutoBalanceEnabled        bool
	AssignmentIntervalSeconds int
	// EncryptedSubscriptionProxyURL is the optional proxy used only when
	// fetching remote proxy subscription sources. It is write-only at rest.
	EncryptedSubscriptionProxyURL string
	Fallbacks                     map[Scope]FallbackConfig
	UpdatedAt                     time.Time
}

func DefaultOperationsConfig() OperationsConfig {
	return OperationsConfig{
		ProbeProvider:             ProbeProviderCloudflare,
		ProbeIntervalSeconds:      900,
		AssignmentIntervalSeconds: 300,
		Fallbacks: map[Scope]FallbackConfig{
			ScopeBuild:        {Mode: FallbackModeNone},
			ScopeWeb:          {Mode: FallbackModeNone},
			ScopeConsole:      {Mode: FallbackModeNone},
			ScopeWebAsset:     {Mode: FallbackModeNone},
			ScopeConsoleAsset: {Mode: FallbackModeNone},
		},
	}
}

// FallbackFor always returns a canonical, safe fallback value. It accepts
// sparse maps so older callers and historical records remain compatible.
func (value OperationsConfig) FallbackFor(scope Scope) FallbackConfig {
	fallback := value.Fallbacks[scope]
	fallback.Mode = fallback.Mode.Normalized()
	if fallback.Mode != FallbackModeFixed {
		fallback.NodeID = 0
	}
	return fallback
}

// SupportsScope reports whether a node can serve requests for the supplied
// scope. Console may intentionally reuse a Web browser proxy. Resource scopes
// may reuse their provider's primary node so explicit account bindings remain
// authoritative when no independently bound resource identity exists.
func SupportsScope(nodeScope, requestScope Scope) bool {
	if nodeScope == requestScope {
		return true
	}
	switch requestScope {
	case ScopeWebAsset, ScopeConsole:
		return nodeScope == ScopeWeb
	case ScopeConsoleAsset:
		return nodeScope == ScopeConsole || nodeScope == ScopeWeb
	default:
		return false
	}
}
