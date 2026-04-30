"""
Airdrop Farmer - 自动链上交互脚本
在多链上执行 DeFi 交互，满足未来空投快照条件

安全设计：
- 私钥通过环境变量注入，绝不硬编码
- 交互金额随机化，模拟真人行为
- 时间间隔随机，避免被标记为机器人
- 所有操作有日志记录
"""

import os
import sys
import json
import time
import random
import logging
from datetime import datetime
from pathlib import Path

from web3 import Web3
from eth_account import Account

# ============================================================
# 配置
# ============================================================

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "farmer.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("AirdropFarmer")

# 链配置 (RPC endpoints - 公共免费节点)
CHAINS = {
    "ethereum": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "chain_id": 1,
        "name": "Ethereum Mainnet",
        "gas_limit_default": 250000,
    },
    "arbitrum": {
        "rpc": "https://arbitrum-rpc.publicnode.com",
        "chain_id": 42161,
        "name": "Arbitrum One",
        "gas_limit_default": 300000,
    },
    "optimism": {
        "rpc": "https://optimism-rpc.publicnode.com",
        "chain_id": 10,
        "name": "Optimism",
        "gas_limit_default": 300000,
    },
    "base": {
        "rpc": "https://base-rpc.publicnode.com",
        "chain_id": 8453,
        "name": "Base",
        "gas_limit_default": 300000,
    },
    "polygon": {
        "rpc": "https://polygon-rpc.publicnode.com",
        "chain_id": 137,
        "name": "Polygon",
        "gas_limit_default": 300000,
    },
}

