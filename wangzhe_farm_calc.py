#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     王者荣耀 S43 农场最优种菜计算器 v1.0                    ║
║     目标：每晚 00:00-01:00 收菜，最大化收益                 ║
║     作者：离恨烟 · 十二楼离恨楼                             ║
╚══════════════════════════════════════════════════════════════╝
"""

from datetime import datetime, timedelta, time
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
import json
import sys
import os

# ============================================================
# 作物数据库（基于 S43 玩家实测数据，持续更新中）
# type: 'xp' = 经验作物, 'coin' = 金币作物
# sell_price: 满级出售单价（农场币）
# xp_reward: 满级收获经验
# seed_cost: 种子费用（如有）
# water_times: 每轮可浇水次数
# ============================================================

@dataclass
class Crop:
    name: str
    unlock_level: int
    maturity_hours: float     # 基础成熟时间（小时）
    crop_type: str            # 'xp' 或 'coin'
    sell_price: int           # 满级出售单价（农场币）
    xp_reward: int            # 满级收获经验
    water_times: int = 2      # 每轮可浇水次数
    water_save_pct: float = 0.15  # 每次浇水缩短 15% 剩余时间

    @property
    def effective_maturity(self) -> float:
        """浇水后的实际成熟时间"""
        hours = self.maturity_hours
        remaining = hours
        for _ in range(self.water_times):
            remaining *= (1 - self.water_save_pct)
        return round(remaining, 2)

    @property
    def hourly_xp(self) -> float:
        return self.xp_reward / self.effective_maturity

    @property
    def hourly_coin(self) -> float:
        return self.sell_price / self.effective_maturity

    @property
    def cycles_per_day(self) -> int:
        """一天（24h）内可种植几轮"""
        return int(24 / self.effective_maturity)

    def __repr__(self):
        icon = "💰" if self.crop_type == "coin" else "📈"
        return f"{icon} {self.name} (Lv.{self.unlock_level}) {self.maturity_hours}h→{self.effective_maturity}h | {self.sell_price}币 {self.xp_reward}经验"


# ============================================================
# 已确认作物数据（2026-04-09 玩家实测）
# 未确认数据用 None 标记，运行时可手动补充
# ============================================================

CROP_DATABASE: List[Crop] = [
    # === 1~17级：经验作物为主 ===
    Crop("白菜",     1,  4,  "xp",   300,   50),
    Crop("胡萝卜",   2,  4,  "xp",   450,   70),
    Crop("土豆",     4,  4,  "xp",   680,   100),
    Crop("萝卜",     6,  4,  "xp",   950,   140),
    Crop("西红柿",   8,  8,  "xp",  1800,   220),
    Crop("茄子",    10,  8,  "xp",  2400,   300),
    Crop("辣椒",    12,  8,  "xp",  3200,   400),
    Crop("南瓜",    14, 16,  "xp",  4500,   550),

    # === 18级：分水岭 — 出现金币作物 ===
    Crop("大蒜",    18, 16,  "xp",  6030,   700),
    Crop("香蕉",    18, 16, "coin", 19320,    1),

    # === 19~21级 ===
    Crop("小麦",    19,  4,  "xp",  1200,   180),
    Crop("玉米",    20,  8,  "xp",  4000,   450),
    Crop("花生",    21,  8,  "xp",  5500,   500),

    # === 22级：1小时金币作物 ===
    Crop("黄瓜",    22,  1, "coin",  3500,    1),

    # === 23~25级 ===
    Crop("草莓",    23,  4, "coin",  2800,   10),
    Crop("蓝莓",    25,  4, "coin",  3800,   10),

    # === 26~29级 ===
    Crop("西瓜",    26,  8, "coin",  8500,    5),
    Crop("芒果",    28,  8, "coin", 11000,    5),

    # === 30级+ ===
    Crop("柚子",    30, 16, "coin", 28000,    5),
    Crop("火龙果",  32, 16, "coin", 38000,    5),
    Crop("葡萄",    34,  8,  "xp", 16000,  1200),
    Crop("榴莲",    38, 16, "coin", 55000,    5),
    Crop("樱桃",    42, 16, "coin", 75000,    5),
]

# ============================================================
# 用户配置 — 在这里填入你的数据，或运行时交互输入
# ============================================================

@dataclass
class PlayerConfig:
    current_level: int = 18
    current_xp: int = 0            # 当前经验
    xp_to_next_level: int = 500    # 升到下一级所需总经验（不是差值）
    coins_on_hand: int = 0         # 手上农场币
    num_plots: int = 6             # 当前田地数量
    daily_task_xp: int = 300       # 每日对局任务奖励经验
    daily_task_coins: int = 5000   # 每日对局任务奖励农场币
    harvest_start: time = time(0, 0)    # 收菜时间起始 00:00
    harvest_end: time = time(1, 0)      # 收菜时间结束 01:00
    target_coins: int = 0               # 目标攒多少币（0=不限）
    priority: str = "coin"              # 'coin'优先攒钱, 'xp'优先升级, 'balanced'均衡

    @property
    def xp_needed(self) -> int:
        """距离下一级还差多少经验"""
        return max(0, self.xp_to_next_level - self.current_xp)


# ============================================================
# 核心计算引擎
# ============================================================

class FarmCalculator:
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.available_crops = [
            c for c in CROP_DATABASE if c.unlock_level <= config.current_level
        ]
        self.xp_crops = [c for c in self.available_crops if c.crop_type == "xp"]
        self.coin_crops = [c for c in self.available_crops if c.crop_type == "coin"]

    def get_weekend_window(self, base_date: datetime) -> Tuple[datetime, datetime]:
        """计算周末双倍窗口：周五16:00 ~ 周日24:00"""
        # 找到本周五
        days_until_friday = (4 - base_date.weekday()) % 7
        if days_until_friday == 0 and base_date.hour >= 16:
            days_until_friday = 7  # 已过本周五，算下周五
        friday = base_date + timedelta(days=days_until_friday)
        friday_16 = friday.replace(hour=16, minute=0, second=0, microsecond=0)
        sunday_24 = friday_16 + timedelta(days=2, hours=8)  # 周日24:00
        return friday_16, sunday_24

    def is_weekend_double(self, check_time: datetime) -> bool:
        """判断某个时间点是否在周末双倍窗口内"""
        start, end = self.get_weekend_window(check_time)
        return start <= check_time <= end

    def days_until_weekend(self, base_date: datetime) -> int:
        """距离最近一个周末双倍窗口还有几天"""
        start, _ = self.get_weekend_window(base_date)
        delta = start - base_date
        return max(0, delta.days)

    def calculate_planting_schedule(self, crop: Crop, num_plots: int,
                                     target_harvest: time = time(0, 0)) -> Dict:
        """
        计算单个作物的最优种植计划
        返回：种植时间、预计收获时间、日收益等
        """
        mat = crop.effective_maturity

        # 从目标收获时间倒推种植时间
        harvest_dt = datetime.combine(datetime.now().date(), target_harvest)
        plant_dt = harvest_dt - timedelta(hours=mat)

        # 如果种植时间已过，则是明天的计划
        if plant_dt < datetime.now():
            plant_dt += timedelta(days=1)
            harvest_dt = plant_dt + timedelta(hours=mat)

        daily_earnings = crop.sell_price * num_plots
        daily_xp = crop.xp_reward * num_plots
        cycles = crop.cycles_per_day

        return {
            "crop": crop,
            "plant_time": plant_dt.strftime("%H:%M"),
            "harvest_time": harvest_dt.strftime("%H:%M"),
            "effective_maturity": mat,
            "daily_earnings": daily_earnings,
            "daily_xp": daily_xp,
            "cycles_per_day": cycles,
            "weekly_earnings": daily_earnings * 7,
            "weekly_earnings_weekend": daily_earnings * 2 * 2 + daily_earnings * 5,  # 2天双倍+5天正常
        }

    def calculate_multi_crop_schedule(self, target_harvest: time = time(0, 0)) -> Dict:
        """
        计算混合种植方案（部分地块经验、部分地块金币）
        """
        now = datetime.now()
        config = self.config
        days_to_weekend = self.days_until_weekend(now)
        is_weekend = self.is_weekend_double(now)

        # === 第一步：确定策略 ===
        # 如果距离周末很近（≤2天），且不是周末，考虑推迟出售
        # 如果急需升级，部分地块种经验
        # 否则全种金币

        xp_ratio = 0.0
        coin_ratio = 1.0

        if config.priority == "xp":
            # 需要多少轮经验作物才能升到下一级
            if self.xp_crops and config.xp_needed > 0:
                best_xp_crop = max(self.xp_crops, key=lambda c: c.hourly_xp)
                # 每轮经验 = 作物经验 * 地块数
                daily_xp_from_crop = best_xp_crop.xp_reward * config.num_plots
                days_needed = max(1, (config.xp_needed / (daily_xp_from_crop + config.daily_task_xp)))

                if days_needed <= days_to_weekend + 2:
                    # 时间充裕，部分种经验
                    xp_ratio = min(1.0, 0.5)  # 最多50%地块种经验
                else:
                    xp_ratio = min(1.0, config.xp_needed / (best_xp_crop.xp_reward * config.num_plots * 3))
        elif config.priority == "balanced":
            xp_ratio = 0.3

        xp_plots = max(0, int(config.num_plots * xp_ratio))
        coin_plots = config.num_plots - xp_plots

        # === 第二步：选最佳作物 ===
        best_xp_crop = max(self.xp_crops, key=lambda c: c.hourly_xp) if self.xp_crops else None
        best_coin_crop = max(self.coin_crops, key=lambda c: c.hourly_coin) if self.coin_crops else None

        # 如果没有金币作物，退而求其次用时薪最高的经验作物
        if not best_coin_crop:
            best_coin_crop = max(self.xp_crops, key=lambda c: c.hourly_coin) if self.xp_crops else None

        # === 第三步：生成时间表 ===
        schedules = []

        if best_coin_crop and coin_plots > 0:
            schedules.append(self.calculate_planting_schedule(
                best_coin_crop, coin_plots, target_harvest
            ))
            schedules[-1]["plot_count"] = coin_plots
            schedules[-1]["role"] = "💰 金币作物"

        if best_xp_crop and xp_plots > 0:
            schedules.append(self.calculate_planting_schedule(
                best_xp_crop, xp_plots, target_harvest
            ))
            schedules[-1]["plot_count"] = xp_plots
            schedules[-1]["role"] = "📈 经验作物"

        # === 第四步：计算升级时间线 ===
        total_daily_xp = config.daily_task_xp
        for s in schedules:
            crop = s["crop"]
            total_daily_xp += crop.xp_reward * s["plot_count"] * s["cycles_per_day"]

        days_to_level = 0
        if total_daily_xp > 0 and config.xp_needed > 0:
            days_to_level = (config.xp_needed + total_daily_xp - 1) // total_daily_xp

        # === 第五步：计算攒币时间线 ===
        total_daily_coins = config.daily_task_coins
        for s in schedules:
            crop = s["crop"]
            total_daily_coins += crop.sell_price * s["plot_count"] * s["cycles_per_day"]

        days_to_target = 0
        if total_daily_coins > 0 and config.target_coins > 0:
            remaining = config.target_coins - config.coins_on_hand
            if remaining > 0:
                # 考虑周末双倍：平均每天收益
                avg_daily = total_daily_coins * (9/7)  # 粗略估算双倍加成
                days_to_target = (remaining + int(avg_daily) - 1) // int(avg_daily)

        return {
            "schedules": schedules,
            "total_daily_xp": total_daily_xp,
            "total_daily_coins": total_daily_coins,
            "days_to_level": days_to_level,
            "days_to_target": days_to_target,
            "is_weekend_now": is_weekend,
            "days_to_weekend": days_to_weekend,
            "xp_plots": xp_plots,
            "coin_plots": coin_plots,
            "best_xp_crop": best_xp_crop,
            "best_coin_crop": best_coin_crop,
        }


# ============================================================
# 输出报告
# ============================================================

def print_banner():
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║     🌾 王者荣耀 S43 农场最优种菜计算器 🌾                   ║")
    print("║     每晚 00:00-01:00 收菜 · 最大化收益方案                 ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()


def print_crop_comparison(calculator: FarmCalculator):
    """输出可用作物对比表"""
    print("═" * 65)
    print("📋 可用作物一览（Lv.{} 及以下解锁）".format(calculator.config.current_level))
    print("═" * 65)
    print(f"{'作物':<8} {'类型':<6} {'基础':<6} {'浇水后':<7} {'时薪币':<10} {'时薪经验':<10} {'日轮数':<6}")
    print("─" * 65)

    # 按时薪金币排序（金币作物优先）
    sorted_crops = sorted(calculator.available_crops,
                          key=lambda c: (0 if c.crop_type == "coin" else 1, -c.hourly_coin))

    for crop in sorted_crops:
        type_str = "💰金币" if crop.crop_type == "coin" else "📈经验"
        print(f"{crop.name:<8} {type_str:<6} {crop.maturity_hours:<6.1f}h "
              f"{crop.effective_maturity:<6.1f}h "
              f"{crop.hourly_coin:<10.0f} {crop.hourly_xp:<10.1f} {crop.cycles_per_day:<6}")
    print()


def print_optimal_plan(result: Dict, calculator: FarmCalculator):
    """输出最优种植方案"""
    config = calculator.config
    print("═" * 65)
    print("🎯 最优种植方案")
    print("═" * 65)

    # 策略说明
    if result["best_coin_crop"]:
        print(f"  💰 最佳金币作物: {result['best_coin_crop'].name} "
              f"(时薪 {result['best_coin_crop'].hourly_coin:.0f} 币/时)")
    if result["best_xp_crop"]:
        print(f"  📈 最佳经验作物: {result['best_xp_crop'].name} "
              f"(时薪 {result['best_xp_crop'].hourly_xp:.1f} 经验/时)")

    print(f"  📊 地块分配: {result['coin_plots']}块金币 + {result['xp_plots']}块经验 = {config.num_plots}块")
    print()

    # 时间表
    print("⏰ 种植时间表（以 00:00 收菜为目标）")
    print("─" * 65)
    for s in result["schedules"]:
        crop = s["crop"]
        print(f"  {s['role']}")
        print(f"    🌱 {crop.name} × {s['plot_count']}块")
        print(f"    ⏰ 种植时间: {s['plant_time']}")
        print(f"    🌾 收获时间: {s['harvest_time']} (成熟 {s['effective_maturity']:.1f}h)")
        print(f"    💰 日收入: {s['daily_earnings']:,} 农场币")
        print(f"    📈 日经验: {s['daily_xp']:,}")
        print(f"    🔄 日轮数: {s['cycles_per_day']} 轮")
        print()

    # 每日汇总
    print("─" * 65)
    print(f"  📊 每日汇总:")
    print(f"     对局任务:  +{config.daily_task_xp:,} 经验  +{config.daily_task_coins:,} 币")
    print(f"     种植收入:  +{result['total_daily_xp']:,} 经验  +{result['total_daily_coins']:,} 币")
    print(f"     合计每日:  +{result['total_daily_xp'] + config.daily_task_xp:,} 经验  +{result['total_daily_coins'] + config.daily_task_coins:,} 币")
    print()


def print_timeline(result: Dict, calculator: FarmCalculator):
    """输出升级/攒币时间线"""
    config = calculator.config
    print("═" * 65)
    print("📅 升级 & 攒币时间线")
    print("═" * 65)

    now = datetime.now()

    if config.xp_needed > 0:
        level_date = now + timedelta(days=result["days_to_level"])
        print(f"  📈 升到 Lv.{config.current_level + 1}:")
        print(f"     还需 {config.xp_needed:,} 经验")
        print(f"     预计 {result['days_to_level']} 天后 ({level_date.strftime('%m/%d')})")
        print()

    if config.target_coins > 0:
        target_date = now + timedelta(days=result["days_to_target"])
        print(f"  💰 攒够 {config.target_coins:,} 币:")
        remaining = max(0, config.target_coins - config.coins_on_hand)
        print(f"     还需 {remaining:,} 币（当前 {config.coins_on_hand:,} 币）")
        print(f"     预计 {result['days_to_target']} 天后 ({target_date.strftime('%m/%d')})")
        print()

    # 周末窗口
    print(f"  🎉 周末双倍窗口:")
    start, end = calculator.get_weekend_window(now)
    print(f"     {start.strftime('%m/%d %H:%M')} ~ {end.strftime('%m/%d %H:%M')}")
    if result["is_weekend_now"]:
        print(f"     ✅ 当前正在双倍窗口！立刻出售收益翻倍！")
    else:
        print(f"     ⏳ 距离开启还有 {result['days_to_weekend']} 天")
        print(f"     💡 建议: 周五16:00前成熟一批作物，囤到双倍窗口出售")
    print()


def print_tips(calculator: FarmCalculator):
    """输出实用建议"""
    print("═" * 65)
    print("💡 每日必做清单")
    print("═" * 65)
    print("""
  1. ✅ 打一局1v1对局任务（约2分钟，人机无效）
  2. ✅ 给所有作物浇水（缩短15%剩余时间，可浇2次）
  3. ✅ 偷菜先偷再祝福（先偷→祝福→可能多得一次）
  4. ✅ 优先偷香蕉/柚子等高价值作物
  5. ✅ 解锁一键务农（农场雕像→设置）
  6. ✅ 周五16:00~周日24:00统一出售（双倍收益）
  7. ✅ 升小摊优先于升农场（升农场=方便别人偷）
  8. ✅ 仓库扩容跟上等级（高等级作物种类多）

  🌊 "流"能量: 每局对局获得，可额外加速作物生长
