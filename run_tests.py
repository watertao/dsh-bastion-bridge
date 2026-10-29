"""Run the test suite with a stubbed paramiko module (offline environments).

Only for local smoke-testing on machines without PyPI access: the bridge tests
exercise SSHBridge logic through fake channels and never touch real SSH.
"""

import os
import sys
import types
import unittest


def _install_paramiko_stub() -> None:
    if "paramiko" in sys.modules:
        return
    stub = types.ModuleType("paramiko")

    class _Base:
        pass

    stub.SSHClient = type("SSHClient", (_Base,), {})
    stub.Transport = type(
        "Transport",
        (_Base,),
        {
            "_kex_info": {},
            "_key_info": {},
            "_cipher_info": {},
            "_mac_info": {},
            "_compression_info": {},
        },
    )
    stub.AutoAddPolicy = type("AutoAddPolicy", (_Base,), {})
    stub.AuthenticationException = type(
        "AuthenticationException", (Exception,), {}
    )
    stub.SSHException = type("SSHException", (Exception,), {})
    stub.socket = type("socket", (Exception,), {})
    sys.modules["paramiko"] = stub


if __name__ == "__main__":
    if "--stub-paramiko" in sys.argv:
        _install_paramiko_stub()
        sys.argv.remove("--stub-paramiko")
    sys.path.insert(0, os.getcwd())
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
