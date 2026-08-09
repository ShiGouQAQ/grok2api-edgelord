package egress

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	domain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// TestManagerExitChangeCoordinatorOnRotate 验证 P2-4 接线：MihomoRotate 成功
// 轮换后经注入的 ExitChangeCoordinator 广播出口变更（coordinator 非 nil 时
// 调用点不再回退直接 OnExitChanged）。
func TestManagerExitChangeCoordinatorOnRotate(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	manager := NewManager(nil, nil)
	manager.SetLogger(slog.New(slog.NewTextHandler(io.Discard, nil)))
	var coordinatorCalls atomic.Int64
	manager.SetExitChangeCoordinator(func(scope domain.Scope) {
		if scope != "" {
			t.Errorf("unexpected scope %q, want empty (all scopes)", scope)
		}
		coordinatorCalls.Add(1)
	})
	manager.UpdateMihomoConfig(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})

	rotation, err := manager.MihomoRotate(context.Background())
	if err != nil {
		t.Fatalf("MihomoRotate: %v", err)
	}
	if !rotation.Changed || rotation.NewNode == "" {
		t.Fatalf("rotation not applied: %#v", rotation)
	}
	if coordinatorCalls.Load() != 1 {
		t.Fatalf("ExitChangeCoordinator calls = %d, want 1", coordinatorCalls.Load())
	}
}

// TestMihomoBlacklistEventBroadcastMerge 验证 P1-9：实例 A 的 BanNode/UnbanNode/
// ClearBlacklist 发布事件，实例 B 合并进本地黑名单（A ban → B 出现该节点）。
func TestMihomoBlacklistEventBroadcastMerge(t *testing.T) {
	clientB := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	clientA := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	var published []repository.EgressEvent
	var publishMu sync.Mutex
	clientA.SetBlacklistEventPublisher(func(_ context.Context, event repository.EgressEvent) error {
		publishMu.Lock()
		published = append(published, event)
		publishMu.Unlock()
		clientB.MergeBlacklistEvent(event)
		return nil
	})

	// A 封禁 → B 本地黑名单出现该节点，事件携带组名与节点名。
	clientA.BanNode("fast")
	if got := clientB.BannedNodes(); len(got) != 1 || got[0] != "fast" {
		t.Fatalf("B banned nodes after A.BanNode = %v, want [fast]", got)
	}
	publishMu.Lock()
	if len(published) != 1 || published[0].Kind != repository.EgressEventNodeBanned || published[0].Group != "XAI-GROUP" || published[0].NodeName != "fast" {
		t.Fatalf("published events = %#v", published)
	}
	publishMu.Unlock()

	// A 解禁 → B 解禁该节点。
	clientA.UnbanNode("fast")
	if got := clientB.BannedNodes(); len(got) != 0 {
		t.Fatalf("B banned nodes after A.UnbanNode = %v, want none", got)
	}

	// A 清空黑名单 → B 清空。
	clientA.BanNode("slow")
	clientA.BanNode("fast")
	if got := clientB.BannedNodes(); len(got) != 2 {
		t.Fatalf("B banned nodes before clear = %v, want [fast slow]", got)
	}
	clientA.ClearBlacklist()
	if got := clientB.BannedNodes(); len(got) != 0 {
		t.Fatalf("B banned nodes after A.ClearBlacklist = %v, want none", got)
	}
}

// TestMihomoBlacklistEventGroupMismatchIgnored 验证组名不匹配（测试组等其他
// 客户端）的事件被忽略，不污染本地黑名单。
func TestMihomoBlacklistEventGroupMismatchIgnored(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	client.MergeBlacklistEvent(repository.EgressEvent{Kind: repository.EgressEventNodeBanned, Group: "OTHER-GROUP", NodeName: "fast"})
	if got := client.BannedNodes(); len(got) != 0 {
		t.Fatalf("unexpected merged nodes = %v", got)
	}
}

