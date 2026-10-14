"""pytest 共享 fixture。"""

# test_ops 的运维任务环境，供其它测试模块以 ops_env 名字复用
from tests.test_ops import env as ops_env  # noqa: F401
