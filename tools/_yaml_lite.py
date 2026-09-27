#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_yaml_lite.py — 全库唯一的 YAML 读取实现（2026-09-26 收敛自 8 份副本）。

历史：v0.1 并行施工时 cluster/prioritize/score/evalset/calibrate/regress/govern/clarify
各自带一份 YAML 子集解析器，语义有分叉（空文档、布尔大小写、flow 风格）。
同一份 pack 在两处解析结果可能不同——对评测标准是不可接受的。本模块收敛为唯一实现。

策略：
  1. 环境有 PyYAML → safe_load（权威路径）；
  2. 无 PyYAML → 内置子集解析器（只认 scenario-pack 用到的结构：缩进 map、"- " 列表、
     嵌套、引号串、行内注释、true/false/null、int/float）；
  3. 遇到不识别结构 → YamlLiteError（ValueError 子类，**不猜**）。

所有站点统一 `from _yaml_lite import load_yaml_file, YamlLiteError`；
YamlLiteError 继承 ValueError，因此旧调用点的 `except (XxxError, ValueError)` 保持有效。
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml  # type: ignore

    _HAS_PYYAML = True
except ImportError:  # pragma: no cover - 取决于部署环境
    yaml = None
    _HAS_PYYAML = False


class YamlLiteError(ValueError):
    """YAML 读取失败。继承 ValueError：既有 except 元组无需改动。"""


# --------------------------------------------------------------------------- #
# 公共入口
# --------------------------------------------------------------------------- #


def load_yaml_file(path) -> dict:
    """读 YAML 文件返回 dict。空文档返回 {}。任何失败都抛 YamlLiteError。"""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise YamlLiteError(f"{p}: 文件不存在") from e
    except UnicodeDecodeError as e:
        raise YamlLiteError(f"{p}: 非 UTF-8 编码") from e
    doc = load_yaml_text(raw, source=str(p))
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise YamlLiteError(f"{p}: 顶层必须是 mapping，得到 {type(doc).__name__}")
    return doc


def load_yaml_text(text: str, source: str = "<text>") -> dict | list | None:
    if _HAS_PYYAML:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise YamlLiteError(f"{source}: PyYAML 解析失败: {e}") from e
    return _subset_parse(text, source)


# --------------------------------------------------------------------------- #
# 子集解析器（无 PyYAML 时）
# --------------------------------------------------------------------------- #


def _strip_comment(line: str) -> str:
    """去掉行内注释：` #`（空白+#）起算；引号内的 # 不动。"""
    in_s, quote = None, ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote, in_s = ch, i
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(v: str):
    s = v.strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true",):
        return True
    if low in ("false",):
        return False
    if low in ("null", "~", "none"):
        return None
    if s.startswith(("[", "{")):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise YamlLiteError(f"不支持的 flow 结构: {s!r}") from e
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if " #" in s:  # 引号串尾部带注释的兜底
        return s.split(" #", 1)[0].strip()
    return s


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _subset_parse(text: str, source: str):
    lines: list = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        lines.append((_indent_of(stripped), stripped.strip()))
    if not lines:
        return None
    value, idx = _parse_block(lines, 0, lines[0][0], source)
    if idx != len(lines):
        raise YamlLiteError(f"{source}: 第 {idx + 1} 段无法归并（缩进错乱？）")
    return value


def _parse_block(lines, i: int, indent: int, source: str):
    if lines[i][1].startswith("- "):
        return _parse_list(lines, i, indent, source)
    return _parse_map(lines, i, indent, source)


def _parse_map(lines, i: int, indent: int, source: str):
    out: dict = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise YamlLiteError(f"{source}: 意外缩进于 {content!r}")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlLiteError(f"{source}: 不是 key: value 行: {content!r}")
        key, _, rest = content.partition(":")
        key = key.strip().strip("\"'")
        if key.lstrip("-").isdigit():  # YAML 数字键：与 PyYAML 一致按 int 建键（如 scale_anchors）
            key = int(key)
        rest = rest.strip()
        i += 1
        if rest:
            out[key] = _scalar(rest)
            continue
        # 块级子结构：下一行缩进更大，或同缩进的 "- " 列表
        if i < len(lines) and (lines[i][0] > indent or (lines[i][0] == indent and lines[i][1].startswith("- "))):
            child_indent = lines[i][0]
            out[key], i = _parse_block(lines, i, child_indent, source)
        else:
            out[key] = None
    return out, i


def _parse_list(lines, i: int, indent: int, source: str):
    out: list = []
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or not content.startswith("- "):
            break
        item = content[2:].strip()
        i += 1
        if ":" in item and not item.startswith(("[", "{", "\"", "'")):
            # "- key: value" 起头的 map 项：把该项还原成虚拟 map 第一行再递归
            synthetic = [(indent + 2, item)]
            while i < len(lines) and lines[i][0] > indent:
                synthetic.append(lines[i])
                i += 1
            # 子块缩进以 item 的虚拟缩进为准
            child_indent = synthetic[0][0]
            for j, (sind, scontent) in enumerate(synthetic):
                if sind != child_indent:
                    synthetic[j] = (child_indent, scontent)
            val, consumed = _parse_map(synthetic, 0, child_indent, source)
            if consumed != len(synthetic):
                raise YamlLiteError(f"{source}: 列表项解析不一致: {item!r}")
            out.append(val)
        else:
            out.append(_scalar(item))
    return out, i


def subset_parse(text: str, source: str = "<text>"):
    """暴露给自检/测试用：强制走无 PyYAML 的子集路径。"""
    return _subset_parse(text, source)
