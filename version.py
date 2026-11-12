"""桥接器版本号。

源码里的版本号与即将发布的 Release 保持一致；CI 构建时会由
.github/workflows/release.yml 只替换本文件的 __version__ 一行，保证 EXE 里的
版本与构建来源一致：

- 打 ``v*`` 标签构建：取标签名（如 v1.8.1 → 1.8.1），并发布 Release；
- workflow_dispatch 手动构建：可用 ``version`` 输入指定版本号，留空则自动
  打 ``<当前版本>-dev.<run号>+<commit短sha>`` 戳（如 1.8.0-dev.42+79ebf37），
  仅上传构建产物，不发布 Release。

发新版本时先改这里、再打同名 tag。
"""

__version__ = "1.8.0"

#: 该桥接器适配的堡垒机客户端与传参风格（启动横幅展示用）。
BASTION_CLIENT = "齐治 AccessClient"
ARGUMENT_STYLE = "PuTTY 兼容传参"


def version_lines() -> list[str]:
    """Return the human-readable version block shown on the console."""
    return [
        f"桥接器版本   : v{__version__}",
        f"适用堡垒机   : {BASTION_CLIENT}（{ARGUMENT_STYLE}）",
    ]
