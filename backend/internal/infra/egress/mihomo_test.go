package egress

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// mihomoTestServer 返回一个模拟 Clash API 的测试服务。
// group 由调用方持有并在 handler 内通过 mutex 读取，便于测试中变更节点集。
func mihomoTestServer(t *testing.T, group *mihomoGroup, mu *sync.Mutex, switchStatus int, switchDelay time.Duration, switches *[]string, switchMu *sync.Mutex) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			w.Header().Set("Content-Type", "application/json")
			mu.Lock()
			_ = json.NewEncoder(w).Encode(group)
			mu.Unlock()
		case http.MethodPut:
			if switchDelay > 0 {
				time.Sleep(switchDelay)
			}
			var body struct {
				Name string `json:"name"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			switchMu.Lock()
			*switches = append(*switches, body.Name)
			switchMu.Unlock()
			mu.Lock()
			group.Now = body.Name // 模拟切换生效，使 verifySwitch 通过
			mu.Unlock()
			w.WriteHeader(switchStatus)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
}

// mihomoIPServer 返回 IP 回显服务：每次请求返回 body() 的当前值，
// 模拟真实出口 IP 探测端点（/cdn-cgi/trace 风格）。
func mihomoIPServer(body func() string) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = io.WriteString(w, body())
	}))
}

// mihomoProxyServer 模拟本地 mihomo 代理端口：把收到的代理请求转发到
// target 后原样回传（探测请求经 ExitProbeProxyURL 到达这里）。
func mihomoProxyServer(target *httptest.Server) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		targetURL := r.URL.String()
		if !r.URL.IsAbs() {
			targetURL = target.URL + r.URL.RequestURI()
		}
		request, err := http.NewRequest(r.Method, targetURL, r.Body)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		defer response.Body.Close()
		w.WriteHeader(response.StatusCode)
		_, _ = io.Copy(w, response.Body)
	}))
}

func mihomoTestGroup() (mihomoGroup, *sync.Mutex) {
	group := mihomoGroup{
		All: []string{"slow", "fast", "dead"},
		Now: "slow",
		Providers: map[string]mihomoProvider{
			"p1": {Nodes: []mihomoNode{
				{Name: "slow", History: []mihomoDelay{{Delay: 300}}},
				{Name: "fast", History: []mihomoDelay{{Delay: 50}}},
				{Name: "dead", History: []mihomoDelay{{Delay: -1}}},
			}},
		},
	}
	return group, &sync.Mutex{}
}

func TestGetGroupNodes(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	nodes, err := client.GetGroupNodes(context.Background())
	if err != nil {
		t.Fatalf("GetGroupNodes: %v", err)
	}
	if len(nodes) != 3 || nodes[0] != "slow" || nodes[1] != "fast" || nodes[2] != "dead" {
		t.Fatalf("unexpected nodes: %v", nodes)
	}
}

func TestGetGroupNodesNonOK(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if _, err := client.GetGroupNodes(context.Background()); err == nil {
		t.Fatal("expected error on non-200")
	}
	if _, err := client.GetCurrentNode(context.Background()); err == nil {
		t.Fatal("expected error on non-200")
	}
}

func TestSwitchNode(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if err := client.SwitchNode(context.Background(), "fast"); err != nil {
		t.Fatalf("SwitchNode: %v", err)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("unexpected switches: %v", switches)
	}
}

func TestSwitchNodeNon204(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusBadRequest, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if err := client.SwitchNode(context.Background(), "fast"); err == nil {
		t.Fatal("expected error on non-204")
	}
}

func TestSelectOptimal(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	ctx := context.Background()

	best, err := client.SelectOptimal(ctx, false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "fast" {
		t.Fatalf("min-delay selection: got %q, want fast", best)
	}

	client.mu.Lock()
	client.blacklist["fast"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.mu.Unlock()
	best, err = client.SelectOptimal(ctx, false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "slow" {
		t.Fatalf("blacklist exclusion: got %q, want slow", best)
	}

	best, err = client.SelectOptimal(ctx, true)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	// 当前节点 slow 被排除；dead 虽无可用延迟仍是唯一候选，作为兜底返回。
	if best != "dead" {
		t.Fatalf("exclude current: got %q, want dead", best)
	}

	client.mu.Lock()
	client.blacklist["slow"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.blacklist["dead"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.mu.Unlock()
	if _, err = client.SelectOptimal(ctx, false); err == nil {
		t.Fatal("expected error when all nodes blacklisted")
	}
	// 全部被黑名单覆盖时不自动清空，等待节点集刷新或配置变更，避免切换循环。
	if banned := client.BannedNodes(); len(banned) != 3 {
		t.Fatalf("blacklist should be preserved, got %v", banned)
	}
}

func TestSelectOptimalNoDelayData(t *testing.T) {
	group := mihomoGroup{All: []string{"first", "second"}, Now: "first"}
	groupMu := &sync.Mutex{}
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	best, err := client.SelectOptimal(context.Background(), false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "first" {
		t.Fatalf("no delay data: got %q, want first", best)
	}
}

func TestSwitchAndBlacklistCurrent(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("expected a switch to happen, got %v", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	banned := client.BannedNodes()
	if len(banned) != 1 || banned[0] != "slow" {
		t.Fatalf("current node should be blacklisted: %v", banned)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("should switch to optimal 'fast', got %v", switches)
	}
}

func TestSwitchToOptimal(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	node, result := client.SwitchToOptimal(context.Background(), "manual")
	if result != MihomoSwitchDone {
		t.Fatalf("expected a switch to happen, got %v", result)
	}
	if node != "fast" {
		t.Fatalf("should switch to optimal 'fast', got %q", node)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	if banned := client.BannedNodes(); len(banned) != 0 {
		t.Fatalf("manual switch must not touch the blacklist: %v", banned)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("should switch to optimal 'fast', got %v", switches)
	}
}

func TestClearBlacklist(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	client.SwitchAndBlacklistCurrent(context.Background(), "test")
	if cleared := client.ClearBlacklist(); cleared != 1 {
		t.Fatalf("cleared: got %d, want 1", cleared)
	}
	if banned := client.BannedNodes(); len(banned) != 0 {
		t.Fatalf("blacklist should be empty after clear: %v", banned)
	}
}

func TestSwitchAndBlacklistCurrentSingleFlight(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 150*time.Millisecond, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	results := make([]MihomoSwitchResult, 4)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range results {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			results[i] = client.SwitchAndBlacklistCurrent(context.Background(), "test")
		}()
	}
	close(start)
	wg.Wait()

	done, merged := 0, 0
	for _, result := range results {
		switch result {
		case MihomoSwitchDone:
			done++
		case MihomoSwitchMerged:
			merged++
		case MihomoSwitchFailed:
			t.Fatalf("unexpected failed result: %v", results)
		}
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if done != 1 || merged != 3 || len(switches) != 1 {
		t.Fatalf("single-flight violated: done=%d merged=%d put=%d", done, merged, len(switches))
	}
}

func TestNodeSetChangeClearsBlacklist(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	ctx := context.Background()
	if _, err := client.SelectOptimal(ctx, false); err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	client.mu.Lock()
	client.blacklist["fast"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.mu.Unlock()

	groupMu.Lock()
	group.All = []string{"x", "y", "z"}
	group.Now = "x"
	group.Providers = nil
	groupMu.Unlock()

	if _, err := client.SelectOptimal(ctx, false); err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if banned := client.BannedNodes(); len(banned) != 0 {
		t.Fatalf("node set change should clear blacklist, got %v", banned)
	}
}

func TestUpdateConfig(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: false, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if _, err := client.GetGroupNodes(context.Background()); err == nil {
		t.Fatal("expected error while disabled")
	}
	client.UpdateConfig(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if _, err := client.GetGroupNodes(context.Background()); err != nil {
		t.Fatalf("GetGroupNodes after enable: %v", err)
	}
	client.UpdateConfig(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: ""})
	if _, err := client.GetGroupNodes(context.Background()); err == nil {
		t.Fatal("expected error when group name cleared")
	}
}

func TestBlacklistExpiresAfterTTL(t *testing.T) {
	group := mihomoGroup{
		All: []string{"nodeA", "nodeB"},
		Now: "nodeA",
		Providers: map[string]mihomoProvider{
			"p1": {Nodes: []mihomoNode{
				{Name: "nodeA", History: []mihomoDelay{{Delay: 300}}},
				{Name: "nodeB", History: []mihomoDelay{{Delay: 50}}},
			}},
		},
	}
	groupMu := &sync.Mutex{}
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	client.mu.Lock()
	client.blacklist["nodeA"] = time.Now().UTC().Add(-time.Second) // 已过期
	client.blacklist["nodeB"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.mu.Unlock()

	banned := client.BannedNodes()
	if len(banned) != 1 || banned[0] != "nodeB" {
		t.Fatalf("expired entry must be pruned: got %v, want [nodeB]", banned)
	}
	best, err := client.SelectOptimal(context.Background(), false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "nodeA" {
		t.Fatalf("expired node must be selectable again: got %q, want nodeA", best)
	}
}

func TestSwitchVerifiesCurrentNode(t *testing.T) {
	// PUT 返回 204 但 GET 恒返回固定 now（"slow"）：verifySwitch 拉取发现
	// now 仍是旧节点，切换必须判定失败且版本号不增加。
	group, groupMu := mihomoTestGroup()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			w.Header().Set("Content-Type", "application/json")
			groupMu.Lock()
			_ = json.NewEncoder(w).Encode(group)
			groupMu.Unlock()
		case http.MethodPut:
			w.WriteHeader(http.StatusNoContent)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchFailed {
		t.Fatalf("switch must fail when now does not converge to the target, got %v", result)
	}
	if client.SwitchCount() != 0 {
		t.Fatalf("switchCount: got %d, want 0", client.SwitchCount())
	}
}

func TestSwitchAndBlacklistCurrentSkipsCurrentWhenSelectFails(t *testing.T) {
	// 当前节点 slow 与全部候选节点均被黑名单覆盖，SelectOptimal 失败：
	// 黑名单加入必须发生在 select 成功之后，slow 不得被封禁。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusInternalServerError, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	client.mu.Lock()
	client.blacklist["fast"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.blacklist["dead"] = time.Now().UTC().Add(mihomoBlacklistTTL)
	client.mu.Unlock()

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchFailed {
		t.Fatalf("expected switch to fail when every candidate is blacklisted, got %v", result)
	}
	banned := client.BannedNodes()
	if mihomoContains(banned, "slow") {
		t.Fatalf("current node must not be blacklisted when select fails: %v", banned)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("no PUT should be issued when select fails, got %v", switches)
	}
}

func TestUnsetExitProbeConfigPreservesLegacyBehavior(t *testing.T) {
	// 仅设置旧字段（无出口 IP 校验、无映射）：行为必须与旧版完全一致。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("result: got %v, want Done", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	if banned := client.BannedNodes(); len(banned) != 1 || banned[0] != "slow" {
		t.Fatalf("banned: %v", banned)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("switches: %v", switches)
	}
}

func TestExitIPChangeSuccess(t *testing.T) {
	// 切换后出口 IP 立即变化：一次尝试即成功。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	api := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer api.Close()
	ip := mihomoIPServer(func() string {
		groupMu.Lock()
		defer groupMu.Unlock()
		ipByNode := map[string]string{"slow": "10.0.0.1", "fast": "10.0.0.2"}
		return "ip=" + ipByNode[group.Now] + "\n"
	})
	defer ip.Close()
	proxy := mihomoProxyServer(ip)
	defer proxy.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: api.URL, GroupName: "XAI-GROUP",
		ExitProbeProxyURL: proxy.URL,
		IPProbeURL:        ip.URL,
		VerifyTimeout:     300 * time.Millisecond,
	})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("result: got %v, want Done", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("switches: %v", switches)
	}
}

func TestExitIPSameIPRetriesToNextNode(t *testing.T) {
	// slow 与 fast 共享同一出口 IP（粘滞会话未换 IP）：首次切换后出口 IP
	// 未变化 → 封禁 fast 并重选 dead（出口 IP 不同）→ 成功。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	api := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer api.Close()
	ip := mihomoIPServer(func() string {
		groupMu.Lock()
		defer groupMu.Unlock()
		ipByNode := map[string]string{"slow": "10.0.0.1", "fast": "10.0.0.1", "dead": "10.0.0.9"}
		return "ip=" + ipByNode[group.Now] + "\n"
	})
	defer ip.Close()
	proxy := mihomoProxyServer(ip)
	defer proxy.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: api.URL, GroupName: "XAI-GROUP",
		ExitProbeProxyURL: proxy.URL,
		IPProbeURL:        ip.URL,
		MaxAttempts:       1, // 首次 + 1 次重试
		VerifyTimeout:     300 * time.Millisecond,
	})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("result: got %v, want Done", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 2 || switches[0] != "fast" || switches[1] != "dead" {
		t.Fatalf("switches: %v", switches)
	}
	banned := client.BannedNodes()
	if len(banned) != 2 || banned[0] != "fast" || banned[1] != "slow" {
		t.Fatalf("banned: %v", banned)
	}
}

func TestExitIPSameIPAllRetriesFailed(t *testing.T) {
	// 出口 IP 永不变化：首次尝试 + 重试全部同 IP → Failed。节点级验证通过的
	// 切换已发生（出口选择确实变更），代际号在重试耗尽路径上也已提交。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	api := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer api.Close()
	ip := mihomoIPServer(func() string { return "ip=10.0.0.1\n" })
	defer ip.Close()
	proxy := mihomoProxyServer(ip)
	defer proxy.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: api.URL, GroupName: "XAI-GROUP",
		ExitProbeProxyURL: proxy.URL,
		IPProbeURL:        ip.URL,
		MaxAttempts:       1,
		VerifyTimeout:     300 * time.Millisecond,
	})
	epoch := client.Epoch()
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchFailed {
		t.Fatalf("result: got %v, want Failed", result)
	}
	// G7 新语义：首次 verifySwitch 通过即提交切换（switchCount +1、epoch +1），
	// 重试耗尽返回 Failed 不影响已提交的计数；额外的 epoch +1 来自第二次
	// SelectOptimal 观察到 now 变化（slow→fast）。
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1 (switch committed at node-level verification)", client.SwitchCount())
	}
	if client.Epoch() != epoch+2 {
		t.Fatalf("epoch: got %d, want %d (commit + now-change bump)", client.Epoch(), epoch+2)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 2 {
		t.Fatalf("switches: %v", switches)
	}
}

func TestExitProbeFailureDegradesToNodeLevel(t *testing.T) {
	// 旧 IP 探测失败（代理不可达）：切换不得失败，降级为仅节点级验证。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	api := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer api.Close()
	deadProxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	deadProxy.Close() // 探测目标已关闭：连接失败

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: api.URL, GroupName: "XAI-GROUP",
		ExitProbeProxyURL: deadProxy.URL,
		IPProbeURL:        "http://127.0.0.1:1/ip",
	})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("result: got %v, want Done (degraded)", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("switches: %v", switches)
	}
}

func TestExitProbeFailureMidVerifyDegradesToNodeLevel(t *testing.T) {
	// 旧 IP 探测成功，切换后轮询探测全程失败：降级为仅节点级验证，切换成功。
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	api := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer api.Close()
	var probes atomic.Int64
	ip := mihomoIPServer(func() string {
		if probes.Add(1) == 1 {
			return "ip=10.0.0.1\n"
		}
		return "" // 模拟后续探测失败
	})
	defer ip.Close()
	proxy := mihomoProxyServer(ip)
	defer proxy.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: api.URL, GroupName: "XAI-GROUP",
		ExitProbeProxyURL: proxy.URL,
		IPProbeURL:        ip.URL,
		VerifyTimeout:     300 * time.Millisecond,
	})
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("result: got %v, want Done (degraded)", result)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
}

func TestRotate(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	name, result := client.Rotate(context.Background(), "guard")
	if result != MihomoSwitchDone {
		t.Fatalf("Rotate: got %v, want Done", result)
	}
	if name != "fast" {
		t.Fatalf("Rotate: got %q, want fast", name)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	banned := client.BannedNodes()
	if len(banned) != 1 || banned[0] != "slow" {
		t.Fatalf("rotated-away node must be blacklisted: %v", banned)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "fast" {
		t.Fatalf("switches: %v", switches)
	}
}

func TestRotateMergedWhileSwitching(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	client.mu.Lock()
	client.switching = true // 模拟在途切换占用单飞位
	client.mu.Unlock()
	defer func() {
		client.mu.Lock()
		client.switching = false
		client.mu.Unlock()
	}()

	name, result := client.Rotate(context.Background(), "guard")
	if result != MihomoSwitchMerged {
		t.Fatalf("Rotate during in-flight switch: got %v, want Merged", result)
	}
	if name != "" {
		t.Fatalf("merged Rotate must return empty name, got %q", name)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("merged Rotate must not issue a PUT, got %v", switches)
	}
}

// mihomoGroupAndDelayServer 同时提供 GET /proxies/{group} 与
// GET /group/{group}/delay 的模拟服务，用于延迟探测与择优测试。
func mihomoGroupAndDelayServer(t *testing.T, group *mihomoGroup, mu *sync.Mutex, delays func() map[string]int) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/group/") {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(delays())
			return
		}
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/proxies/") {
			w.Header().Set("Content-Type", "application/json")
			mu.Lock()
			_ = json.NewEncoder(w).Encode(group)
			mu.Unlock()
			return
		}
		w.WriteHeader(http.StatusMethodNotAllowed)
	}))
}

func TestProbeGroupDelays(t *testing.T) {
	var seenURL string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenURL = r.URL.RequestURI()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]int{"slow": 300, "fast": 50, "dead": 0})
	}))
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	delays, err := client.ProbeGroupDelays(context.Background(), "XAI-GROUP", "http://www.gstatic.com/generate_204", 3*time.Second)
	if err != nil {
		t.Fatalf("ProbeGroupDelays: %v", err)
	}
	if delays["fast"] != 50 || delays["slow"] != 300 || delays["dead"] != 0 {
		t.Fatalf("unexpected delays: %v", delays)
	}
	if !strings.Contains(seenURL, "/group/XAI-GROUP/delay?url=") || !strings.Contains(seenURL, "timeout=3000") {
		t.Fatalf("unexpected delay request URL: %s", seenURL)
	}
}

func TestProbeGroupDelaysNonOK(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	if _, err := client.ProbeGroupDelays(context.Background(), "XAI-GROUP", "http://example.com", time.Second); err == nil {
		t.Fatal("expected error on non-200")
	}
}

func TestProbeGroupDelaysDisabled(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: false})
	if _, err := client.ProbeGroupDelays(context.Background(), "XAI-GROUP", "http://example.com", time.Second); err == nil {
		t.Fatal("expected error while disabled")
	}
}

func TestSwitchTestGroup(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-TEST-GROUP"})
	epoch := client.Epoch()
	name, result := client.SwitchTestGroup(context.Background(), "slow", "guard_probe")
	if result != MihomoSwitchDone {
		t.Fatalf("SwitchTestGroup: got %v, want Done", result)
	}
	if name != "slow" {
		t.Fatalf("SwitchTestGroup: got %q, want slow", name)
	}
	if client.SwitchCount() != 1 {
		t.Fatalf("switchCount: got %d, want 1", client.SwitchCount())
	}
	if client.Epoch() != epoch+1 {
		t.Fatalf("epoch: got %d, want %d", client.Epoch(), epoch+1)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 1 || switches[0] != "slow" {
		t.Fatalf("explicit target switch: got %v, want [slow]", switches)
	}
}

func TestSwitchTestGroupMergedWhileSwitching(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-TEST-GROUP"})
	client.mu.Lock()
	client.switching = true
	client.mu.Unlock()
	defer func() {
		client.mu.Lock()
		client.switching = false
		client.mu.Unlock()
	}()

	name, result := client.SwitchTestGroup(context.Background(), "slow", "guard_probe")
	if result != MihomoSwitchMerged {
		t.Fatalf("SwitchTestGroup during in-flight switch: got %v, want Merged", result)
	}
	if name != "" {
		t.Fatalf("merged switch must return empty name, got %q", name)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("merged switch must not issue a PUT, got %v", switches)
	}
}

func TestSwitchTestGroupEmptyTarget(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-TEST-GROUP"})
	if _, result := client.SwitchTestGroup(context.Background(), "", "guard_probe"); result != MihomoSwitchFailed {
		t.Fatalf("empty target: got %v, want Failed", result)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("empty target must not issue a PUT, got %v", switches)
	}
}

func TestSelectOptimalPrefersDelayProbeData(t *testing.T) {
	// select 组不产生 history：配置 DelayProbeURL 时 SelectOptimal 主动探测择优。
	group := mihomoGroup{All: []string{"first", "second"}, Now: "first"}
	groupMu := &sync.Mutex{}
	server := mihomoGroupAndDelayServer(t, &group, groupMu, func() map[string]int {
		return map[string]int{"first": 200, "second": 40}
	})
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP",
		DelayProbeURL: "http://www.gstatic.com/generate_204",
	})
	best, err := client.SelectOptimal(context.Background(), false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "second" {
		t.Fatalf("delay-probe selection: got %q, want second (40ms)", best)
	}
}

func TestSelectOptimalDelayProbeFailureFallsBack(t *testing.T) {
	// 延迟探测失败（500）：回退第一个可用节点，保持旧版语义。
	group := mihomoGroup{All: []string{"first", "second"}, Now: "first"}
	groupMu := &sync.Mutex{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/group/") {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		groupMu.Lock()
		_ = json.NewEncoder(w).Encode(group)
		groupMu.Unlock()
	}))
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{
		Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP",
		DelayProbeURL: "http://www.gstatic.com/generate_204",
	})
	best, err := client.SelectOptimal(context.Background(), false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "first" {
		t.Fatalf("probe failure must fall back to first available: got %q, want first", best)
	}
}

func TestSelectOptimalDelayProbeDisabledPreservesLegacy(t *testing.T) {
	// 未配置 DelayProbeURL：即使组无历史数据也只回退首可用节点，不发探测请求。
	group := mihomoGroup{All: []string{"first", "second"}, Now: "first"}
	groupMu := &sync.Mutex{}
	var groupProbes atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/group/") {
			groupProbes.Add(1)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		groupMu.Lock()
		_ = json.NewEncoder(w).Encode(group)
		groupMu.Unlock()
	}))
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	best, err := client.SelectOptimal(context.Background(), false)
	if err != nil {
		t.Fatalf("SelectOptimal: %v", err)
	}
	if best != "first" {
		t.Fatalf("legacy fallback: got %q, want first", best)
	}
	if groupProbes.Load() != 0 {
		t.Fatalf("delay probe must be skipped when DelayProbeURL is empty")
	}
}

func TestSelectOptimalInGroup(t *testing.T) {
	useGroup, useMu := mihomoTestGroup()
	testGroup := mihomoGroup{
		All: []string{"a", "b"},
		Now: "a",
		Providers: map[string]mihomoProvider{"p1": {Nodes: []mihomoNode{
			{Name: "a", History: []mihomoDelay{{Delay: 300}}},
			{Name: "b", History: []mihomoDelay{{Delay: 30}}},
		}}},
	}
	testMu := &sync.Mutex{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.HasSuffix(r.URL.Path, "/proxies/XAI-GROUP"):
			useMu.Lock()
			_ = json.NewEncoder(w).Encode(useGroup)
			useMu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/proxies/XAI-TEST-GROUP"):
			testMu.Lock()
			_ = json.NewEncoder(w).Encode(testGroup)
			testMu.Unlock()
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	ctx := context.Background()
	best, err := client.SelectOptimalInGroup(ctx, "XAI-TEST-GROUP", false)
	if err != nil {
		t.Fatalf("SelectOptimalInGroup: %v", err)
	}
	if best != "b" {
		t.Fatalf("named-group selection: got %q, want b", best)
	}
	best, err = client.SelectOptimalInGroup(ctx, "", false)
	if err != nil {
		t.Fatalf("SelectOptimalInGroup default: %v", err)
	}
	if best != "fast" {
		t.Fatalf("default-group selection: got %q, want fast", best)
	}
}

func TestBanNode(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:9093", GroupName: "XAI-TEST-GROUP"})
	epoch := client.Epoch()
	if count := client.BanNode("slow"); count != 1 {
		t.Fatalf("BanNode(slow): count=%d, want 1", count)
	}
	if client.Epoch() != epoch+1 {
		t.Fatalf("ban must bump epoch: %d -> %d", epoch, client.Epoch())
	}
	// 重复封禁同一节点只刷新 TTL，不重复计数也不重复 bump。
	if count := client.BanNode("slow"); count != 1 {
		t.Fatalf("BanNode(slow) again: count=%d, want 1", count)
	}
	if client.Epoch() != epoch+1 {
		t.Fatalf("re-ban must not bump epoch again: %d", client.Epoch())
	}
	if count := client.BanNode("fast"); count != 2 {
		t.Fatalf("BanNode(fast): count=%d, want 2", count)
	}
	banned := client.BannedNodes()
	if len(banned) != 2 || banned[0] != "fast" || banned[1] != "slow" {
		t.Fatalf("BannedNodes: got %v, want [fast slow]", banned)
	}
}

func TestUnbanNode(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:9093", GroupName: "XAI-TEST-GROUP"})
	client.BanNode("slow")
	client.BanNode("fast")
	epoch := client.Epoch()
	if count := client.UnbanNode("slow"); count != 1 {
		t.Fatalf("UnbanNode(slow): count=%d, want 1", count)
	}
	if client.Epoch() != epoch+1 {
		t.Fatalf("unban must bump epoch: %d -> %d", epoch, client.Epoch())
	}
	banned := client.BannedNodes()
	if len(banned) != 1 || banned[0] != "fast" {
		t.Fatalf("BannedNodes after unban: got %v, want [fast]", banned)
	}
	// 解禁未封禁节点保持计数不变，不 bump。
	if count := client.UnbanNode("slow"); count != 1 {
		t.Fatalf("UnbanNode(slow) again: count=%d, want 1", count)
	}
	if client.Epoch() != epoch+1 {
		t.Fatalf("unban of non-banned node must not bump epoch: %d", client.Epoch())
	}
}

// fakeEpochStore 是 EgressEpochStore 的测试替身：记录按组键的 bump 次数，
// bumpErr 非 nil 时模拟共享存储故障。
type fakeEpochStore struct {
	mu        sync.Mutex
	bumps     map[string]uint64
	bumpErr   error
	bumpCount int
}

func newFakeEpochStore() *fakeEpochStore {
	return &fakeEpochStore{bumps: make(map[string]uint64)}
}

func (f *fakeEpochStore) BumpEpoch(_ context.Context, groupKey string) (uint64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.bumpCount++
	if f.bumpErr != nil {
		return 0, f.bumpErr
	}
	f.bumps[groupKey]++
	return f.bumps[groupKey], nil
}

func (f *fakeEpochStore) GetEpoch(_ context.Context, groupKey string) (uint64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.bumps[groupKey], nil
}

// fakeSwitchLock 是 DistributedLock 的测试替身：acquired/err 可配置，
// 记录获取的键名与是否释放。
type fakeSwitchLock struct {
	mu          sync.Mutex
	acquired    bool
	err         error
	acquireKeys []string
	released    bool
}

func (f *fakeSwitchLock) Acquire(_ context.Context, key string, _ time.Duration) (func(), bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.acquireKeys = append(f.acquireKeys, key)
	if f.err != nil {
		return nil, false, f.err
	}
	if !f.acquired {
		return nil, false, nil
	}
	return func() { f.released = true }, true, nil
}

func (f *fakeSwitchLock) keyCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.acquireKeys)
}

func (f *fakeSwitchLock) lastKey() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.acquireKeys) == 0 {
		return ""
	}
	return f.acquireKeys[len(f.acquireKeys)-1]
}

// TestSetEpochStoreNilFallsBackToLocal 验证 SetEpochStore(nil) 清空共享存储
// 后回落纯本地计数：本地 epoch 仍递增，共享存储不再收到 bump。
func TestSetEpochStoreNilFallsBackToLocal(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	client.SetEpochStore(store)

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("switch with store: got %v, want Done", result)
	}
	store.mu.Lock()
	bumpedGroup := store.bumps["XAI-GROUP"] > 0
	storeBumps := store.bumpCount
	store.mu.Unlock()
	if !bumpedGroup {
		t.Fatal("switch must bump shared store under default group key XAI-GROUP")
	}

	localEpoch := client.Epoch()
	client.SetEpochStore(nil) // 清空 → 纯本地回退
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("switch after clearing store: got %v, want Done", result)
	}
	if client.Epoch() <= localEpoch {
		t.Fatalf("local epoch must still increment after nil store: %d -> %d", localEpoch, client.Epoch())
	}
	store.mu.Lock()
	after := store.bumpCount
	store.mu.Unlock()
	if after != storeBumps {
		t.Fatalf("store bumped after nil fallback: %d -> %d", storeBumps, after)
	}
}

// TestEpochStoreBumpFailureDegradesToLocal 验证共享存储 bump 失败只降级为
// 本地计数：切换仍成功（Done），本地 epoch 照常递增，绝不 fatal。
func TestEpochStoreBumpFailureDegradesToLocal(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	store.bumpErr = errors.New("redis down")
	client.SetEpochStore(store)

	localEpoch := client.Epoch()
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("bump failure must degrade, got %v, want Done", result)
	}
	if client.Epoch() <= localEpoch {
		t.Fatalf("local epoch must still increment on bump failure: %d -> %d", localEpoch, client.Epoch())
	}
}

// TestSwitchLockNotAcquiredMergesWithoutPut 验证分布式锁未抢到（另一实例
// 正在切换）时按 Merged 语义处理：不执行切换（无 PUT）、不 bump。
func TestSwitchLockNotAcquiredMergesWithoutPut(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	lock := &fakeSwitchLock{}
	client.SetSwitchLock(lock)

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchMerged {
		t.Fatalf("lock not acquired: got %v, want Merged", result)
	}
	if key := lock.lastKey(); key != "egress-mihomo-switch:XAI-GROUP" {
		t.Fatalf("lock key: got %q, want egress-mihomo-switch:XAI-GROUP", key)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("PUT executed despite lock not acquired: %v", switches)
	}
}

// TestSwitchLockAcquireErrorFailsClosed 验证分布式锁获取失败 fail-closed：
// 返回 Failed（绝不双实例并发切换），不执行 PUT。
func TestSwitchLockAcquireErrorFailsClosed(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	lock := &fakeSwitchLock{err: errors.New("redis down")}
	client.SetSwitchLock(lock)

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchFailed {
		t.Fatalf("lock acquire error: got %v, want Failed", result)
	}
	switchMu.Lock()
	defer switchMu.Unlock()
	if len(switches) != 0 {
		t.Fatalf("PUT executed despite lock acquire error: %v", switches)
	}
}

// TestSwitchLockAcquiredReleasesAfterSwitch 验证分布式锁正常路径：抢到锁后
// 执行切换（Done），切换结束释放锁，且本地单飞标志已复位（可再次切换）。
func TestSwitchLockAcquiredReleasesAfterSwitch(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	lock := &fakeSwitchLock{acquired: true}
	client.SetSwitchLock(lock)

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("lock acquired: got %v, want Done", result)
	}
	switchMu.Lock()
	puts := len(switches)
	switchMu.Unlock()
	if puts != 1 {
		t.Fatalf("PUT count: got %d, want 1", puts)
	}
	if lock.keyCount() != 1 {
		t.Fatalf("lock acquired count: got %d, want 1", lock.keyCount())
	}
	if !lock.released {
		t.Fatal("distributed lock must be released after switch")
	}
	// 本地单飞已复位：锁再次可用时同一实例可发起下一次切换。
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("second switch: got %v, want Done", result)
	}
}