# 常用合约地址
CONTRACTS = {
    # WETH on each chain
    "ethereum": {
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "UniswapV3Router": "0xEf1fc6E3B6E4b8D4E21f3924A2C5f6E5b5e4e5E5",  # placeholder, see below
    },
    "arbitrum": {
        "WETH": "0x82aF49447D8a07e3bd95BD0d6f9D0538E81A5E5",
        "USDC": "0xaf88d0657796b5060b34170A4927a6b0a7c7D7e9",
    },
    "optimism": {
        "WETH": "0x4200000000000000000000000000000000000006",
        "USDC": "0x0b2C639c533813f4Aa9D89304268A9758e0e7DFb",
    },
    "base": {
        "WETH": "0x4200000000000000000000000000000000000006",
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
}

# ERC20 ABI (最小化，只需 transfer/approve/balanceOf)
ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"}
]""")

# Uniswap V3 Quoter ABI (简化)
UNISWAP_V3_QUOTER_ABI = json.loads("""[
    {"inputs":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"amountIn","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"quoteExactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}
]""")

# ============================================================
# 安全模块
# ============================================================

class SecureKeyManager:
    """私钥管理 - 环境变量注入，内存使用，不落盘"""

    def __init__(self):
        key = os.environ.get("ETH_PRIVATE_KEY")
        if not key:
            logger.error("❌ ETH_PRIVATE_KEY 环境变量未设置！")
            logger.info("用法: export ETH_PRIVATE_KEY=你的私钥")
            logger.info("⚠️  私钥仅在内存中使用，不会写入任何文件")
            sys.exit(1)

        # 验证私钥格式
        if not key.startswith("0x"):
            key = "0x" + key
        try:
            self.account = Account.from_key(key)
        except Exception as e:
            logger.error(f"❌ 私钥格式无效: {e}")
            sys.exit(1)

        self.address = self.account.address
        logger.info(f"✅ 账户已加载: {self.address}")

    def sign_transaction(self, tx_dict):
        """签名交易 - 私钥仅在此处使用"""
        signed = self.account.sign_transaction(tx_dict)
        return signed

    def get_address(self):
        return self.address


# ============================================================
# 链连接
# ============================================================

class ChainConnection:
    """连接到一条链"""

    def __init__(self, chain_name: str):
        config = CHAINS[chain_name]
        self.w3 = Web3(Web3.HTTPProvider(config["rpc"]))
        self.chain_id = config["chain_id"]
        self.name = chain_name
        self.chain_display = config["name"]
        self.gas_limit_default = config["gas_limit_default"]

        if not self.w3.is_connected():
            logger.warning(f"⚠️  {self.chain_display} 连接失败")
        else:
            logger.info(f"✅ 已连接 {self.chain_display} (block: {self.w3.eth.block_number})")

    def get_eth_balance(self, address):
        balance = self.w3.eth.get_balance(address)
        return Web3.from_wei(balance, "ether")

    def get_gas_price(self):
        return self.w3.eth.gas_price

    def send_raw_transaction(self, raw_tx):
        tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()

    def wait_for_receipt(self, tx_hash, timeout=120):
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(
                Web3.to_bytes(hexstr=tx_hash), timeout=timeout
            )
            return receipt
        except Exception as e:
            logger.error(f"交易确认超时: {e}")
            return None


# ============================================================
# 交互策略
# ============================================================

class InteractionEngine:
    """执行具体的链上交互"""

    def __init__(self, chain: ChainConnection, key_manager: SecureKeyManager):
        self.chain = chain
        self.key_manager = key_manager
        self.address = key_manager.get_address()
        self.w3 = chain.w3

    def _build_base_tx(self, value=0):
        """构建基础交易参数"""
        gas_price = self.chain.get_gas_price()
        nonce = self.w3.eth.get_transaction_count(self.address)
        return {
            "from": self.address,
            "nonce": nonce,
            "gasPrice": gas_price,
            "gas": self.chain.gas_limit_default,
            "chainId": self.chain.chain_id,
            "value": value,
        }

    def _execute_tx(self, tx_dict):
        """签名并发送交易"""
        signed = self.key_manager.sign_transaction(tx_dict)
        tx_hash = self.chain.send_raw_transaction(signed.raw_transaction)
        logger.info(f"📤 交易已发送: {tx_hash}")

        receipt = self.chain.wait_for_receipt(tx_hash)
        if receipt:
            status = "✅ 成功" if receipt.status == 1 else "❌ 失败"
            gas_used = receipt.gasUsed
            gas_cost_eth = Web3.from_wei(gas_used * tx_dict["gasPrice"], "ether")
            logger.info(f"{status} | gas: {gas_used} | cost: {gas_cost_eth:.6f} ETH")
            return receipt
        return None

    # --- 具体交互操作 ---

    def self_transfer(self):
        """1. 自转账 - 最简单的交互，证明地址活跃"""
        amount = Web3.to_wei(random.uniform(0.0001, 0.001), "ether")
        tx = self._build_base_tx(value=amount)
        tx["to"] = self.address
        tx["gas"] = 21000  # 简单转账gas固定
        logger.info(f"💰 自转账 {Web3.from_wei(amount, 'ether')} ETH on {self.chain.name}")
        return self._execute_tx(tx)

    def approve_token(self, token_address, spender, amount=None):
        """2. Approve ERC20 - DeFi交互的前置步骤"""
        contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
        if amount is None:
            amount = 2**256 - 1  # max approve
        tx = contract.functions.approve(spender, amount).build_transaction(
            self._build_base_tx()
        )
        logger.info(f"🔓 Approve token {token_address[:10]}... for spender {spender[:10]}...")
        return self._execute_tx(tx)

    def swap_eth_for_token(self, token_address, eth_amount_range=(0.001, 0.01)):
        """3. Swap ETH → Token on Uniswap V3"""
        amount = Web3.to_wei(random.uniform(*eth_amount_range), "ether")
        logger.info(
            f"🔄 Swap {Web3.from_wei(amount, 'ether')} ETH → token on {self.chain.name}"
        )
        # Note: 实际 swap 需要调用 Uniswap V3 SwapRouter
        # 这里是框架，具体实现需要根据链的 Router 地址
        # 完整实现见下方 SwapRouter 调用
        tx = self._build_base_tx(value=amount)
        # SwapRouter.exactInputSingle(params)
        # params = (tokenIn=WETH, tokenOut=token, fee=3000, recipient=self, deadline, amountIn, amountOutMinimum=0, sqrtPriceLimitX96=0)
        # 完整调用需要 Uniswap V3 Router ABI 和地址
        logger.info("⚠️  Swap 完整实现需要 Uniswap V3 Router ABI (见 README)")
        return None

    def wrap_eth(self, weth_address, amount_range=(0.001, 0.01)):
        """4. Wrap ETH → WETH"""
        amount = Web3.to_wei(random.uniform(*amount_range), "ether")
        # WETH.deposit() - payable
        weth_abi = json.loads("""[
            {"constant":false,"inputs":[],"name":"deposit","outputs":[],"type":"function"},
            {"constant":false,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"}
        ]""")
        contract = self.w3.eth.contract(address=weth_address, abi=weth_abi)
        tx = contract.functions.deposit().build_transaction(
            self._build_base_tx(value=amount)
        )
        logger.info(f"📦 Wrap {Web3.from_wei(amount, 'ether')} ETH → WETH on {self.chain.name}")
        return self._execute_tx(tx)


# ============================================================
# 农场调度器
# ============================================================

class AirdropFarmScheduler:
    """调度多链交互，模拟真人行为模式"""

    def __init__(self, key_manager: SecureKeyManager):
        self.key_manager = key_manager
        self.address = key_manager.get_address()
        self.connections = {}
        self.engines = {}
        self.stats = {
            "total_tx": 0,
            "total_gas_eth": 0.0,
            "total_gas_usd": 0.0,
            "interactions": [],
        }

    def connect_chain(self, chain_name: str):
        """连接到一条链并初始化交互引擎"""
        conn = ChainConnection(chain_name)
        if conn.w3.is_connected():
            self.connections[chain_name] = conn
            self.engines[chain_name] = InteractionEngine(conn, self.key_manager)
            return True
        return False

    def check_balances(self):
        """检查所有链上的 ETH 余额"""
        logger.info("=" * 50)
        logger.info("📊 钱包余额检查")
        for name, conn in self.connections.items():
            balance = conn.get_eth_balance(self.address)
            logger.info(f"  {conn.chain_display}: {balance:.4f} ETH")
        logger.info("=" * 50)

    def run_daily_plan(self, plan_config: dict):
        """
        执行每日交互计划

        plan_config 格式:
        {
            "chains": ["ethereum", "arbitrum", "optimism", "base"],
            "actions_per_chain": {
                "ethereum": ["self_transfer", "wrap_eth"],
                "arbitrum": ["self_transfer", "wrap_eth"],
                "optimism": ["self_transfer"],
                "base": ["self_transfer", "wrap_eth"],
            },
            "delay_between_actions": (30, 180),  # 秒，随机延迟
        }
        """
        logger.info("🚀 开始执行每日交互计划")
        self.check_balances()

        for chain_name in plan_config["chains"]:
            if chain_name not in self.engines:
                if not self.connect_chain(chain_name):
                    logger.warning(f"跳过 {chain_name}（连接失败）")
                    continue

            engine = self.engines[chain_name]
            actions = plan_config["actions_per_chain"].get(chain_name, [])
            conn = self.connections[chain_name]

            # 检查余额是否够 gas
            balance = conn.get_eth_balance(self.address)
            if balance < 0.001:
                logger.warning(f"⚠️  {chain_name} ETH余额不足 ({balance:.4f}), 跳过")
                continue

            logger.info(f"📍 处理 {conn.chain_display} - {len(actions)} 个操作")

            for action in actions:
                # 随机延迟，模拟真人
                delay = random.uniform(*plan_config.get("delay_between_actions", (30, 180)))
                logger.info(f"⏳ 等待 {delay:.0f} 秒...")
                time.sleep(delay)

                try:
                    result = self._execute_action(engine, action, chain_name)
                    if result:
                        self.stats["total_tx"] += 1
                        gas_eth = Web3.from_wei(result.gasUsed * conn.get_gas_price(), "ether")
                        self.stats["total_gas_eth"] += gas_eth
                        self.stats["interactions"].append({
                            "chain": chain_name,
                            "action": action,
                            "tx_hash": result.transactionHash.hex(),
                            "gas_used": result.gasUsed,
                            "timestamp": datetime.now().isoformat(),
                        })
                except Exception as e:
                    logger.error(f"❌ {action} on {chain_name} 失败: {e}")

        # 保存统计
        self._save_stats()
        self._print_summary()

    def _execute_action(self, engine: InteractionEngine, action: str, chain: str):
        """执行单个交互动作"""
        if action == "self_transfer":
            return engine.self_transfer()
        elif action == "wrap_eth":
            weth = CONTRACTS.get(chain, {}).get("WETH")
            if weth:
                return engine.wrap_eth(weth)
            else:
                logger.warning(f"{chain} 无 WETH 地址配置")
                return None
        elif action == "approve":
            # 需要指定 token 和 spender
            logger.info("approve 需要额外参数，暂跳过")
            return None
        else:
            logger.warning(f"未知操作: {action}")
            return None

    def _save_stats(self):
        """保存交互统计到 JSON"""
        stats_file = LOG_DIR / "stats.json"
        with open(stats_file, "w") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)
        logger.info(f"📊 统计已保存到 {stats_file}")

    def _print_summary(self):
        """打印执行摘要"""
        logger.info("=" * 50)
        logger.info("📋 执行摘要")
        logger.info(f"  总交易数: {self.stats['total_tx']}")
        logger.info(f"  总 gas 花费: {self.stats['total_gas_eth']:.6f} ETH")
        logger.info(f"  交互详情:")
        for i in self.stats["interactions"]:
            logger.info(
                f"    {i['chain']} | {i['action']} | {i['tx_hash']} | gas: {i['gas_used']}"
            )
        logger.info("=" * 50)


# ============================================================
# 预设计划
# ============================================================

DEFAULT_7_DAY_PLAN = {
    "day_1": {
        "chains": ["ethereum", "arbitrum"],
        "actions_per_chain": {
            "ethereum": ["self_transfer"],
            "arbitrum": ["self_transfer"],
        },
        "delay_between_actions": (60, 300),
    },
    "day_2": {
        "chains": ["optimism", "base"],
        "actions_per_chain": {
            "optimism": ["self_transfer"],
            "base": ["self_transfer"],
        },
        "delay_between_actions": (60, 300),
    },
    "day_3": {
        "chains": ["ethereum", "arbitrum"],
        "actions_per_chain": {
            "ethereum": ["wrap_eth"],
            "arbitrum": ["wrap_eth"],
        },
        "delay_between_actions": (120, 600),
    },
    "day_4": {
        "chains": ["optimism", "base"],
        "actions_per_chain": {
            "optimism": ["self_transfer"],
            "base": ["wrap_eth"],
        },
        "delay_between_actions": (120, 600),
    },
    "day_5": {
        "chains": ["arbitrum", "base"],
        "actions_per_chain": {
            "arbitrum": ["self_transfer", "wrap_eth"],
            "base": ["self_transfer"],
        },
        "delay_between_actions": (180, 900),
    },
    "day_6": {
        "chains": ["ethereum", "polygon"],
        "actions_per_chain": {
            "ethereum": ["self_transfer"],
            "polygon": ["self_transfer"],
        },
        "delay_between_actions": (300, 1200),
    },
    "day_7": {
        "chains": ["arbitrum", "optimism", "base"],
        "actions_per_chain": {
            "arbitrum": ["wrap_eth"],
            "optimism": ["self_transfer"],
            "base": ["wrap_eth"],
        },
        "delay_between_actions": (300, 1200),
    },
}


# ============================================================
# 主入口
# ============================================================

def main():
    print("""
    🌾 Airdrop Farmer v1.0
    =========================
    自动链上交互脚本 - 为空投快照创造活跃记录

    ⚠️  安全提示:
    - 私钥通过环境变量注入，绝不存储在文件中
    - 交互金额很小 (0.0001-0.01 ETH)，主要目的是创造链上记录
    - 所有操作有完整日志

    用法:
    1. 设置私钥: export ETH_PRIVATE_KEY=你的私钥（不带0x也可以）
    2. 运行: python airdrop_farmer.py --day 1
    3. 或完整7天: python airdrop_farmer.py --full-plan
    """)

    # 初始化安全模块
    key_manager = SecureKeyManager()

    # 初始化调度器
    scheduler = AirdropFarmScheduler(key_manager)

    # 连接所有链
    connected = []
    for chain_name in CHAINS:
        if scheduler.connect_chain(chain_name):
            connected.append(chain_name)

    if not connected:
        logger.error("❌ 无法连接任何链！检查 RPC 节点。")
        sys.exit(1)

    logger.info(f"✅ 已连接 {len(connected)} 条链: {connected}")

    # 检查余额
    scheduler.check_balances()

    # 执行计划
    if "--full-plan" in sys.argv:
        for day_num, plan in DEFAULT_7_DAY_PLAN.items():
            logger.info(f"\n{'='*50}")
            logger.info(f"🗓️  执行 Day {day_num}")
            logger.info(f"{'='*50}")
            scheduler.run_daily_plan(plan)
            logger.info(f"✅ Day {day_num} 完成，等待24小时...")
            # 实际部署时用 cron 每天触发，这里不真的sleep 24h
    elif "--day" in sys.argv:
        day_idx = sys.argv.index("--day")
        day_num = int(sys.argv[day_idx + 1])
        plan_key = f"day_{day_num}"
        if plan_key in DEFAULT_7_DAY_PLAN:
            scheduler.run_daily_plan(DEFAULT_7_DAY_PLAN[plan_key])
        else:
            logger.error(f"❌ 无 Day {day_num} 配置 (1-7)")
            sys.exit(1)
    else:
        # 单次测试运行
        logger.info("🧪 测试模式 - 仅在 Arbitrum 上做一次自转账")
        test_plan = {
            "chains": ["arbitrum"],
            "actions_per_chain": {"arbitrum": ["self_transfer"]},
            "delay_between_actions": (5, 15),
        }
        scheduler.run_daily_plan(test_plan)


if __name__ == "__main__":
    main()