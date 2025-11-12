"""存放 CLI runtime 所需的数据文件（例如 worker requirements）。"""

from importlib import resources


def load_worker_requirements() -> str:
    """读取 worker 依赖清单，供 CLI 初始化虚拟环境时使用。"""

    with resources.files(__package__).joinpath("worker_requirements.txt").open("r", encoding="utf-8") as fp:
        return fp.read()
