"""GitHub Pages 运行回放页验收：内容、交互、材料直链、移动端与资源路径。

在 202 的 `revguard-grafana-browser` 浏览器容器内执行，服务端由独立 Nginx
容器以 `/revguard/` 前缀提供静态文件，验证的是与 GitHub Pages 项目站点相同的
子路径加载方式。

案件清单不写死在脚本里：脚本先读 `data/index.json`，再逐个切换标签，核对页面
渲染的步骤数、阶段数与追踪行数是否与数据包一致。
"""

import json
import os
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = os.environ.get("SITE_URL", "http://10.10.10.202:18099/revguard/")
out = Path("/evidence")
out.mkdir(exist_ok=True)
result = {"site_url": BASE, "cases": [], "checks": {}, "failed_requests": [], "console": []}

with urllib.request.urlopen(BASE + "data/index.json", timeout=20) as response:
    index = json.load(response)
result["release"] = index.get("release")
result["cases"] = [item["case_id"] for item in index["cases"]]

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

    # 材料入口：两个决赛成片必须给出 Release 附件直链，且真的能下载
    material_links = [
        element.get_attribute("href")
        for element in driver.find_elements(By.CSS_SELECTOR, "#materials a[href$='.mp4']")
    ]
    record("材料入口含两个成片直链", len(material_links) == 2, f"links={material_links}")
    reachable = []
    for link in material_links:
        request = urllib.request.Request(link, method="HEAD", headers={"User-Agent": "revguard-website-probe"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                reachable.append([link.rsplit("/", 1)[-1], response.status])
        except Exception as exc:  # noqa: BLE001 - 探针把下载失败转成检查结果
            reachable.append([link.rsplit("/", 1)[-1], f"error:{type(exc).__name__}"])
    record("成片直链可下载", all(status == 200 for _, status in reachable), f"reachable={reachable}")

    driver.get(BASE + "replay.html")
    WebDriverWait(driver, 20).until(lambda d: "STEP" in d.find_element(By.ID, "step-card").text)
    time.sleep(1)
    tabs = driver.find_elements(By.CSS_SELECTOR, ".case-tab")
    record("回放标签与索引一致", len(tabs) == len(index["cases"]),
           f"tabs={len(tabs)} index={len(index['cases'])}")

    for item in index["cases"]:
        if item is not index["cases"][0]:
            driver.find_element(By.CSS_SELECTOR, f".case-tab[data-file='{item['file']}']").click()
        WebDriverWait(driver, 25).until(
            lambda d, steps=str(item["steps"]): d.find_element(By.ID, "step-total").text == steps
        )
        facts = len(driver.find_elements(By.CSS_SELECTOR, "#facts .fact"))
        stages = len(driver.find_elements(By.CSS_SELECTOR, "#stages .stage"))
        total = driver.find_element(By.ID, "step-total").text
        trace_rows = len(driver.find_elements(By.CSS_SELECTOR, "#trace-table tbody tr"))
        release = driver.find_element(By.ID, "release-label").text
        record(
            f"{item['case_id']} 渲染",
            facts == 8 and stages == item["steps"] and total == str(item["steps"]) and trace_rows == item["spans"],
            f"facts={facts} stages={stages} steps={total} trace={trace_rows} release={release}",
        )
        driver.save_screenshot(str(out / f"replay-{item['case_id'].lower()}.png"))

    driver.find_element(By.ID, "btn-play").click()
    time.sleep(9)
    step_index = int(driver.find_element(By.ID, "step-index").text)
    playing_label = driver.find_element(By.ID, "btn-play").text
    record("播放可推进", step_index > 1, f"step_index={step_index} button={playing_label}")
    driver.find_element(By.ID, "btn-play").click()
    driver.save_screenshot(str(out / "replay-playing.png"))

    driver.set_window_size(390, 844)
    time.sleep(2)
    overflow = driver.execute_script("return document.documentElement.scrollWidth > window.innerWidth + 1")
    record("移动端无横向溢出", not overflow, f"overflow={overflow}")
    driver.save_screenshot(str(out / "replay-mobile.png"))
    driver.set_window_size(1600, 1050)

    collect_network()
    record("无失败请求", not result["failed_requests"], f"{result['failed_requests']}")
    result["console"] = [entry for entry in driver.get_log("browser") if entry["level"] == "SEVERE"]
    record("无浏览器错误", not result["console"], f"{result['console']}")
finally:
    driver.quit()

result["passed"] = all(check["passed"] for check in result["checks"].values())
(out / "browser-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["passed"] else 1)
