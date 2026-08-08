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

// TestDelaySnapshotCache 覆盖 DelaySnapshot 的节流语义：TTL 内缓存命中不
// 重复探测；epoch bump（黑名单变动）后缓存失效重新探测；探测失败同样被
// 缓存（负缓存），避免状态轮询每 2s 重试失败的端点。
func TestDelaySnapshotCache(t *testing.T) {
	group := mihomoGroup{
		All: []string{"slow", "fast", "dead"}, Now: "slow",
		Providers: map[string]mihomoProvider{"p1": {Nodes: []mihomoNode{
			{Name: "slow"}, {Name: "fast"}, {Name: "dead"}, // select 组：无 history
		}}},
	}
	mu := &sync.Mutex{}
	var delayCalls atomic.Int64
	server := mihomoGroupAndDelayServer(t, &group, mu, func() map[string]int {
		delayCalls.Add(1)
		return map[string]int{"slow": 300, "fast": 50, "dead": 0}
	})
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP", DelayProbeURL: "http://www.gstatic.com/generate_204"})

	available := mihomoAvailable(group.All, group.Now, nil, false)
	for i := 0; i < 3; i++ {
		delays, ok := client.DelaySnapshot(context.Background(), "", available)
		if !ok || delays["slow"] != 300 || delays["fast"] != 50 {
			t.Fatalf("call %d: delays=%v ok=%v", i, delays, ok)
		}
		if _, exists := delays["dead"]; exists {
			t.Fatalf("call %d: dead (0) must be filtered out: %v", i, delays)
		}
	}
	if delayCalls.Load() != 1 {
		t.Fatalf("delay endpoint must be hit once within TTL, got %d", delayCalls.Load())
	}
	// epoch bump（封禁节点）后缓存失效，重新探测；封禁成员被过滤。
	client.BanNode("fast")
	available = mihomoAvailable(group.All, group.Now, map[string]struct{}{"fast": {}}, false)
	delays, ok := client.DelaySnapshot(context.Background(), "", available)
	if !ok || delays["slow"] != 300 {
		t.Fatalf("snapshot after cache invalidation: delays=%v ok=%v", delays, ok)
	}
	if _, exists := delays["fast"]; exists {
		t.Fatalf("banned member must be filtered from snapshot: %v", delays)
	}
	if delayCalls.Load() != 2 {
		t.Fatalf("delay endpoint must be hit again after epoch bump, got %d", delayCalls.Load())
	}
	// TTL 过期后重新探测。
	client.mu.Lock()
	client.delayCache["XAI-GROUP"] = mihomoDelayCache{delays: map[string]int{"slow": 1}, ok: true, at: time.Now().Add(-mihomoDelayCacheTTL)}
	client.mu.Unlock()
	if _, ok := client.DelaySnapshot(context.Background(), "", available); !ok {
		t.Fatal("snapshot after TTL expiry must succeed")
	}
	if delayCalls.Load() != 3 {
		t.Fatalf("delay endpoint must be hit again after TTL expiry, got %d", delayCalls.Load())
	}
}

// TestDelaySnapshotFailureCached 验证探测失败同样走负缓存：一次失败后，
// TTL 内的后续调用不再重试探测端点。
func TestDelaySnapshotFailureCached(t *testing.T) {
	var delayCalls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/group/"):
			delayCalls.Add(1)
			w.WriteHeader(http.StatusInternalServerError)
		case strings.HasPrefix(r.URL.Path, "/proxies/"):
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(mihomoGroup{All: []string{"a"}, Now: "a"})
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP", DelayProbeURL: "http://x"})
	if _, ok := client.DelaySnapshot(context.Background(), "", []string{"a"}); ok {
		t.Fatal("expected ok=false on probe failure")
	}
	if _, ok := client.DelaySnapshot(context.Background(), "", []string{"a"}); ok {
		t.Fatal("expected cached ok=false on second call")
	}
	if delayCalls.Load() != 1 {
		t.Fatalf("failed probe must be throttled, got %d delay calls", delayCalls.Load())
	}
}

