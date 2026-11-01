#!/usr/bin/env python3
"""
probe_mesh.py — 探测多机网络的连通性矩阵

设计原则（很重要）：
  默认直连，不读环境变量里的代理设置。
  因为「探测结果被代理污染」是这类工具最常见的失效方式 ——
  测出来的其实是代理的状态，不是目标站点的状态。

  要测走代理的情况，显式加 --use-proxy。

用法：
    python3 probe_mesh.py --config mesh.yaml
    python3 probe_mesh.py --config mesh.yaml --use-proxy
    python3 probe_mesh.py --config mesh.yaml --only vps
    python3 probe_mesh.py --host 1.2.3.4:22 --host 5.6.7.8:443
"""

import argparse
import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_TIMEOUT = 5
MAX_WORKERS = 12


# ─────────────────────────────────────────────────────────────
# 配置解析（复用极简 YAML 子集解析，避免依赖 PyYAML）
# ─────────────────────────────────────────────────────────────

def _strip_comment(line):
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            out.append(ch)
        elif ch == '#':
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(v):
    v = v.strip()
    if not v:
        return None
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none"):
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in inner.split(",") if p.strip()]
    if (v[0] == v[-1]) and v[0] in ('"', "'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def parse_yaml(text):
    """题库/配置用的 YAML 子集：嵌套 dict + list of dict。"""
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        s = _strip_comment(raw)
        if not s.strip():
            continue
        lines.append((len(s) - len(s.lstrip()), s.strip()))

    def block(start, base):
        items, mapping, mode = [], {}, None
        i = start
        while i < len(lines):
            ind, content = lines[i]
            if ind < base:
                break
            if ind > base:
                i += 1
                continue
            if content.startswith("- "):
                mode = mode or "list"
                if mode != "list":
                    break
                body = content[2:].strip()
                if ":" in body and not body.startswith(('"', "'")):
                    k, _, v = body.partition(":")
                    item = {k.strip(): _scalar(v)}
                    i += 1
                    sub_ind = None
                    while i < len(lines) and lines[i][0] > base:
                        si, sc = lines[i]
                        if sub_ind is None:
                            sub_ind = si
                        if si != sub_ind:
                            i += 1
                            continue
                        if sc.startswith("- "):
                            i += 1
                            continue
                        k2, _, v2 = sc.partition(":")
                        item[k2.strip()] = _scalar(v2)
                        i += 1
                    items.append(item)
                    continue
                items.append(_scalar(body))
                i += 1
                continue
            mode = mode or "dict"
            if mode != "dict":
                break
            k, _, v = content.partition(":")
            k, v = k.strip(), v.strip()
            if not v:
                if i + 1 < len(lines) and lines[i + 1][0] > base:
                    sub, nxt = block(i + 1, lines[i + 1][0])
                    mapping[k] = sub
                    i = nxt
                    continue
                mapping[k] = None
                i += 1
                continue
            mapping[k] = _scalar(v)
            i += 1
        return (items if mode == "list" else mapping), i

    val, _ = block(0, 0)
    return val if isinstance(val, dict) else {"nodes": val}


# ─────────────────────────────────────────────────────────────
# 探测
# ─────────────────────────────────────────────────────────────

def probe_tcp(host, port, timeout=DEFAULT_TIMEOUT, use_proxy=None):
    """
    纯 TCP 层探测。不走 HTTP 库，绕开环境变量的所有默认行为。

    use_proxy: None=直连, ("socks5", "127.0.0.1", 7897)=走 SOCKS5
    返回 (ok: bool, detail: str, elapsed_ms: int)
    """
    t0 = time.time()
    try:
        if use_proxy:
            kind, phost, pport = use_proxy
            if kind == "socks5":
                s = _socks5_connect(phost, pport, host, port, timeout)
            else:
                raise ValueError(f"不支持的代理类型: {kind}")
        else:
            s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True, "OPEN", int((time.time() - t0) * 1000)
    except socket.timeout:
        return False, "TIMEOUT", int((time.time() - t0) * 1000)
    except socket.gaierror:
        return False, "DNS_FAIL", int((time.time() - t0) * 1000)
    except ConnectionRefusedError:
        return False, "REFUSED", int((time.time() - t0) * 1000)
    except ConnectionResetError:
        return False, "RESET", int((time.time() - t0) * 1000)
    except OSError as e:
        if e.errno in (51, 65):     # Network is unreachable / No route to host
            return False, "UNREACHABLE", int((time.time() - t0) * 1000)
        return False, f"ERR:{type(e).__name__}", int((time.time() - t0) * 1000)


def _socks5_connect(phost, pport, host, port, timeout):
    """最小 SOCKS5 握手，无第三方依赖。"""
    s = socket.create_connection((phost, pport), timeout=timeout)
    s.settimeout(timeout)
    # 握手：版本5，1种方法（无认证）
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        s.close()
        raise OSError("SOCKS5 握手失败")
    # 请求：CONNECT，域名寻址
    h = host.encode()
    req = b"\x05\x01\x00\x03" + bytes([len(h)]) + h + port.to_bytes(2, "big")
    s.sendall(req)
    resp = s.recv(10)
    if len(resp) < 2 or resp[1] != 0:
        s.close()
        raise OSError(f"SOCKS5 连接失败，返回码 {resp[1] if len(resp) > 1 else '?'}")
    return s


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="多机网络连通性探测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--config", help="配置 YAML 文件")
    ap.add_argument("--host", action="append", default=[],
                    help="直接给 host:port，可重复")
    ap.add_argument("--use-proxy", action="store_true",
                    help="走 SOCKS5 代理（默认直连，反映真实情况）")
    ap.add_argument("--proxy", default="127.0.0.1:7897",
                    help="SOCKS5 代理地址，默认 127.0.0.1:7897")
    ap.add_argument("--only", help="只测名字匹配这个字符串的节点")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    proxy = None
    if args.use_proxy:
        ph, _, pp = args.proxy.partition(":")
        proxy = ("socks5", ph, int(pp or 1080))

    targets = []   # [(name, host, port, group)]

    if args.config:
        cfg = parse_yaml(Path(args.config).read_text(encoding="utf-8"))
        for node in (cfg.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or node.get("host") or "?")
            host = str(node.get("host") or "")
            if not host:
                continue
            ports = node.get("ports") or [22]
            if not isinstance(ports, list):
                ports = [ports]
            group = str(node.get("group") or "")
            for p in ports:
                targets.append((name, host, int(p), group))

    for spec in args.host:
        h, _, p = spec.partition(":")
        targets.append((h, h, int(p or 22), ""))

    if args.only:
        targets = [t for t in targets if args.only in t[0]]

    if not targets:
        sys.exit("没有探测目标。用 --config 或 --host 指定。")

    mode = f"走代理 {args.proxy}" if proxy else "直连"
    print(f"探测 {len(targets)} 条链路（{mode}，超时 {args.timeout}s）\n")

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(probe_tcp, t[1], t[2], args.timeout, proxy): t
                for t in targets}
        for f in as_completed(futs):
            name, host, port, group = futs[f]
            ok, detail, ms = f.result()
            results[(name, port)] = (ok, detail, ms, host, group)

    if args.json:
        out = [{"name": n, "host": r[3], "port": p, "ok": r[0],
                "detail": r[1], "ms": r[2], "group": r[4],
                "via": "proxy" if proxy else "direct"}
               for (n, p), r in sorted(results.items())]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 表格
    w = max(len(n) for n, _ in results) + 2
    print(f"{'节点':<{w}}{'端口':<7}{'结果':<12}{'耗时':<8}{'地址'}")
    print("─" * (w + 40))
    okc = 0
    for (name, port), (ok, detail, ms, host, _) in sorted(results.items()):
        mark = "✅" if ok else "❌"
        if ok:
            okc += 1
        print(f"{name:<{w}}{port:<7}{mark} {detail:<9}{str(ms)+'ms':<8}{host}")

    print("─" * (w + 40))
    print(f"可达 {okc} / {len(results)}")

    fails = {}
    for (name, port), (ok, detail, ms, host, _) in results.items():
        if not ok:
            fails.setdefault(detail, []).append(f"{name}:{port}")
    if fails:
        print("\n失败原因分布：")
        for d, nodes in fails.items():
            hint = {
                "TIMEOUT": "网络层不通：防火墙 / 被墙 / 主机不在线",
                "REFUSED": "主机在线但端口没开：服务没启动",
                "DNS_FAIL": "域名解析失败：DNS 配置或域名错误",
                "UNREACHABLE": "无路由：本地网络或代理配置问题",
                "RESET": "连接被重置：常见于中间设备干扰",
            }.get(d, "")
            print(f"  {d:<12} {len(nodes)} 条  ← {hint}")


if __name__ == "__main__":
    main()
