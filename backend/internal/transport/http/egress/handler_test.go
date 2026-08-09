package egress

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	egressapp "github.com/chenyme/grok2api/backend/internal/application/egress"
	egressdomain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	"github.com/chenyme/grok2api/backend/internal/repository"
	"github.com/chenyme/grok2api/backend/internal/transport/http/middleware"
	"github.com/gin-gonic/gin"
)

func TestQualityGuardStatusReadsOnlyPublicState(t *testing.T) {
	path := t.TempDir() + "/state.json"
	state := `{"version":1,"started_at":10,"updated_at":20,"last_active_cycle_at":15,"last_passive_poll_at":19,"password":"must-not-leak","guard":{"mode":"hybrid","model":"grok-4.5","client_key_id":"6","node_ids":["8"],"active_interval_seconds":1800,"passive_poll_seconds":5,"soft_tps":500,"hard_tps":1000,"consecutive_soft":2,"consecutive_errors":2,"quarantine_seconds":300,"min_healthy_nodes":3,"max_output_tokens":384,"prompt":"private-probe-prompt","expected":"private-marker"},"protected_node_ids":["9"],"nodes":{"8":{"active_soft_strikes":0,"passive_soft_strikes":0,"error_strikes":0,"quarantined_until":0,"disabled_by_guard":false,"last_reason":"","last_probe_at":15,"last_observed_at":19,"last_source":"passive","last_classification":"healthy","last_output_tps":42.5,"last_output_tokens":100,"last_first_token_ms":900,"last_duration_ms":4000}},"statistics":{"started_at":11,"active":{"total":7,"healthy":6,"soft":1,"hard":0,"errors":0,"output_tokens":1400},"passive":{"total":9,"healthy":8,"soft":0,"hard":1,"errors":0,"output_tokens":1800},"actions":{"quarantined":1,"restored":0,"suppressed":0}}}`
	if err := os.WriteFile(path, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("GET", "/egress-quality-guard", nil)
	NewHandler(nil, path).qualityGuardStatus(context)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"available":true`) || !strings.Contains(recorder.Body.String(), `"last_output_tps":42.5`) || !strings.Contains(recorder.Body.String(), `"output_tokens":1400`) || !strings.Contains(recorder.Body.String(), `"protectedNodeIds":["9"]`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if strings.Contains(recorder.Body.String(), "must-not-leak") || strings.Contains(recorder.Body.String(), "private-probe-prompt") || strings.Contains(recorder.Body.String(), "private-marker") || strings.Contains(recorder.Body.String(), "client_key_id") || !strings.Contains(recorder.Body.String(), `"recentEvents":[]`) {
		t.Fatalf("response leaked or omitted public defaults: %s", recorder.Body.String())
	}
}

func TestQualityGuardStatusIsOptional(t *testing.T) {
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("GET", "/egress-quality-guard", nil)
	NewHandler(nil).qualityGuardStatus(context)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"available":false`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityProbeRoutesKeepAdminAndSidecarContractsSeparate(t *testing.T) {
	gin.SetMode(gin.TestMode)
	handler := NewHandler(nil)

	adminRouter := gin.New()
	handler.Register(adminRouter.Group(""))
	adminRecorder := httptest.NewRecorder()
	adminRouter.ServeHTTP(adminRecorder, httptest.NewRequest("POST", "/egress-nodes/1/quality-test", nil))
	if adminRecorder.Code != 400 || !strings.Contains(adminRecorder.Body.String(), `"code":"invalidRequest"`) {
		t.Fatalf("admin route status=%d body=%s", adminRecorder.Code, adminRecorder.Body.String())
	}

	internalRouter := gin.New()
	handler.RegisterQualityGuard(internalRouter.Group(""))
	internalRecorder := httptest.NewRecorder()
	internalRouter.ServeHTTP(internalRecorder, httptest.NewRequest("POST", "/egress-nodes/1/quality-test", nil))
	if internalRecorder.Code != 503 || !strings.Contains(internalRecorder.Body.String(), `"code":"qualityGuardUnavailable"`) {
		t.Fatalf("internal route status=%d body=%s", internalRecorder.Code, internalRecorder.Body.String())
	}
}

func TestQualityGuardStateAcceptsBoundedMultiMegabyteState(t *testing.T) {
	path := t.TempDir() + "/state.json"
	state := `{"version":1,"guard":{"mode":"active"},"nodes":{},"padding":"` + strings.Repeat("x", 2<<20) + `"}`
	if err := os.WriteFile(path, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	value, available, err := NewHandler(nil, path).readQualityGuardState()
	if err != nil || !available || value.Guard.Mode != "active" {
		t.Fatalf("available=%v mode=%q error=%v", available, value.Guard.Mode, err)
	}
}

func TestWriteQualityProbeErrorUsesSpecificSafeMessage(t *testing.T) {
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	NewHandler(nil).writeQualityProbeError(context, errors.New("sensitive upstream failure"))
	if recorder.Code != 502 || !strings.Contains(recorder.Body.String(), `"code":"egressQualityProbeFailed"`) || !strings.Contains(recorder.Body.String(), "质量检测暂不可用") || strings.Contains(recorder.Body.String(), "sensitive upstream failure") {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestWriteQualityProbeErrorIdentifiesMissingProbeAccount(t *testing.T) {
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	NewHandler(nil).writeQualityProbeError(context, egressapp.ErrQualityProbeNoAccount)
	if recorder.Code != 503 || !strings.Contains(recorder.Body.String(), `"code":"egressQualityProbeNoAccount"`) || !strings.Contains(recorder.Body.String(), "暂无可调度账号") {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestUpdateQualityGuardConfigWritesPrivateAtomicFile(t *testing.T) {
	directory := t.TempDir()
	statePath := directory + "/state.json"
	configPath := directory + "/runtime-config.json"
	state := `{"version":1,"guard":{"mode":"hybrid","model":"grok-4.5","client_key_id":"6","node_ids":["8","9","10","11","12"],"active_interval_seconds":1800,"passive_poll_seconds":5,"soft_tps":500,"hard_tps":1000,"consecutive_soft":2,"consecutive_errors":2,"quarantine_seconds":300,"min_healthy_nodes":3,"max_output_tokens":384,"prompt":"probe","expected":"QUALITY_OK"},"nodes":{}}`
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("PUT", "/egress-quality-guard/config", bytes.NewBufferString(`{"mode":"passive","activeIntervalSeconds":3600,"passivePollSeconds":10,"softTPS":400,"hardTPS":900,"consecutiveSoft":3,"consecutiveErrors":4,"quarantineSeconds":600,"minHealthyNodes":2}`))
	context.Request.Header.Set("Content-Type", "application/json")
	NewHandler(nil, statePath, configPath).updateQualityGuardConfig(context)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"saved":true`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"passive_poll_seconds":10`) || strings.Contains(string(data), "prompt") {
		t.Fatalf("runtime config = %s", data)
	}
	info, err := os.Stat(configPath)
	if err != nil {
		t.Fatal(err)
	}
	// Windows has no POSIX permission bits: the os.WriteFile mode argument
	// is ignored and files always report 0666-style permissions.
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("runtime config mode = %o", info.Mode().Perm())
	}
}

func TestUpdateQualityGuardConfigRejectsInvalidAndUnknownFields(t *testing.T) {
	directory := t.TempDir()
	statePath := directory + "/state.json"
	state := `{"version":1,"guard":{"mode":"hybrid","node_ids":["8","9"]},"nodes":{}}`
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		`{"mode":"hybrid","activeIntervalSeconds":60,"passivePollSeconds":5,"softTPS":1000,"hardTPS":500,"consecutiveSoft":2,"consecutiveErrors":2,"quarantineSeconds":300,"minHealthyNodes":1}`,
		`{"mode":"hybrid","activeIntervalSeconds":60,"passivePollSeconds":5,"softTPS":500,"hardTPS":1000,"consecutiveSoft":2,"consecutiveErrors":2,"quarantineSeconds":300,"minHealthyNodes":1,"proxy":"forbidden"}`,
	} {
		recorder := httptest.NewRecorder()
		context, _ := gin.CreateTestContext(recorder)
		context.Request = httptest.NewRequest("PUT", "/egress-quality-guard/config", bytes.NewBufferString(body))
		NewHandler(nil, statePath, directory+"/runtime-config.json").updateQualityGuardConfig(context)
		if recorder.Code != 400 {
			t.Fatalf("body=%s status=%d response=%s", body, recorder.Code, recorder.Body.String())
		}
	}
}

func TestUpdateQualityGuardConfigPersistsRotationToBootstrap(t *testing.T) {
	directory := t.TempDir()
	statePath := directory + "/state.json"
	configPath := directory + "/runtime-config.json"
	bootstrapPath := directory + "/bootstrap.json"
	state := `{"version":1,"guard":{"mode":"hybrid","node_ids":["8","9"]},"nodes":{}}`
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	bootstrap := `{"version":1,"enabled":true,"internal_token":"secret-token","config":{"model":"grok-4.5","node_ids":["8","9"],"rotation_url":"https://rotator.example/rotate","rotation_token":"rot-secret","rotatable_node_ids":["8"]}}`
	if err := os.WriteFile(bootstrapPath, []byte(bootstrap), 0o600); err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("PUT", "/egress-quality-guard/config", bytes.NewBufferString(`{"mode":"hybrid","activeIntervalSeconds":60,"passivePollSeconds":5,"softTPS":500,"hardTPS":1000,"consecutiveSoft":2,"consecutiveErrors":2,"quarantineSeconds":300,"minHealthyNodes":1,"rotationUrl":"http://127.0.0.1:9090/rotate","rotatableNodeIds":["8","9"," 10 "]}`))
	context.Request.Header.Set("Content-Type", "application/json")
	NewHandler(nil, statePath, configPath, bootstrapPath).updateQualityGuardConfig(context)
	if recorder.Code != 200 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	data, err := os.ReadFile(bootstrapPath)
	if err != nil {
		t.Fatal(err)
	}
	body := string(data)
	if !strings.Contains(body, `"rotation_url":"http://127.0.0.1:9090/rotate"`) || !strings.Contains(body, `"rotatable_node_ids":["8","9","10"]`) {
		t.Fatalf("bootstrap = %s", body)
	}
	if !strings.Contains(body, `"internal_token":"secret-token"`) || !strings.Contains(body, `"rotation_token":"rot-secret"`) {
		t.Fatalf("tokens must survive rotation update: %s", body)
	}
}

func TestUpdateQualityGuardConfigRejectsRotationWithoutURL(t *testing.T) {
	directory := t.TempDir()
	statePath := directory + "/state.json"
	state := `{"version":1,"guard":{"mode":"hybrid","node_ids":["8","9"]},"nodes":{}}`
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("PUT", "/egress-quality-guard/config", bytes.NewBufferString(`{"mode":"hybrid","activeIntervalSeconds":60,"passivePollSeconds":5,"softTPS":500,"hardTPS":1000,"consecutiveSoft":2,"consecutiveErrors":2,"quarantineSeconds":300,"minHealthyNodes":1,"rotatableNodeIds":["8"]}`))
	NewHandler(nil, statePath, directory+"/runtime-config.json", directory+"/bootstrap.json").updateQualityGuardConfig(context)
	if recorder.Code != 400 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardStatusExposesRotationFromBootstrap(t *testing.T) {
	directory := t.TempDir()
	statePath := directory + "/state.json"
	bootstrapPath := directory + "/bootstrap.json"
	state := `{"version":1,"started_at":10,"guard":{"mode":"hybrid","model":"grok-4.5","node_ids":["8","9"],"active_interval_seconds":1800,"passive_poll_seconds":5,"soft_tps":500,"hard_tps":1000,"consecutive_soft":2,"consecutive_errors":2,"quarantine_seconds":300,"min_healthy_nodes":1,"max_output_tokens":384},"nodes":{}}`
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	bootstrap := `{"version":1,"enabled":true,"internal_token":"secret-token","config":{"model":"grok-4.5","node_ids":["8","9"],"rotation_url":"https://rotator.example/rotate","rotation_token":"rot-secret","rotatable_node_ids":["8","9"]}}`
	if err := os.WriteFile(bootstrapPath, []byte(bootstrap), 0o600); err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("GET", "/egress-quality-guard", nil)
	NewHandler(nil, statePath, "", bootstrapPath).qualityGuardStatus(context)
	if recorder.Code != 200 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	body := recorder.Body.String()
	if !strings.Contains(body, `"rotation_url":"https://rotator.example/rotate"`) || !strings.Contains(body, `"rotatable_node_ids":["8","9"]`) {
		t.Fatalf("status = %s", body)
	}
	if strings.Contains(body, "rot-secret") || strings.Contains(body, "secret-token") {
		t.Fatalf("status must not leak tokens: %s", body)
	}
}

func TestBatchNodeUpdateRequestRequiresEnabled(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, test := range []struct {
		name    string
		body    string
		wantErr bool
		want    bool
	}{
		{name: "missing", body: `{"ids":["1"]}`, wantErr: true},
		{name: "explicit false", body: `{"ids":["1"],"enabled":false}`, want: false},
		{name: "explicit true", body: `{"ids":["1"],"enabled":true}`, want: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			context, _ := gin.CreateTestContext(httptest.NewRecorder())
			context.Request = httptest.NewRequest("PATCH", "/egress-nodes/batch", bytes.NewBufferString(test.body))
			context.Request.Header.Set("Content-Type", "application/json")
			var request batchNodeUpdateRequest
			err := context.ShouldBindJSON(&request)
			if test.wantErr {
				if err == nil {
					t.Fatal("expected binding error")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if request.Enabled == nil || *request.Enabled != test.want {
				t.Fatalf("enabled = %v, want %v", request.Enabled, test.want)
			}
		})
	}
}

func TestUpdateManyRejectsMissingEnabled(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest("PATCH", "/egress-nodes/batch", bytes.NewBufferString(`{"ids":["1"]}`))
	context.Request.Header.Set("Content-Type", "application/json")

	(&Handler{}).updateMany(context)

	if recorder.Code != 400 || !strings.Contains(recorder.Body.String(), "invalidRequest") {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestLegacyEgressSourceListRequest(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, test := range []struct {
		path string
		want bool
	}{
		{path: "/egress-sources", want: true},
		{path: "/egress-sources?page=1", want: false},
		{path: "/egress-sources?pageSize=100", want: false},
		{path: "/egress-sources?search=alpha", want: false},
		{path: "/egress-sources?scope=grok_build", want: false},
	} {
		context, _ := gin.CreateTestContext(httptest.NewRecorder())
		context.Request = httptest.NewRequest("GET", test.path, nil)
		if got := legacyEgressSourceListRequest(context); got != test.want {
			t.Fatalf("legacyEgressSourceListRequest(%q) = %v, want %v", test.path, got, test.want)
		}
	}
}

func TestParseBoundedEgressNodeIDsChecksRawInputLength(t *testing.T) {
	values := make([]string, 5001)
	for index := range values {
		values[index] = "1"
	}
	if _, err := parseBoundedEgressNodeIDs(values, 5000); err == nil || !strings.Contains(err.Error(), "count") {
		t.Fatalf("oversized duplicate input error = %v", err)
	}
	ids, err := parseBoundedEgressNodeIDs([]string{"2", "2", "1"}, 5000)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 2 || ids[0] != 2 || ids[1] != 1 {
		t.Fatalf("ids = %v", ids)
	}
}

func TestNewNodeResponseIncludesIPv4AndIPv6ProbeDetails(t *testing.T) {
	testedAt := time.Now().UTC().Truncate(time.Second)
	response := newNodeResponse(egressdomain.PublicNode{
		ProbeStatus:   egressdomain.ProbeStatusHealthy,
		ProbeProvider: egressdomain.ProbeProviderCloudflare,
		IPv4Probe: egressdomain.ProbeFamilyResult{
			Status: egressdomain.ProbeStatusHealthy, TestedAt: testedAt, LatencyMS: 21, ExitIP: "198.51.100.2",
		},
		IPv6Probe: egressdomain.ProbeFamilyResult{
			Status: egressdomain.ProbeStatusUnhealthy, TestedAt: testedAt, LatencyMS: 48, Error: "代理连接失败",
		},
	})
	if response.ProbeProvider != "cloudflare" || response.IPv4Probe.ExitIP != "198.51.100.2" || response.IPv4Probe.TestedAt == nil || response.IPv6Probe.Status != "unhealthy" || response.IPv6Probe.Error == "" {
		t.Fatalf("node response = %#v", response)
	}
}

func TestOperationsConfigRequestParsesFallbacks(t *testing.T) {
	input, err := (operationsConfigRequest{
		ProbeProvider: "cloudflare", ProbeIntervalSeconds: 900, AssignmentIntervalSeconds: 300,
		Fallbacks: map[string]operationsFallbackRequest{
			"grok_build": {Mode: "fixed", NodeID: "42"},
			"grok_web":   {Mode: "direct"},
		},
	}).input()
	if err != nil {
		t.Fatal(err)
	}
	if fallback := input.Fallbacks[egressdomain.ScopeBuild]; fallback.Mode != egressdomain.FallbackModeFixed || fallback.NodeID != 42 {
		t.Fatalf("Build fallback = %#v", fallback)
	}
	if fallback := input.Fallbacks[egressdomain.ScopeWeb]; fallback.Mode != egressdomain.FallbackModeDirect || fallback.NodeID != 0 {
		t.Fatalf("Web fallback = %#v", fallback)
	}
	if input.ProbeProvider != egressdomain.ProbeProviderCloudflare {
		t.Fatalf("probe provider = %q", input.ProbeProvider)
	}
}

func TestOperationsConfigRequestRejectsInvalidFallbackNodeID(t *testing.T) {
	_, err := (operationsConfigRequest{
		Fallbacks: map[string]operationsFallbackRequest{"grok_build": {Mode: "fixed", NodeID: "zero"}},
	}).input()
	if !errors.Is(err, egressapp.ErrInvalidInput) {
		t.Fatalf("invalid node ID error = %v", err)
	}
}

func TestOperationsConfigResponseReportsSubscriptionProxyWithoutExposingIt(t *testing.T) {
	response := newOperationsConfigResponse(egressdomain.OperationsConfig{
		ProbeProvider:                 egressdomain.ProbeProviderCloudflare,
		EncryptedSubscriptionProxyURL: "encrypted-secret-must-not-be-returned",
	})
	if !response.SubscriptionProxyConfigured {
		t.Fatal("configured subscription proxy was not reported")
	}
	if response.ProbeProvider != "cloudflare" {
		t.Fatalf("probe provider=%q", response.ProbeProvider)
	}
}

type stubMihomoManager struct {
	rotation    egressapp.MihomoRotation
	err         error
	selectNode  string
	selectErr   error
	bannedCount int
	banErr      error
	status      egressapp.MihomoStatus
}

func (value stubMihomoManager) MihomoStatus(context.Context) egressapp.MihomoStatus {
	return value.status
}
func (value stubMihomoManager) MihomoSwitch(context.Context) (string, error) { return "", nil }
func (value stubMihomoManager) MihomoClearBlacklist() (int, error)           { return 0, nil }
func (value stubMihomoManager) Rotate(context.Context) (egressapp.MihomoRotation, error) {
	return value.rotation, value.err
}
func (value stubMihomoManager) MihomoTestSelect(context.Context, string) (string, error) {
	return value.selectNode, value.selectErr
}
func (value stubMihomoManager) MihomoTestBan(string) (int, error) {
	return value.bannedCount, value.banErr
}
func (value stubMihomoManager) MihomoTestUnban(string) (int, error) {
	return value.bannedCount, value.banErr
}
func (value stubMihomoManager) MihomoRefreshDelays(context.Context) egressapp.MihomoStatus {
	return value.status
}

func newQualityGuardRotateRouter(service *egressapp.Service) *gin.Engine {
	return newQualityGuardRotateRouterWithPaths(service)
}

// newQualityGuardRotateRouterWithPaths 允许注入 bootstrap 路径夹具，用于
// rotate 白名单（rotatable_node_ids）相关测试。
func newQualityGuardRotateRouterWithPaths(service *egressapp.Service, paths ...string) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	internal := router.Group("/api/internal/v1/quality-guard")
	internal.Use(middleware.QualityGuardAuth("guard-secret"))
	NewHandler(service, paths...).RegisterQualityGuard(internal)
	return router
}

func writeQualityGuardBootstrapFixture(t *testing.T, directory, bootstrap string) string {
	t.Helper()
	path := directory + "/bootstrap.json"
	if err := os.WriteFile(path, []byte(bootstrap), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestQualityGuardStatusExposesTestEpoch(t *testing.T) {
	// P1-7：状态接口必须透出 testEpoch（测试客户端独立出口代际号），守卫
	// 据此归因测试组探测而非退化为生产 epoch。
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{status: egressapp.MihomoStatus{
		Enabled: true, Epoch: 3, TestEnabled: true, TestEpoch: 7, TestCurrentNode: "t1",
	}})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("GET", "/api/internal/v1/quality-guard/egress-mihomo/status", nil)
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"testEpoch":7`) || !strings.Contains(recorder.Body.String(), `"epoch":3`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateRequiresToken(t *testing.T) {
	router := newQualityGuardRotateRouter(nil)
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"8"}`)))
	if recorder.Code != 401 || !strings.Contains(recorder.Body.String(), `"code":"qualityGuardUnauthorized"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateRateLimitedPerIP(t *testing.T) {
	// P2-2：rotate 端点 1 次/30s/IP 限流。同路由器第二次调用必须 429，
	// 与后端 doSwitch 节流闸配合（guard 按 quarantine_seconds 退避重试）。
	bootstrapPath := writeQualityGuardBootstrapFixture(t, t.TempDir(), `{"version":1,"enabled":true,"config":{"rotatable_node_ids":["8"]}}`)
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true, NewNode: "fast"}})
	router := newQualityGuardRotateRouterWithPaths(service, "", "", bootstrapPath)
	request := func() *httptest.ResponseRecorder {
		recorder := httptest.NewRecorder()
		req := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"8"}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer guard-secret")
		router.ServeHTTP(recorder, req)
		return recorder
	}
	if recorder := request(); recorder.Code != 200 {
		t.Fatalf("first rotate status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if recorder := request(); recorder.Code != 429 {
		t.Fatalf("second rotate within window must be rate limited, got status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateReportsChanged(t *testing.T) {
	bootstrapPath := writeQualityGuardBootstrapFixture(t, t.TempDir(), `{"version":1,"enabled":true,"config":{"rotatable_node_ids":["8"]}}`)
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true, NewNode: "fast"}})
	router := newQualityGuardRotateRouterWithPaths(service, "", "", bootstrapPath)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"8","oldExitIp":"10.0.0.1"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"changed":true`) || !strings.Contains(recorder.Body.String(), `"nodeId":"8"`) || !strings.Contains(recorder.Body.String(), `"oldExitIp":"10.0.0.1"`) || !strings.Contains(recorder.Body.String(), `"newNode":"fast"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateAllowsRotatableNode(t *testing.T) {
	bootstrapPath := writeQualityGuardBootstrapFixture(t, t.TempDir(), `{"version":1,"enabled":true,"config":{"rotatable_node_ids":["8","9"]}}`)
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true, NewNode: "fast"}})
	router := newQualityGuardRotateRouterWithPaths(service, "", "", bootstrapPath)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"9"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"changed":true`) || !strings.Contains(recorder.Body.String(), `"nodeId":"9"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateRejectsNonRotatableNode(t *testing.T) {
	bootstrapPath := writeQualityGuardBootstrapFixture(t, t.TempDir(), `{"version":1,"enabled":true,"config":{"rotatable_node_ids":["8"]}}`)
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true}})
	router := newQualityGuardRotateRouterWithPaths(service, "", "", bootstrapPath)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"7"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 403 || !strings.Contains(recorder.Body.String(), `"code":"nodeNotRotatable"`) || strings.Contains(recorder.Body.String(), `"changed":`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateRejectsEmptyWhitelist(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true}})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":"8"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 403 || !strings.Contains(recorder.Body.String(), `"code":"nodeNotRotatable"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardRotateRejectsEmptyNodeID(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{rotation: egressapp.MihomoRotation{Changed: true}})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/rotate", bytes.NewBufferString(`{"nodeId":""}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 400 || !strings.Contains(recorder.Body.String(), `"code":"invalidRequest"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardSelectReportsChangedAndCurrentNode(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{selectNode: "fast"})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/select", bytes.NewBufferString(`{"nodeId":"fast"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"changed":true`) || !strings.Contains(recorder.Body.String(), `"currentNode":"fast"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardSelectRejectsEmptyNodeID(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{selectNode: "fast"})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/select", bytes.NewBufferString(`{"nodeId":""}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 400 || !strings.Contains(recorder.Body.String(), `"code":"invalidRequest"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardSelectReportsUnavailableWhenNoManager(t *testing.T) {
	router := newQualityGuardRotateRouter(egressapp.NewService(nil, nil, ""))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/select", bytes.NewBufferString(`{"nodeId":"fast"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 503 || !strings.Contains(recorder.Body.String(), `"code":"egressMihomoUnavailable"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardBanReportsBannedNodes(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{bannedCount: 1})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/ban", bytes.NewBufferString(`{"nodeId":"slow"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"bannedNodes":1`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardUnbanReportsRemainingNodes(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{bannedCount: 0})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/unban", bytes.NewBufferString(`{"nodeId":"slow"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"bannedNodes":0`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardBanRejectsEmptyNodeID(t *testing.T) {
	service := egressapp.NewService(nil, nil, "")
	service.SetMihomoManager(stubMihomoManager{})
	router := newQualityGuardRotateRouter(service)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/ban", bytes.NewBufferString(`{"nodeId":""}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 400 || !strings.Contains(recorder.Body.String(), `"code":"invalidRequest"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestQualityGuardBanReportsUnavailableWhenNoManager(t *testing.T) {
	router := newQualityGuardRotateRouter(egressapp.NewService(nil, nil, ""))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/internal/v1/quality-guard/egress-mihomo/ban", bytes.NewBufferString(`{"nodeId":"slow"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer guard-secret")
	router.ServeHTTP(recorder, request)
	if recorder.Code != 503 || !strings.Contains(recorder.Body.String(), `"code":"egressMihomoUnavailable"`) {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

type stubManualDetectRepo struct {
	node egressdomain.Node
}

func (r *stubManualDetectRepo) ListEgressNodes(context.Context, egressdomain.Scope, repository.SortQuery) ([]egressdomain.Node, error) {
	return []egressdomain.Node{r.node}, nil
}
func (r *stubManualDetectRepo) GetEgressNode(_ context.Context, id uint64) (egressdomain.Node, error) {
	if r.node.ID != id {
		return egressdomain.Node{}, repository.ErrNotFound
	}
	return r.node, nil
}
func (r *stubManualDetectRepo) CreateEgressNode(_ context.Context, value egressdomain.Node) (egressdomain.Node, error) {
	return value, nil
}
func (r *stubManualDetectRepo) UpdateEgressNode(_ context.Context, value egressdomain.Node) (egressdomain.Node, error) {
	return value, nil
}
func (r *stubManualDetectRepo) DeleteEgressNode(context.Context, uint64) error { return nil }
func (r *stubManualDetectRepo) ListEgressNodePage(context.Context, repository.EgressNodeListQuery) ([]egressdomain.Node, int64, error) {
	return []egressdomain.Node{r.node}, 1, nil
}

type recordingMihomoManager struct {
	stubMihomoManager
	selectCalls []string
}

func (value *recordingMihomoManager) MihomoTestSelect(_ context.Context, nodeName string) (string, error) {
	value.selectCalls = append(value.selectCalls, nodeName)
	return nodeName, nil
}

type stubManualProber struct{}

func (stubManualProber) ProbeEgressQuality(context.Context, uint64, egressapp.QualityProbeInput) (egressapp.QualityProbeResult, error) {
	return egressapp.QualityProbeResult{RequestID: "req-1", NodeID: 1, Model: "grok-4.5", ExpectedMatched: true}, nil
}

func TestQualityGuardManualDetectSelectsMihomoSyncedMemberFirst(t *testing.T) {
	// Mihomo 同步节点的 ProxyURL 共享同一测试通道（127.0.0.1:7891），手动检测
	// 必须先切换测试组到目标成员再探测，否则探测结果取决于测试组当前成员。
	gin.SetMode(gin.TestMode)
	repo := &stubManualDetectRepo{node: egressdomain.Node{
		ID: 1, Name: "mihomo-sg-1", Scope: egressdomain.ScopeBuild,
		SourceKey: "mihomo:test-group:mihomo-sg-1", EncryptedProxyURL: "encrypted",
	}}
	manager := &recordingMihomoManager{}
	service := egressapp.NewService(repo, nil, "")
	service.SetMihomoManager(manager)
	service.SetQualityProber(stubManualProber{})
	handler := NewHandler(service).WithQualityGuardProbe(egressapp.QualityProbeInput{
		ClientKeyID: 7, Model: "grok-4.5", Prompt: "p", Expected: "e",
	})
	router := gin.New()
	handler.Register(router.Group("/api/admin/v1"))

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/admin/v1/egress-quality-guard/nodes/1/test", nil)
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if len(manager.selectCalls) != 1 || manager.selectCalls[0] != "mihomo-sg-1" {
		t.Fatalf("expected one select for mihomo-sg-1, got %v", manager.selectCalls)
	}
}

func TestQualityGuardManualDetectSkipsSelectForStandardNode(t *testing.T) {
	// 标准节点走公共探测，不触发测试组切换。
	gin.SetMode(gin.TestMode)
	repo := &stubManualDetectRepo{node: egressdomain.Node{
		ID: 2, Name: "std-1", Scope: egressdomain.ScopeBuild, EncryptedProxyURL: "encrypted",
	}}
	manager := &recordingMihomoManager{}
	service := egressapp.NewService(repo, nil, "")
	service.SetMihomoManager(manager)
	service.SetQualityProber(stubManualProber{})
	handler := NewHandler(service).WithQualityGuardProbe(egressapp.QualityProbeInput{
		ClientKeyID: 7, Model: "grok-4.5", Prompt: "p", Expected: "e",
	})
	router := gin.New()
	handler.Register(router.Group("/api/admin/v1"))

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/admin/v1/egress-quality-guard/nodes/2/test", nil)
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if len(manager.selectCalls) != 0 {
		t.Fatalf("expected no select for standard node, got %v", manager.selectCalls)
	}
}

func TestMihomoRefreshDelaysReturnsStatus(t *testing.T) {
	gin.SetMode(gin.TestMode)
	manager := &stubMihomoManager{status: egressapp.MihomoStatus{
		Enabled: true, GroupName: "XAI-GROUP", CurrentNode: "AT_1", SwitchCount: 3,
		TestEnabled: true, TestGroupName: "XAI-TEST-GROUP", TestCurrentNode: "DE_2",
		Members: []egressapp.MihomoMemberStatus{{Name: "AT_1", DelayMS: 120, Current: true}},
	}}
	service := egressapp.NewService(&stubManualDetectRepo{}, nil, "")
	service.SetMihomoManager(manager)
	router := gin.New()
	NewHandler(service).Register(router.Group("/api/admin/v1"))

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/admin/v1/egress-mihomo/refresh-delays", nil)
	router.ServeHTTP(recorder, request)
	if recorder.Code != 200 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Data egressapp.MihomoStatus `json:"data"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if !payload.Data.Enabled || payload.Data.CurrentNode != "AT_1" || len(payload.Data.Members) != 1 {
		t.Fatalf("unexpected payload: %+v", payload.Data)
	}
}

func TestMihomoRefreshDelaysUnavailableWhenNoManager(t *testing.T) {
	gin.SetMode(gin.TestMode)
	service := egressapp.NewService(&stubManualDetectRepo{}, nil, "")
	router := gin.New()
	NewHandler(service).Register(router.Group("/api/admin/v1"))

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/api/admin/v1/egress-mihomo/refresh-delays", nil)
	router.ServeHTTP(recorder, request)
	if recorder.Code != 503 {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}
