# 🌾 Airdrop Farmer - 自动链上交互脚本

## 安装依赖

```bash
pip install web3 eth-account
```

## 使用方法

### 1. 设置私钥（环境变量，绝不硬编码）

```bash
export ETH_PRIVATE_KEY=你的私钥
# ⚠️ 私钥仅在内存中使用，不会写入任何文件
# ⚠️ 用完后 unset ETH_PRIVATE_KEY 清除环境变量
```

### 2. 运行

```bash
# 测试模式 - Arbitrum 上一次自转账
python airdrop_farmer.py

# 执行第1天计划
python airdrop_farmer.py --day 1

# 执行完整7天计划
python airdrop_farmer.py --full-plan
```

### 3. 清除私钥

```bash
unset ETH_PRIVATE_KEY
```

## 7天交互计划

| 天 | 链 | 操作 | 预估gas |
|---|---|---|---|
| Day 1 | ETH + Arbitrum | 自转账 | ~$5 |
| Day 2 | Optimism + Base | 自转账 | ~$2 |
| Day 3 | ETH + Arbitrum | Wrap ETH | ~$5 |
| Day 4 | Optimism + Base | 自转账 + Wrap | ~$3 |
| Day 5 | Arbitrum + Base | 自转账 + Wrap | ~$3 |
| Day 6 | ETH + Polygon | 自转账 | ~$5 |
| Day 7 | Arb + OP + Base | Wrap + 自转账 | ~$4 |
| **总计** | 5条链 | 13次交互 | **~$27** |

## 安全设计

- ✅ 私钥通过 `ETH_PRIVATE_KEY` 环境变量注入，不落盘
- ✅ 交互金额极小 (0.0001-0.01 ETH)，仅创造链上记录
- ✅ 操作间随机延迟 (30s-20min)，模拟真人行为
- ✅ 完整日志记录，可审计每笔交易
- ✅ 所有链余额检查，余额不足自动跳过

## 前提条件

- 需要在各链上有 ETH 做 gas：
  - Ethereum: ≥ 0.05 ETH (~$3,800)
  - Arbitrum: ≥ 0.01 ETH (~$76)
  - Optimism: ≥ 0.01 ETH (~$76)
  - Base: ≥ 0.01 ETH (~$76)

⚠️ **你的钱包目前只有 0.023 ETH，建议：**
1. 先充值到 Arbitrum/Base（L2 gas 更便宜）
2. 通过跨链桥分配到各条链
3. 或者用 cron 每天自动执行 Day N 计划

## 进阶：用 cron 自动化

```bash
# 每天凌晨3点执行当天计划
0 3 * * * export ETH_PRIVATE_KEY=xxx && python airdrop_farmer.py --day $(date +%u) && unset ETH_PRIVATE_KEY
```

⚠️ cron 方式私钥会短暂出现在环境变量中，建议用加密存储替代。