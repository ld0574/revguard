"""GitHub Pages 运行回放页验收：内容、交互、移动端与资源路径。

在 202 的 `revguard-grafana-browser` 浏览器容器内执行，服务端由独立 Nginx
容器以 `/revguard/` 前缀提供静态文件，验证的是与 GitHub Pages 项目站点相同的
子路径加载方式。
"""

import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = os.environ.get("SITE_URL", "http://10.10.10.202:18099/revguard/")
out = Path("/evidence")
out.mkdir(exist_ok=True)
result = {"site_url": BASE, "checks": {}, "failed_requests": [], "console": []}

options = Options()
options.binary_location = "/usr/bin/chromium"
for arg in ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1050"]:
    options.add_argument(arg)
options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)


def record(name, passed, detail=""):
    result["checks"][name] = {"passed": bool(passed), "detail": detail}
    return passed


def collect_network():
    for entry in driver.get_log("performance"):
        message = json.loads(entry["message"])["message"]
        if message["method"] == "Network.responseReceived":
            response = message["params"]["response"]
            if response["status"] >= 400:
                result["failed_requests"].append({"status": response["status"], "url": response["url"]})


try:
    driver.get(BASE)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.CSS_SELECTOR, "a[href='replay.html']"))
    record("首页含回放入口", True)
    driver.save_screenshot(str(out / "site-home.png"))

    driver.get(BASE + "replay.html")
    WebDriverWait(driver, 20).until(lambda d: "STEP" in d.find_element(By.ID, "step-card").text)
    time.sleep(1)
    facts = len(driver.find_elements(By.CSS_SELECTOR, "#facts .fact"))
    stages = len(driver.find_elements(By.CSS_SELECTOR, "#stages .stage"))
    total = driver.find_element(By.ID, "step-total").text
    trace_rows = len(driver.find_elements(By.CSS_SELECTOR, "#trace-table tbody tr"))
    record("CASE-0001 摘要与阶段渲染", facts == 8 and stages == 10 and total == "10",
           f"facts={facts} stages={stages} steps={total}")
    record("运行追踪表渲染", trace_rows == 35, f"rows={trace_rows}")
    driver.save_screenshot(str(out / "replay-case-0001.png"))

    driver.find_element(By.CSS_SELECTOR, ".case-tab[data-case='case-2026-0008']").click()
    WebDriverWait(driver, 20).until(lambda d: d.find_element(By.ID, "step-total").text == "11")
    stages8 = len(driver.find_elements(By.CSS_SELECTOR, "#stages .stage"))
    record("切换到 CASE-0008", stages8 == 11, f"stages={stages8}")
    driver.save_screenshot(str(out / "replay-case-0008.png"))

    driver.find_element(By.ID, "btn-play").click()
    time.sleep(9)
    index = int(driver.find_element(By.ID, "step-index").text)
    playing_label = driver.find_element(By.ID, "btn-play").text
    record("播放可推进并暂停", index > 1, f"step_index={index} button={playing_label}")
    driver.find_element(By.ID, "btn-play").click()
    driver.save_screenshot(str(out / "replay-playing.png"))

    driver.set_window_size(390, 844)
    time.sleep(2)
    overflow = driver.execute_script("return document.documentElement.scrollWidth > window.innerWidth + 1")
    record("移动端无横向溢出", not overflow, f"overflow={overflow}")
    driver.save_screenshot(str(out / "replay-mobile.png"))

    driver.set_window_size(1600, 1050)
    collect_network()
    result["console"] = [entry["message"] for entry in driver.get_log("browser") if entry["level"] == "SEVERE"]
    record("无失败请求", not result["failed_requests"], json.dumps(result["failed_requests"])[:200])
    record("无浏览器错误", not result["console"], json.dumps(result["console"])[:200])
finally:
    driver.quit()

result["passed"] = all(item["passed"] for item in result["checks"].values())
(out / "browser-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["passed"] else 1)
