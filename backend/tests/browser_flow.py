"""Invoked by the opt-in MySQL browser test; no mocked HTTP responses."""

import json
import sys

from playwright.sync_api import expect, sync_playwright

config = json.load(sys.stdin)
with sync_playwright() as p:
    options = {"channel": "chrome"} if config.get("chrome") else {}
    browser = p.chromium.launch(**options)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def login(username, path):
        page.goto(config["url"] + "/login?redirect=" + path)
        page.get_by_label("用户名").fill(username)
        page.get_by_label("密码", exact=True).fill("test-only-password-123")
        page.get_by_role("button", name="登录", exact=True).click()
        page.wait_for_url("**" + path)

    login("admin", "/admin/m1")
    page.get_by_label("开始日期").fill(config["day"])
    page.get_by_label("结束日期").fill(config["day"])
    page.get_by_role("button", name="查询", exact=True).click()
    expect(page.get_by_text("预约数", exact=True)).to_be_visible()
    page.get_by_label("签到码教室", exact=True).select_option(str(config["room"]))
    page.get_by_role("button", name="读取签到码", exact=True).click()
    code = page.locator(".code-result .el-alert__title").inner_text().split("：")[1].split(" · ")[0]
    page.get_by_role("button", name="退出", exact=True).click()
    login("student", "/m1/reservations")
    page.get_by_label("日期", exact=True).fill(config["day"])
    page.get_by_role("button", name="查找座位").click()
    page.get_by_role("button", name="预约", exact=True).first.click()
    page.get_by_role("button", name="确定", exact=True).click()
    expect(page.get_by_role("heading", name="预约详情", exact=False)).to_be_visible()
    first_url = page.url
    page.reload()
    expect(page.get_by_role("heading", name="预约详情", exact=False)).to_be_visible()
    page.get_by_role("button", name="取消预约", exact=True).click()
    page.get_by_role("button", name="确定", exact=True).click()
    expect(page.locator(".detail")).to_contain_text("已取消")
    page.locator(".detail").get_by_role("button", name="复用座位").click()
    page.get_by_label("日期", exact=True).fill(config["day"])
    page.get_by_role("button", name="查找座位").click()
    page.get_by_role("button", name="预约", exact=True).first.click()
    page.get_by_role("button", name="确定", exact=True).click()
    page.wait_for_url(lambda url: str(url) != first_url and "/reservations/" in str(url))
    page.get_by_label("签到码", exact=True).fill(code)
    page.get_by_role("button", name="签到", exact=True).click()
    expect(page.locator(".detail")).to_contain_text("使用中")
    page.goto(config["url"] + "/admin/m1")
    page.wait_for_url("**/resources")
    assert not errors, errors
    browser.close()
print("真实浏览器：管理统计/编码、学生预约/刷新/取消/复用/签到/越权拒绝通过")
