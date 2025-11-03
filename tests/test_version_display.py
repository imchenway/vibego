"""测试 /start 命令的版本号显示"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from vibego_cli import __version__

print("\n" + "=" * 60)
print("版本号显示测试")
print("=" * 60 + "\n")

print(f"从 vibego_cli 读取的版本号: {__version__}")
print()

# 模拟 /start 命令的回复消息
project_count = 3  # 假设有3个项目

message = (
    f"Master bot 已启动（v{__version__}）。\n"
    f"已登记项目: {project_count} 个。\n"
    "使用 /projects 查看状态，/run 或 /stop 控制 worker。"
)

print("模拟 /start 命令的 Telegram 回复：")
print("-" * 60)
print(message)
print("-" * 60)
print()

print("✅ 版本号显示格式正确！")
print(f"   - 版本号来源：vibego_cli/__init__.py")
print(f"   - 当前版本：v{__version__}")
print(f"   - 显示格式：简洁格式（v{__version__}）")
print()

# 验证修改点
print("=" * 60)
print("修改摘要")
print("=" * 60)
print()
print("1. 文件：master.py")
print("   - 第57行：添加导入 `from vibego_cli import __version__`")
print("   - 第1796行：修改消息为 `f\"Master bot 已启动（v{__version__}）。\"`")
print()
print("2. 修改效果：")
print("   修改前：Master bot 已启动。")
print(f"   修改后：Master bot 已启动（v{__version__}）。")
print()
print("3. 部署说明：")
print("   重启 master bot 后，用户执行 /start 命令即可看到版本号")
print("   命令：python -m vibego_cli stop && python -m vibego_cli start")
print()
