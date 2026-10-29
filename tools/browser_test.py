# -*- coding: utf-8 -*-
"""Co-Sight 前端浏览器自动化测试工具（Playwright 驱动系统 Edge）v2
用途：直接控制网页版前端——打开页面、在输入框输入消息、点发送、等待并读取 AI 回复。
      v2 改进：回复读取更稳（轮询 .chat-msg.ai 元素数量+文本非空双条件）、
      支持 keep-open 保持会话（连续场景 A→D 用同一会话）、发送后主动展开面板。

用法：
    python3.12 tools/browser_test.py "查看错题"
    python3.12 tools/browser_test.py "求极限 lim(x→0) sinx/x 为什么等于1？" "保存这道题"
    python3.12 tools/browser_test.py --keep-open "查看错题"   # 单条且保持页面不关（跑完让用户看）
    python3.12 tools/browser_test.py --headless false "查看错题"

依赖：pip install --user playwright（用系统 Edge，无需下载浏览器）
"""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

URL = "http://localhost:7788/cosight/"
SEND_TIMEOUT_MS = 120000


def _expand_panel(page):
    """确保聊天面板展开（消息可能已渲染但面板收起）。"""
    try:
        panel = page.locator("#chat-panel")
        if panel.count() and "collapsed" in (panel.get_attribute("class") or ""):
            page.evaluate("if(window.expandChatPanel) window.expandChatPanel()")
            time.sleep(0.4)
    except Exception:
        pass


def wait_for_ai_reply(page, before_count, timeout_ms=SEND_TIMEOUT_MS):
    """等待出现新的 AI 消息：.chat-msg.ai 数量增加 且 最后一条文本非空。
    返回 (最后一条AI的文本列表, 是否超时)。"""
    deadline = time.time() + timeout_ms / 1000
    last_good = None
    while time.time() < deadline:
        _expand_panel(page)
        ai_msgs = page.locator(".chat-msg.ai")
        n = ai_msgs.count()
        if n > before_count:
            texts = ai_msgs.nth(n - 1).locator(".chat-bubble").all_inner_texts()
            joined = "\n".join(t for t in texts if t and t.strip())
            if joined.strip():
                last_good = joined.strip()
                # 再等一小段，确认没有继续追加（流式结束）
                time.sleep(1.0)
                ai_msgs2 = page.locator(".chat-msg.ai")
                n2 = ai_msgs2.count()
                texts2 = ai_msgs2.nth(n2 - 1).locator(".chat-bubble").all_inner_texts()
                joined2 = "\n".join(t for t in texts2 if t and t.strip())
                if n2 == n and joined2.strip() == last_good:
                    return [last_good], False
                last_good = joined2.strip() or last_good
        time.sleep(0.5)
    return ([last_good] if last_good else []), True


def send_and_wait(page, msg):
    """发送一条消息并等待 AI 回复，返回 (回复文本, 耗时, 是否超时)。
    注意：before 计数必须在发送动作之前完成（快通道回复可能秒回）。"""
    _expand_panel(page)
    # 确保输入框可见（欢迎页 → 主界面）
    main_input = page.locator("#message-input")
    if main_input.count() == 0 or not main_input.is_visible():
        page.wait_for_selector("#initial-message-input", timeout=10000)
        page.locator("#initial-message-input").fill(msg)
        before = page.locator(".chat-msg.ai").count()  # 发送前计数
        page.locator("#initial-send-button").click()
        time.sleep(1.8)  # 欢迎页 → 主界面切换
    else:
        before = page.locator(".chat-msg.ai").count()  # 发送前计数
        main_input.fill(msg)
        page.locator("#send-button").click()
    t0 = time.time()
    replies, timed_out = wait_for_ai_reply(page, before)
    elapsed = time.time() - t0
    return ("\n".join(replies) if replies else "(无回复)"), elapsed, timed_out


def upload_file_to_page(page, file_path, wait_ok=True):
    """在页面上传文件（模拟用户点击"上传文件"按钮选择文件，等价拖拽上传）。
    使用 file_chooser（真实用户操作路径），随后验证"已上传"列表出现。"""
    import os
    fname = os.path.basename(file_path)
    page.wait_for_selector("#initial-file-upload-button, #file-upload-button", timeout=10000)
    # 优先用初始欢迎页的上传按钮（若在欢迎页）；否则用主输入区
    btn = page.locator("#initial-file-upload-button")
    if btn.count() == 0 or not btn.is_visible():
        btn = page.locator("#file-upload-button")
    with page.expect_file_chooser(timeout=15000) as fc_info:
        btn.click()
    chooser = fc_info.value
    chooser.set_files(file_path)
    if not wait_ok:
        return True, fname
    deadline = time.time() + 30
    ok = False
    # 后端同名文件会加时间戳后缀（如 xxx_174728.pdf），匹配时用去扩展名的主干
    stem = os.path.splitext(fname)[0]
    while time.time() < deadline:
        items = page.locator(".uploaded-file-item .file-name").all_inner_texts()
        if any(stem in t for t in items):
            ok = True
            break
        time.sleep(0.5)
    return ok, fname


def main():
    parser = argparse.ArgumentParser(description="Co-Sight 前端浏览器自动化测试 v2")
    parser.add_argument("messages", nargs="*", help="要发送的消息（多条连续发送，同一会话）")
    parser.add_argument("--upload", action="append", default=[], help="上传文件路径（可多个），等价拖拽/选择文件")
    parser.add_argument("--headless", default="true", help="true/false 是否显示浏览器窗口")
    parser.add_argument("--url", default=URL, help="页面地址")
    parser.add_argument("--keep-open", action="store_true", help="跑完后保持浏览器打开（便于查看）")
    parser.add_argument("--shot", default="browser_test_result.png", help="截图保存路径")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(args.url, timeout=25000)
        page.wait_for_selector("#initial-message-input, #message-input", timeout=15000)
        print(f"✅ 页面已打开: {page.title()}")

        # 上传文件（等价拖拽/选择文件）
        for fp in args.upload:
            ok, fname = upload_file_to_page(page, fp)
            status = "✅ 已上传" if ok else "❌ 上传失败/未显示"
            results.append((f"[上传] {fname}", status, status))
            print(f"\n【上传】{fname} -> {status}\n{'─' * 60}")

        for msg in args.messages:
            reply, elapsed, timed_out = send_and_wait(page, msg)
            status = "⏱ 超时" if timed_out else f"⏱ {elapsed:.1f}s"
            results.append((msg, reply, status))
            print(f"\n【用户】{msg}\n【AI({status})】{reply[:600]}\n{'─' * 60}")

        page.screenshot(path=args.shot, full_page=False)
        print(f"\n📸 截图已保存: {args.shot}")

        if args.keep_open:
            print("⏸ 浏览器保持打开，按回车关闭...")
            try:
                input()
            except EOFError:
                time.sleep(30)
        browser.close()

    ok = all("⏱ 超时" not in status for _, _, status in results)
    print(f"\n=== 浏览器测试完成：{'✅ 全部成功' if ok else '⚠ 存在超时/失败'} ===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