// TestDelaySnapshotUnconfigured 验证未配置 DelayProbeURL 时返回 ok=false
// 且不发起任何探测请求（行为不变，零回归）。
func TestDelaySnapshotUnconfigured(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	if _, ok := client.DelaySnapshot(context.Background(), "", []string{"a"}); ok {
		t.Fatal("expected ok=false without DelayProbeURL")
	}
	if _, ok := client.DelaySnapshot(context.Background(), "", nil); ok {
		t.Fatal("expected ok=false on empty available")
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
// bumpErr 非 nil 时模拟共享存储 bump 故障，getErr 非 nil 时模拟读取故障。
type fakeEpochStore struct {
	mu        sync.Mutex
	bumps     map[string]uint64
	bumpErr   error
	getErr    error
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
	if f.getErr != nil {
		return 0, f.getErr
	}
	return f.bumps[groupKey], nil
}

// fakeSwitchLock 是 DistributedLock 的测试替身：acquired/err 可配置，
// 记录获取的键名、TTL 与是否释放。
type fakeSwitchLock struct {
	mu          sync.Mutex
	acquired    bool
	err         error
	acquireKeys []string
	lastTTL     time.Duration
	released    bool
}

func (f *fakeSwitchLock) Acquire(_ context.Context, key string, ttl time.Duration) (func(), bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.acquireKeys = append(f.acquireKeys, key)
	f.lastTTL = ttl
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
	client.switchMinInterval = 0 // 本测试验证 store 回退语义，禁用自动切换节流
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
	client.switchMinInterval = 0 // 本测试验证锁释放语义，禁用自动切换节流
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

// TestBumpEpochMirrorsSharedStore 验证注入了 epochStore 时本地 atomic 以
// BumpEpoch 返回值为准（共享权威），而非本地盲目 +1——共享值已领先时本地
// 直接对齐到共享值，保证跨实例 clearance 指纹单调一致。
func TestBumpEpochMirrorsSharedStore(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	store.mu.Lock()
	store.bumps["XAI-GROUP"] = 5 // 共享值已领先（另一实例已 bump 5 次）
	store.mu.Unlock()
	client.SetEpochStore(store)
	epoch := client.Epoch()
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("switch: got %v, want Done", result)
	}
	store.mu.Lock()
	shared := store.bumps["XAI-GROUP"]
	store.mu.Unlock()
	if shared != 6 {
		t.Fatalf("shared store must have bumped once: got %d, want 6", shared)
	}
	if client.Epoch() != shared {
		t.Fatalf("local epoch must mirror shared value: got %d, want %d", client.Epoch(), shared)
	}
	if client.Epoch() == epoch+1 {
		t.Fatalf("local must take shared value, not blindly +1: got %d (was %d)", client.Epoch(), epoch)
	}
}

// TestRefreshEpochFromStoreCatchesUpLocal 验证共享值领先时
// RefreshEpochFromStore 把本地 atomic 追平到共享值（另一实例 bump 后本
// 实例收敛）。
func TestRefreshEpochFromStoreCatchesUpLocal(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	client.SetEpochStore(store)
	store.mu.Lock()
	store.bumps["XAI-GROUP"] = 7 // 模拟另一实例已 bump 7 次
	store.mu.Unlock()
	client.RefreshEpochFromStore(context.Background())
	if client.Epoch() != 7 {
		t.Fatalf("local epoch must catch up to shared: got %d, want 7", client.Epoch())
	}
}

// TestRefreshEpochFromStoreDoesNotRegress 验证本地领先（降级 bump 累积）
// 时刷新不得把本地倒退到共享值。
func TestRefreshEpochFromStoreDoesNotRegress(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	client.SetEpochStore(store)
	client.epoch.Store(9)
	store.mu.Lock()
	store.bumps["XAI-GROUP"] = 4
	store.mu.Unlock()
	client.RefreshEpochFromStore(context.Background())
	if client.Epoch() != 9 {
		t.Fatalf("refresh must not regress local epoch: got %d, want 9", client.Epoch())
	}
}

// TestRefreshEpochFromStoreNilAndErrorGraceful 验证刷新是 best-effort：
// 未注入 store 直接返回；读取失败记 Debug 返回，本地保持不动。
func TestRefreshEpochFromStoreNilAndErrorGraceful(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	client.RefreshEpochFromStore(context.Background()) // nil store：no-op

	store := newFakeEpochStore()
	store.getErr = errors.New("redis down")
	client.SetEpochStore(store)
	client.epoch.Store(3)
	client.RefreshEpochFromStore(context.Background())
	if client.Epoch() != 3 {
		t.Fatalf("refresh error must leave local epoch unchanged: got %d, want 3", client.Epoch())
	}
}

// TestSwitchLockTTLCoversFullRetryWindow 验证锁 TTL = (MaxAttempts+1)×
// (VerifyTimeout + perAttemptHTTPBudget) + clearanceLockGrace，覆盖最坏同 IP
// 重试窗口（每轮含 SelectOptimal/SwitchNode/verifySwitch 的 HTTP 往返）；
// ttl 不足会在切换完成前提前过期，第二实例可并发进入切换。
func TestSwitchLockTTLCoversFullRetryWindow(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP", MaxAttempts: 2, VerifyTimeout: 10 * time.Second})
	lock := &fakeSwitchLock{acquired: true}
	client.SetSwitchLock(lock)
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("switch: got %v, want Done", result)
	}
	want := time.Duration(3)*(10*time.Second+perAttemptHTTPBudget) + clearanceLockGrace
	if lock.lastTTL != want {
		t.Fatalf("lock TTL: got %v, want %v", lock.lastTTL, want)
	}
}

// TestDelaySnapshotConcurrentProbeMerged 验证缓存 miss 后的并发探测按组
// 单飞合并：N 个 goroutine 同时调用，探测端点只被真实打一次。
func TestDelaySnapshotConcurrentProbeMerged(t *testing.T) {
	group := mihomoGroup{
		All: []string{"slow", "fast"}, Now: "slow",
		Providers: map[string]mihomoProvider{"p1": {Nodes: []mihomoNode{{Name: "slow"}, {Name: "fast"}}}},
	}
	mu := &sync.Mutex{}
	var delayCalls atomic.Int64
	server := mihomoGroupAndDelayServer(t, &group, mu, func() map[string]int {
		delayCalls.Add(1)
		time.Sleep(50 * time.Millisecond) // 拉长探测窗口，确保并发调用能合并
		return map[string]int{"slow": 300, "fast": 50}
	})
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP", DelayProbeURL: "http://www.gstatic.com/generate_204"})
	available := mihomoAvailable(group.All, group.Now, nil, false)

	const n = 8
	results := make([]map[string]int, n)
	oks := make([]bool, n)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			results[i], oks[i] = client.DelaySnapshot(context.Background(), "", available)
		}()
	}
	close(start)
	wg.Wait()
	for i := 0; i < n; i++ {
		if !oks[i] || results[i]["slow"] != 300 || results[i]["fast"] != 50 {
			t.Fatalf("call %d: delays=%v ok=%v", i, results[i], oks[i])
		}
	}
	if delayCalls.Load() != 1 {
		t.Fatalf("concurrent probes must be merged, delay endpoint hit %d times", delayCalls.Load())
	}
}

