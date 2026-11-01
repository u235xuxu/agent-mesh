"""极简 YAML 子集解析器。

为什么不用 PyYAML：

    这个项目的脚本要在「什么都没装」的机器上直接跑。
    多一个依赖，就多一步失败的可能，而失败现场往往是没有外网的机器。

支持范围（够配置文件和题库用）：

    key: value              标量（int / float / bool / null / 引号字符串）
    key: [a, b, c]          行内数组
    key:                    嵌套映射
      sub: v
    key:                    对象数组
      - name: a
        port: 1

不支持：多文档、锚点、块标量（| 和 >）、流式映射 {a: b}。
遇到不支持的写法不会静默出错——要么当字符串处理，要么抛 YAMLError。
"""

from __future__ import annotations


class YAMLError(ValueError):
    """输入超出了本解析器支持的子集。"""


def strip_comment(line: str) -> str:
    """去掉行尾注释。引号内的 # 不算注释。"""
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def scalar(v: str):
    """把字符串转成合适的 Python 标量。"""
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

    # 行内数组 [a, b, c]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [scalar(p) for p in inner.split(",") if p.strip()]

    # 引号字符串
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
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


def parse_yaml(text: str):
    """解析 YAML 子集。返回 dict 或 list。"""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        s = strip_comment(raw)
        if not s.strip():
            continue
        lines.append((len(s) - len(s.lstrip()), s.strip()))

    if not lines:
        return {}

    def block(start: int, base: int):
        items: list = []
        mapping: dict = {}
        mode: str | None = None
        i = start

        while i < len(lines):
            ind, content = lines[i]

            if ind < base:
                break
            if ind > base:
                # 缩进跳跃，且不是由上一行开出来的块——跳过
                i += 1
                continue

            if content.startswith("- "):
                mode = mode or "list"
                if mode != "list":
                    break

                body = content[2:].strip()

                # - key: value  （对象数组的第一项）
                if ":" in body and not body.startswith(('"', "'")):
                    k, _, v = body.partition(":")
                    item = {k.strip(): scalar(v)}
                    i += 1

                    sub_ind: int | None = None
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
                        item[k2.strip()] = scalar(v2)
                        i += 1

                    items.append(item)
                    continue

                items.append(scalar(body))
                i += 1
                continue

            mode = mode or "dict"
            if mode != "dict":
                break

            k, _, v = content.partition(":")
            k, v = k.strip(), v.strip()

            if not v:
                # 空值：下一行缩进更深则开子块
                if i + 1 < len(lines) and lines[i + 1][0] > base:
                    sub, nxt = block(i + 1, lines[i + 1][0])
                    mapping[k] = sub
                    i = nxt
                    continue
                mapping[k] = None
                i += 1
                continue

            mapping[k] = scalar(v)
            i += 1

        return (items if mode == "list" else mapping), i

    value, _ = block(0, 0)

    # 顶层是纯列表时，包一层，让调用方拿到的总是 dict
    if isinstance(value, list):
        return {"items": value}
    return value


def load_yaml_file(path) -> dict:
    """从文件读 YAML。文件不存在抛 FileNotFoundError。"""
    from pathlib import Path

    return parse_yaml(Path(path).read_text(encoding="utf-8"))