""")


def print_detailed_schedule(calculator: FarmCalculator):
    """输出24小时详细时间表"""
    print("═" * 65)
    print("⏰ 24小时详细种植时间表（以今晚 00:00 收菜为锚点）")
    print("═" * 65)
    print()

    for crop in calculator.available_crops:
        mat = crop.effective_maturity
        # 从00:00倒推种植时间
        plant_hour = (24 - mat) % 24
        type_icon = "💰" if crop.crop_type == "coin" else "📈"

        print(f"  {type_icon} {crop.name:<8} 成熟{mat:<6.1f}h → 每日可种{crop.cycles_per_day}轮")
        print(f"     最后一轮: {int(plant_hour):02d}:{int((plant_hour % 1) * 60):02d} 种植 → 00:00 收菜")

        # 如果一天能种多轮，列出所有时间
        if crop.cycles_per_day > 1:
            times = []
            for i in range(crop.cycles_per_day):
                t = (plant_hour + i * mat) % 24
                times.append(f"{int(t):02d}:{int((t % 1) * 60):02d}")
            print(f"     全部轮次: {' → '.join(times)}")
        print()


# ============================================================
# 交互式输入
# ============================================================

def interactive_input() -> PlayerConfig:
    """交互式收集用户数据"""
    print()
    print("📝 请输入你的农场数据（直接回车使用默认值）：")
    print()

    config = PlayerConfig()

    config.current_level = int(input(f"  当前农场等级 [默认 {config.current_level}]: ") or config.current_level)
    config.current_xp = int(input(f"  当前经验值 [默认 {config.current_xp}]: ") or config.current_xp)
    config.xp_to_next_level = int(input(f"  升下一级所需总经验 [默认 {config.xp_to_next_level}]: ") or config.xp_to_next_level)
    config.coins_on_hand = int(input(f"  手上农场币 [默认 {config.coins_on_hand}]: ") or config.coins_on_hand)
    config.num_plots = int(input(f"  当前田地数量 [默认 {config.num_plots}]: ") or config.num_plots)
    config.daily_task_xp = int(input(f"  每日对局任务奖励经验 [默认 {config.daily_task_xp}]: ") or config.daily_task_xp)
    config.daily_task_coins = int(input(f"  每日对局任务奖励农场币 [默认 {config.daily_task_coins}]: ") or config.daily_task_coins)

    target = input(f"  目标攒币数量（0=不限）[默认 {config.target_coins}]: ")
    config.target_coins = int(target) if target else config.target_coins

    print()
    print("  种植优先级:")
    print("    1. 💰 金币优先（推荐，农场升级缺币）")
    print("    2. 📈 经验优先（急需升级解锁新作物）")
    print("    3. ⚖️  均衡模式")
    choice = input(f"  请选择 [1/2/3, 默认 1]: ").strip()
    if choice == "2":
        config.priority = "xp"
    elif choice == "3":
        config.priority = "balanced"

    return config


# ============================================================
# 主程序
# ============================================================

def main():
    print_banner()

    # 检查是否有配置文件
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farm_config.json")

    # 支持命令行参数: python3 wangzhe_farm_calc.py --demo
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        config = PlayerConfig()
        print("  🎮 使用演示模式（默认数据）")
    elif os.path.exists(config_path):
        print(f"📂 检测到配置文件: {config_path}")
        try:
            use_config = input("  是否使用配置文件？[Y/n]: ").strip().lower()
        except EOFError:
            use_config = "y"
        if use_config != "n":
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = PlayerConfig(**{k: v for k, v in data.items() if k in PlayerConfig.__dataclass_fields__})
        else:
            config = interactive_input()
    else:
        try:
            use_cli = input("  使用交互输入？[Y/n]: ").strip().lower()
        except EOFError:
            use_cli = "n"
        if use_cli != "n":
            config = interactive_input()
        else:
            config = PlayerConfig()  # 使用默认值

    # 保存配置供下次使用
    try:
        save_config = input(f"\n  保存配置到文件？[Y/n]: ").strip().lower()
    except EOFError:
        save_config = "n"
    if save_config != "n":
        config_data = {
            "current_level": config.current_level,
            "current_xp": config.current_xp,
            "xp_to_next_level": config.xp_to_next_level,
            "coins_on_hand": config.coins_on_hand,
            "num_plots": config.num_plots,
            "daily_task_xp": config.daily_task_xp,
            "daily_task_coins": config.daily_task_coins,
            "target_coins": config.target_coins,
            "priority": config.priority,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 配置已保存到: {config_path}")

    # 创建计算器
    calculator = FarmCalculator(config)

    # 输出报告
    print("\n" * 2)

    # 1. 作物对比
    print_crop_comparison(calculator)

    # 2. 24小时时间表
    print_detailed_schedule(calculator)

    # 3. 最优方案
    result = calculator.calculate_multi_crop_schedule(time(0, 0))
    print_optimal_plan(result, calculator)

    # 4. 时间线
    print_timeline(result, calculator)

    # 5. 建议
    print_tips(calculator)

    print("═" * 65)
    print("  🌾 祝你农场大丰收！有问题随时来问离恨烟~")
    print("═" * 65)
    print()


if __name__ == "__main__":
    main()
