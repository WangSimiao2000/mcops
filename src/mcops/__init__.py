"""mc-ops：自托管 Minecraft（Fabric）服务器的一体化运维平台。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcops")  # 唯一来源：pyproject.toml
except PackageNotFoundError:  # 未安装、直接从源码目录运行
    __version__ = "0.0.0+unknown"
