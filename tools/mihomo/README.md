# Mihomo 配置参考

本目录提供 Mihomo（vernesong/mihomo / MetaCubeX/mihomo 内核）作为 grok2api 出口代理的配置模板。

## 文件

| 文件 | 说明 |
|---|---|
| `mihomo-xai.yaml` | 单通道基线模板（从 python-main 分支提取，原样保留）——单组 `XAI-GROUP`、`mixed-port: 10801` |
| `mihomo-dual-channel.yaml` | **推荐**双通道模板——使用组 `XAI-GROUP`(:7890) + 测试组 `XAI-TEST-GROUP`(:7891) |

## 双通道架构

```
mihomo 配置（同一批 proxies 节点，两个组各自独立选择状态）
├── XAI-GROUP      → mixed-port :7890 → DB 节点 ProxyURL 指向这里（生产流量）
└── XAI-TEST-GROUP → listener   :7891 → 质量守卫探测专用（只有守卫在动它）
```

- **使用通道**：生产流量出口。只有确认质量问题 / 403 封锁时才切换（Go 侧 `SwitchAndBlacklistCurrent`）。
- **测试通道**：质量守卫（quality_guard）探测专用。守卫在测试组上自由切换探测成员质量，生产出口零扰动；探测结果归因到成员干净无竞态。
- 两个组 `use` 同一 `proxy-provider`（同一批节点），各自独立 `now` 选择状态；Go 侧双 client 实例各自维护 epoch/黑名单，测试切换不 bump 生产 epoch。

## 配置要求

1. **组类型必须 `type: select`（手动选择）**——不能是 `URLTest`/`Fallback`。因为黑名单模拟禁用成员依赖"切走后不自动回选"；URLTest 组会周期性 healthcheck 并自动回选被禁节点。
2. **`listeners` 需要 mihomo v1.18+**。测试通道经 listener 直连测试组，绕过 rules 分流。
3. `external-controller` 端口（默认 9093）与 Go 侧 `mihomoAPIURL` 配置对应。
4. 把 `YOUR_SUBSCRIPTION_URL` 替换为实际机场订阅地址。

## 对应 Go 侧配置（config.example.yaml）

```yaml
provider:
  web:
    mihomoEnabled: true
    mihomoAPIURL: "http://127.0.0.1:9093"     # external-controller
    mihomoGroupName: "XAI-GROUP"               # 使用组
    mihomoTestGroupName: "XAI-TEST-GROUP"      # 测试组（守卫探测）
    mihomoExitProbeProxyURL: "http://127.0.0.1:7890"  # 出口 IP 探测经使用通道
    mihomoTestProxyURL: "http://127.0.0.1:7891"       # 守卫探测经测试通道
    mihomoIPProbeURL: "https://1.1.1.1/cdn-cgi/trace"
    mihomoMaxAttempts: 3
    mihomoVerifyTimeout: 15s
```

## 探测路径说明

- 守卫的 `quality-test` 探测请求**不走 FlareSolverr**（grok_build scope 不在 `isGrokWebScope` 内），纯质量信号（TPS/marker）。
- 生产通道的 403/clearance 走 FlareSolverr 求解，失败触发使用组切换——两套信号天然分离。