// TestDelaySnapshotEpochChangeDiscardsWriteBack 验证探测进行中 epoch 变化
// （黑名单变动已清缓存）时，探测结果不写回缓存，避免陈旧数据重新填充；
// 发起探测的调用方仍收到本次探测结果。
func TestDelaySnapshotEpochChangeDiscardsWriteBack(t *testing.T) {
	group := mihomoGroup{
		All: []string{"slow", "fast"}, Now: "slow",
		Providers: map[string]mihomoProvider{"p1": {Nodes: []mihomoNode{{Name: "slow"}, {Name: "fast"}}}},
	}
	mu := &sync.Mutex{}
	started := make(chan struct{})
	release := make(chan struct{})
	var probeOnce sync.Once
	server := mihomoGroupAndDelayServer(t, &group, mu, func() map[string]int {
		probeOnce.Do(func() { close(started) })
		<-release // 阻塞探测直到测试放行
		return map[string]int{"slow": 300, "fast": 50}
	})
	defer server.Close()
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP", DelayProbeURL: "http://www.gstatic.com/generate_204"})
	available := mihomoAvailable(group.All, group.Now, nil, false)

	var wg sync.WaitGroup
	wg.Add(1)
	var got map[string]int
	var ok bool
	go func() {
		defer wg.Done()
		got, ok = client.DelaySnapshot(context.Background(), "", available)
	}()
	<-started              // 探测已发起
	client.BanNode("fast") // epoch bump 清空缓存
	close(release)         // 放行探测返回
	wg.Wait()

	if !ok || got["slow"] != 300 {
		t.Fatalf("caller must still receive probe result: %v ok=%v", got, ok)
	}
	client.mu.Lock()
	_, cached := client.delayCache["XAI-GROUP"]
	client.mu.Unlock()
	if cached {
		t.Fatal("stale probe result must not be written back to cache after epoch change")
	}
}

