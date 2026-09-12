#!/usr/bin/env python3
"""Run one real browser science-video flow through cloud speech and local HyperFrames."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def fetch_project(studio: str, project_id: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{studio.rstrip('/')}/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["topic", "script", "media"], required=True)
    parser.add_argument("--input", required=True, help="Topic/script text, or a media file path.")
    parser.add_argument("--requirements", default="")
    parser.add_argument("--studio", default="http://127.0.0.1:5174")
    parser.add_argument("--token-file", type=Path, default=ROOT / "backend/storage/access_token.txt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    media_path = Path(args.input).resolve() if args.mode == "media" else None
    if media_path and not media_path.is_file():
        raise FileNotFoundError(media_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        origin = f"{args.studio.split('://', 1)[0]}://{args.studio.split('://', 1)[1].split('/', 1)[0]}"
        context.grant_permissions(["local-network-access"], origin=origin)
        page = context.new_page()
        page.goto(f"{args.studio.rstrip('/')}/projects/new?access_token={token}", wait_until="networkidle")
        page.get_by_role("button", name={"topic": "输入主题", "script": "输入文案", "media": "上传媒体"}[args.mode]).click()
        page.locator('input[placeholder*="黑洞为什么"]').fill(f"科普验收-{args.mode}")
        page.locator("select").nth(0).select_option("16:9")
        if args.mode == "topic":
            page.locator("textarea").nth(0).fill(args.input)
            page.locator("textarea").nth(1).fill(args.requirements)
        elif args.mode == "script":
            page.locator("textarea").fill(args.input)
        page.get_by_role("button", name=re.compile("创建.*项目|创建并上传媒体")).click()
        page.wait_for_url("**/studio?project=**", timeout=30_000)
        project_id = parse_qs(urlparse(page.url).query)["project"][0]
        if media_path:
            with page.expect_response(lambda res: "/assets/upload" in res.url and res.request.method == "POST", timeout=180_000) as upload:
                page.locator('input[type="file"]').set_input_files(str(media_path))
            if not upload.value.ok:
                raise RuntimeError(f"媒体上传失败：{upload.value.status} {upload.value.text()}")
            page.get_by_text("已上传", exact=True).wait_for(timeout=30_000)
        page.get_by_role("button", name="开始生成科普方案").click()
        generate = page.get_by_role("button", name=re.compile("确认生成"))
        analysis_deadline = time.monotonic() + 300
        while time.monotonic() < analysis_deadline:
            if generate.is_visible():
                break
            project = fetch_project(args.studio, project_id, token)
            if project.get("status") == "needs_input":
                page.wait_for_timeout(800)
                page.screenshot(path=str(args.output.with_suffix(".failure.png")), full_page=True)
                agent_text = page.locator("text=Agent").last.inner_text() if page.locator("text=Agent").count() else "请查看 Agent 对话"
                raise RuntimeError(f"分析任务需要补充：{agent_text}")
            if project.get("status") == "failed":
                page.wait_for_timeout(800)
                page.screenshot(path=str(args.output.with_suffix(".failure.png")), full_page=True)
                raise RuntimeError("分析任务未通过 Agent 内部审查，请查看项目聊天中的完整原因。")
            page.wait_for_timeout(1000)
        else:
            page.screenshot(path=str(args.output.with_suffix(".timeout.png")), full_page=True)
            raise TimeoutError("等待科普方案超过 300 秒。")
        generate.click()
        deadline = time.monotonic() + 420
        result_card = page.get_by_text("本机视频", exact=True)
        error_banner = page.locator('[class*="bg-error"]').first
        while time.monotonic() < deadline:
            if result_card.is_visible():
                break
            if error_banner.is_visible():
                message = error_banner.inner_text().strip()
                page.screenshot(path=str(args.output.with_suffix(".failure.png")), full_page=True)
                raise RuntimeError(f"网页流程失败：{message}")
            page.wait_for_timeout(1000)
        else:
            page.screenshot(path=str(args.output.with_suffix(".timeout.png")), full_page=True)
            raise TimeoutError("等待本机视频超过 420 秒。")
        with page.expect_download(timeout=60_000) as download:
            page.locator('a[download]').first.click()
        download.value.save_as(args.output)
        context.close()
        browser.close()

    probe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,width,height", "-of", "json", str(args.output)],
        text=True,
    ).strip()
    print(f"project_id={project_id}")
    print(f"total_seconds={time.perf_counter() - started:.2f}")
    print(probe)


if __name__ == "__main__":
    main()
