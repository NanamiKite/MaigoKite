"""Readable audit evidence generated from Chromium measurements, not copied token constants."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path
from typing import TypedDict

from pydantic import JsonValue, TypeAdapter


class InkRow(TypedDict):
    text: str
    selector: str
    color: list[float]
    background: list[float]
    tokens: list[str]
    opacity: float
    disabled: bool
    ratio: float
    surface: str


class InkSnapshot(TypedDict):
    palette: dict[str, list[float]]
    matrix: dict[str, list[float]]
    rows: list[InkRow]
    decorations: list[dict[str, JsonValue]]


class ChangedFile(TypedDict):
    file: str
    reason: str


def write_report(root: Path) -> None:
    parts = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Inkline 文字色审计</title>',
        "<style>body{font:15px/1.6 system-ui;margin:24px;color:#222;background:#faf8f4}"
        "table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
        "th,td{padding:6px;border:1px solid #ccc;white-space:nowrap}th{background:#eee;position:sticky;top:0}"
        ".low{background:#ffded7;color:#8b1900}.matrix{overflow:auto;max-height:65vh}img{width:100%}"
        ".pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}summary{cursor:pointer;font-weight:600}</style>",
        "<h1>Inkline 静态文字色：Chromium 审计与前后截图</h1>",
        "<p>真实 Chromium / Vite 隔离展示数据，1440×1100；不是 jsdom，也不代表真实后端或桌面打包验收。"
        "此次仅修改文字色，不接入案头背景。</p>",
        "<p>36 列：六预设 × 两主题 × 三纸面。低于 4.5 的格子标红。正文目标 7，accent-ink 在 paper-2 目标 5；"
        "白字 on-accent 只用于实色按钮，不能以纸面判为缺陷。原始 accent / wait 仍是装饰色，"
        "低比值保留在审计表中，不用于普通文字。</p>",
        "<p>表使用实际 shell 的 computed 色值。真实文本按最近不透明祖先和透明叠层检查；"
        "模式胶囊的滑块是同级背景，另计其实际颜色。disabled 与 aria-hidden 装饰不作为普通可读文字。"
        "此轮没有案头图片，不声称完成后续截图像素采样验收。</p>",
    ]
    for phase in ("before", "after"):
        source = root / phase / "inventory.json"
        if not source.exists():
            continue
        report = TypeAdapter(dict[str, InkSnapshot]).validate_json(source.read_text(encoding="utf-8"))
        keys = [
            f"{theme}-{accent}-chat"
            for theme in ("light", "dark")
            for accent in ("stone", "blue", "green", "earth", "lotus", "ink")
        ]
        if not all(key in report for key in keys):
            continue
        tokens = list(
            dict.fromkeys(token for key in keys for token in report[key]["matrix"] if not token.startswith("--paper"))
        )
        # Missing tokens in the initial audit were CSS-invalid, not real text colors.
        if phase == "before":
            tokens = [
                token
                for token in tokens
                if token
                not in (
                    "--accent-ink",
                    "--accent-ink-action",
                    "--seal-ink",
                    "--seal-ink-action",
                    "--stone-ink",
                    "--on-accent-text",
                )
            ]
        parts += [
            f'<h2>{phase}：{len(report)} 个视图状态</h2><div class="matrix"><table><thead><tr><th>文字／语义令牌</th>'
        ]
        for key in keys:
            parts += [f"<th>{key.removesuffix('-chat')}<br>{paper}</th>" for paper in ("paper", "paper-2", "paper-3")]
        parts.append("</tr></thead><tbody>")
        for token in tokens:
            parts.append(f"<tr><th>{escape(token)}</th>")
            for key in keys:
                for ratio in report[key]["matrix"].get(token, [None] * 3):
                    parts.append(
                        "<td>未定义</td>"
                        if ratio is None
                        else f'<td class="{"low" if ratio < 4.5 else ""}">{ratio:.3f}</td>'
                    )
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
        with (root / phase / "matrix.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["token"] + [f"{key}/{paper}" for key in keys for paper in ("paper", "paper-2", "paper-3")])
            for token in tokens:
                writer.writerow(
                    [token]
                    + [
                        f"{ratio:.3f}" + (" LOW" if ratio < 4.5 else "")
                        for key in keys
                        for ratio in report[key]["matrix"][token]
                    ]
                )
        inventory: dict[tuple[float, ...], set[str]] = {}
        for data in report.values():
            for row in data["rows"]:
                inventory.setdefault(tuple(row["color"]), set()).update(
                    token for token in row["tokens"] if token in tokens
                )
        parts += [
            f"<p>{len(inventory)} 种实际文字 RGB（跨预设去重），含已注册的危险、等待和 diff 语义色。</p>",
            f'<a href="{phase}/inventory.json">完整文本节点、computed 值和实际底色记录</a> · '
            f'<a href="{phase}/matrix.csv">36 列 CSV</a>',
        ]
        if phase == "after":
            failures = TypeAdapter(list[JsonValue]).validate_json(
                (root / phase / "failures.json").read_text(encoding="utf-8")
            )
            nodes = sum(len(data["rows"]) for data in report.values())
            parts.append(
                f'<p>{nodes} 个可见文本节点；{len(failures)} 项失败。<a href="after/failures.json">最终断言结果</a></p>'
            )
        else:
            parts.append(
                "<p>初始采集器曾将未定义的候选令牌当作继承色，并漏计模式胶囊的同级背景。"
                "本表已排除未定义令牌；原始记录保留，初始 findings 不用作缺陷数量或验收结论。"
                "最终检查使用修正后的采集器重新运行，不修改产品用例断言。</p>"
            )
        parts.append("<details><summary>实际文字颜色去重列表</summary><table><tr><th>RGBA</th><th>匹配令牌</th></tr>")
        for color, names in sorted(inventory.items()):
            parts.append(f"<tr><td>{color}</td><td>{escape(', '.join(sorted(names)))}</td></tr>")
        parts.append("</table></details>")
    parts.append("<h2>前／后对照（点击展开）</h2>")
    for theme in ("light", "dark"):
        for accent in ("earth", "lotus", "green", "stone", "blue", "ink"):
            for scene in (
                "chat",
                "chat-receipt",
                "waiting",
                "model-menu",
                "settings-models",
                "settings-general",
                "settings-select",
                "changes",
                "capsule",
            ):
                name = f"{theme}-{accent}-{scene}.png"
                if not all((root / phase / name).exists() for phase in ("before", "after")):
                    continue
                parts.append(f'<details><summary>{theme} / {accent} / {scene}</summary><div class="pair">')
                parts += [
                    f'<figure><figcaption>{phase}</figcaption><a href="{phase}/{name}">'
                    f'<img loading="lazy" src="{phase}/{name}" alt="{phase} {name}"></a></figure>'
                    for phase in ("before", "after")
                ]
                parts.append("</div></details>")
    if (root / "after/light-earth-waiting-reminder.png").exists():
        parts.append(
            "<h2>补充：待处理项滚出视口</h2><p>以下仅为 after 补充检查，未采集同状态的 before，"
            '不冒充前后对照。实际等待文字对纸面为浅色 6.108:1、深色 9.018:1。</p><div class="pair">'
        )
        for theme in ("light", "dark"):
            name = f"after/{theme}-earth-waiting-reminder.png"
            parts.append(
                f'<figure><figcaption>{theme}</figcaption><img loading="lazy" src="{name}" alt="待处理提示"></figure>'
            )
        parts.append('</div><a href="after/waiting-reminder-inventory.json">补充 computed 记录</a>')
    manifest = root / "changed-files.json"
    if manifest.exists():
        parts.append("<h2>改动文件与理由</h2><ul>")
        for item in TypeAdapter(list[ChangedFile]).validate_json(manifest.read_text(encoding="utf-8")):
            parts.append(f"<li><code>{escape(item['file'])}</code>：{escape(item['reason'])}</li>")
        parts.append("</ul>")
    parts.append("</html>")
    (root / "report.html").write_text("\n".join(parts), encoding="utf-8")