// TestBumpEpochDoesNotRegressAfterFailedBumps 验证 P1-3：本地在连续失败 bump
// 后领先共享值，随后成功 bump（返回较小的 shared）时本地不得回绕到历史值
// （旧出口 clearance 指纹复活）；max 语义应保留本地领先值。
func TestBumpEpochDoesNotRegressAfterFailedBumps(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	store.bumpErr = errors.New("redis down")
	client.SetEpochStore(store)

	client.BanNode("slow") // 降级 bump：local+1，shared 不动
	client.BanNode("fast") // 降级 bump：local+1，shared 不动
	want := client.Epoch() // 两次失败 bump 后的本地领先值（含构造时的初始 bump）
	store.mu.Lock()
	store.bumpErr = nil
	store.mu.Unlock()
	client.BanNode("dead") // 成功 bump：shared 只到 1，远小于本地领先值
	if got := client.Epoch(); got != want {
		t.Fatalf("local epoch regressed after successful bump: got %d, want %d", got, want)
	}
	store.mu.Lock()
	shared := store.bumps["XAI-GROUP"]
	store.mu.Unlock()
	if shared != 1 {
		t.Fatalf("shared store must have bumped once: got %d, want 1", shared)
	}
	if want <= shared {
		t.Fatalf("test must exercise max branch: local %d must exceed shared %d", want, shared)
	}
}

// TestAutoSwitchThrottledWithinInterval 验证 P1-1：故障驱动的自动切换
// （SwitchAndBlacklistCurrent）在 switchMinInterval 内的第二次触发被节流为
// Merged（不执行 PUT、不加冷却），而人工/管理驱动切换（SwitchToOptimal）
// 不受节流限制。
func TestAutoSwitchThrottledWithinInterval(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	client.switchMinInterval = time.Minute // 拉长间隔：窗口内第二次自动切换必须被节流

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchDone {
		t.Fatalf("first auto switch: got %v, want Done", result)
	}
	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test"); result != MihomoSwitchMerged {
		t.Fatalf("second auto switch within interval must be throttled to Merged, got %v", result)
	}
	switchMu.Lock()
	puts := len(switches)
	switchMu.Unlock()
	if puts != 1 {
		t.Fatalf("throttled switch must not PUT: got %d puts, want 1", puts)
	}

	if _, result := client.SwitchToOptimal(context.Background(), "manual"); result != MihomoSwitchDone {
		t.Fatalf("manual switch must bypass throttle: got %v, want Done", result)
	}
}

// TestRefreshEpochThrottledMergesWithinInterval 验证 P1-4：refreshEpochThrottled
// 在 epochRefreshInterval 内合并（第二次调用不触发 GetEpoch 追平），间隔重置
// 后再次追平。
func TestRefreshEpochThrottledMergesWithinInterval(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	store := newFakeEpochStore()
	client.SetEpochStore(store)
	client.epochRefreshInterval = time.Hour // 窗口内刷新必须被合并

	store.mu.Lock()
	store.bumps["XAI-GROUP"] = 7
	store.mu.Unlock()
	client.refreshEpochThrottled() // 首次刷新：追平
	if client.Epoch() != 7 {
		t.Fatalf("first refresh must catch up: got %d, want 7", client.Epoch())
	}

	store.mu.Lock()
	store.bumps["XAI-GROUP"] = 9 // 他实例又 bump 两次
	store.mu.Unlock()
	client.refreshEpochThrottled() // 间隔内：跳过
	if client.Epoch() != 7 {
		t.Fatalf("refresh within interval must be merged: got %d, want 7", client.Epoch())
	}

	client.mu.Lock()
	client.lastEpochRefresh = time.Time{} // 重置节流
	client.mu.Unlock()
	client.refreshEpochThrottled()
	if client.Epoch() != 9 {
		t.Fatalf("refresh after throttle reset must catch up: got %d, want 9", client.Epoch())
	}
}