// TestMihomoBlacklistStaleUnbanKeepsFreshBan 验证"不删除对方刚加的封禁"：
// 早于本地封禁时刻的解禁事件被忽略，更新的解禁事件才生效。
func TestMihomoBlacklistStaleUnbanKeepsFreshBan(t *testing.T) {
	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: "http://127.0.0.1:1", GroupName: "XAI-GROUP"})
	client.BanNode("fast")

	// 本地封禁之后才收到一个更早发布的解禁事件：保留封禁。
	stale := repository.EgressEvent{Kind: repository.EgressEventNodeUnbanned, Group: "XAI-GROUP", NodeName: "fast", PublishedAt: time.Now().UTC().Add(-time.Minute)}
	client.MergeBlacklistEvent(stale)
	if got := client.BannedNodes(); len(got) != 1 || got[0] != "fast" {
		t.Fatalf("stale unban removed fresh ban, banned = %v", got)
	}

	// 收到晚于本地封禁时刻的解禁事件：解禁生效。
	fresh := repository.EgressEvent{Kind: repository.EgressEventNodeUnbanned, Group: "XAI-GROUP", NodeName: "fast", PublishedAt: time.Now().UTC()}
	client.MergeBlacklistEvent(fresh)
	if got := client.BannedNodes(); len(got) != 0 {
		t.Fatalf("fresh unban did not unban, banned = %v", got)
	}
}

// TestNotifyExitChangedWithoutCoordinatorFallsBack 验证单实例退化：未注入
// coordinator 时 notifyExitChanged 返回 false，调用点回退直接 OnExitChanged
// （现状语义不变）；注入后返回 true 并调用回调。
func TestNotifyExitChangedWithoutCoordinatorFallsBack(t *testing.T) {
	manager := NewManager(nil, nil)
	if manager.notifyExitChanged("") {
		t.Fatal("notifyExitChanged without coordinator should return false")
	}
	var calls atomic.Int64
	manager.SetExitChangeCoordinator(func(domain.Scope) { calls.Add(1) })
	if !manager.notifyExitChanged("") {
		t.Fatal("notifyExitChanged with coordinator should return true")
	}
	if calls.Load() != 1 {
		t.Fatalf("coordinator calls = %d, want 1", calls.Load())
	}
}

// TestMihomoDoSwitchPublishesBlacklistEvent 验证 P1 修复：doSwitch 路径（
// SwitchAndBlacklistCurrent）封禁当前节点时同样发布 node_banned 事件，多
// 实例黑名单与公共 BanNode 入口行为一致地收敛。
func TestMihomoDoSwitchPublishesBlacklistEvent(t *testing.T) {
	group, groupMu := mihomoTestGroup()
	var switches []string
	var switchMu sync.Mutex
	server := mihomoTestServer(t, &group, groupMu, http.StatusNoContent, 0, &switches, &switchMu)
	defer server.Close()

	client := NewMihomoClient(MihomoConfig{Enabled: true, APIURL: server.URL, GroupName: "XAI-GROUP"})
	var published []repository.EgressEvent
	var publishMu sync.Mutex
	client.SetBlacklistEventPublisher(func(_ context.Context, event repository.EgressEvent) error {
		publishMu.Lock()
		published = append(published, event)
		publishMu.Unlock()
		return nil
	})

	if result := client.SwitchAndBlacklistCurrent(context.Background(), "test_do_switch"); result != MihomoSwitchDone {
		t.Fatalf("SwitchAndBlacklistCurrent result = %v, want Done", result)
	}
	if got := client.BannedNodes(); len(got) != 1 || got[0] != "slow" {
		t.Fatalf("banned nodes = %v, want [slow]", got)
	}
	publishMu.Lock()
	defer publishMu.Unlock()
	if len(published) != 1 || published[0].Kind != repository.EgressEventNodeBanned || published[0].Group != "XAI-GROUP" || published[0].NodeName != "slow" {
		t.Fatalf("published events = %#v, want one node_banned for slow", published)
	}
}
