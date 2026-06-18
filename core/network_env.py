#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os


_DEFAULT_NO_PROXY_HOSTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "aimpapi.midea.com",
)


def configure_runtime_network_env(extra_no_proxy_hosts: tuple[str, ...] = ()) -> None:
    """让本机服务与美的内网 API 绕过失效的本地 http_proxy。

    启动 app / api / ingest 时调用一次即可，无需每次手动 unset 代理。
    """
    hosts = list(_DEFAULT_NO_PROXY_HOSTS) + list(extra_no_proxy_hosts)
    for env_name in ("NO_PROXY", "no_proxy"):
        existing = os.getenv(env_name, "")
        parts = [p.strip() for p in existing.split(",") if p.strip()]
        for host in hosts:
            if host not in parts:
                parts.append(host)
        joined = ",".join(parts)
        os.environ[env_name] = joined
