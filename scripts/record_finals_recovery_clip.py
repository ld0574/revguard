"""Record the 90-second fault-recovery standby clip for the finals defence.

Read-only walkthrough of the real CASE-2026-0008 recovered run: deviation,
authoritative re-query, two reversals, net-zero verification and the Grafana
money-recovery panels.  Rendered at a true 1920x1080 viewport in
`revguard-recorder:20260918` on 10.10.10.202 and encoded by
`scripts/encode_finals_walkthrough.py`.

Evidence: docs/evidence/finals-media-20260918/.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

SITE = os.environ.get("SITE_URL", "https://ld0574.github.io/revguard/")
DEMO = os.environ.get("DEMO_URL", "http://10.10.10.202:19088/demo/")
CASE8 = os.environ.get("CASE8", "CASE-2026-0008")
FPS = float(os.environ.get("FPS", "4"))
OUT = Path(os.environ.get("FRAME_DIR", "/frames"))
OUT.mkdir(parents=True, exist_ok=True)

log: list[dict] = []
frame = 0
frame_times: list[float] = []
counter = {"t": 0.0}
last = {"t": time.time()}


def note(scene: str, detail: str = "") -> None:
    log.append({"scene": scene, "at_seconds": round(counter["t"], 1), "frame": frame, "detail": detail})
    print(f"[{counter['t']:6.1f}s] {scene} {detail}", flush=True)


def grab(driver) -> None:
    global frame
    frame += 1
    driver.save_screenshot(str(OUT / f"frame-{frame:06d}.png"))
    now = time.time()
    counter["t"] += now - last["t"]
    last["t"] = now
    frame_times.append(round(counter["t"], 3))


def hold(driver, seconds: float) -> None:
    for _ in range(max(1, int(seconds * FPS))):
        grab(driver)
        time.sleep(max(0.0, 1.0 / FPS - 0.01))


def glide(driver, pixels: float, seconds: float) -> None:
    ticks = max(1, int(seconds * FPS))
    for _ in range(ticks):
        driver.execute_script(f"window.scrollBy(0, {pixels / ticks});")
        grab(driver)
        time.sleep(max(0.0, 1.0 / FPS - 0.01))


def click_text(driver, label: str) -> bool:
    try:
        element = driver.find_element(By.XPATH, f"//button[normalize-space()='{label}']")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.4)
        try:
            element.click()
        except WebDriverException:
            driver.execute_script("arguments[0].click();", element)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.6)
        return True
    except WebDriverException as exc:
        note(f"click-failed:{label}", str(exc)[:120])
        return False


def open_page(driver, url: str, wait: float = 3.0) -> None:
    driver.get(url)
    time.sleep(wait)


def select_case(driver, case_id: str) -> None:
    try:
        element = driver.find_element(By.CSS_SELECTOR, "select.case-select")
        Select(element).select_by_value(case_id)
        note("case-selected", case_id)
        time.sleep(4)
    except WebDriverException as exc:
        note(f"case-select-failed:{case_id}", str(exc)[:120])


options = Options()
options.binary_location = "/usr/bin/chromium"
for arg in [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
    "--window-size=1920,1223", "--hide-scrollbars=false",
    "--force-device-scale-factor=1", "--disable-features=Translate",
]:
    options.add_argument(arg)
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
driver.set_window_size(1920, 1223)
viewport = driver.execute_script("return [window.innerWidth, window.innerHeight]")
print(f"viewport={viewport}", flush=True)
last["t"] = time.time()

try:
    # 1. 故障案件概览：ROLLED_BACK + 审批单（12s）
    note("case8-overview", "错误恢复案件终态")
    open_page(driver, f"{DEMO}?case={CASE8}", wait=6)
    select_case(driver, CASE8)
    hold(driver, 8)
    glide(driver, 500, 5)

    # 2. 执行与审计：写后偏差 → 冻结自动重试 → 权威查询 → 冲销（45s）
    note("case8-audit", "偏差、冻结与冲销记录")
    driver.execute_script("window.scrollTo(0, 0);")
    click_text(driver, "执行与审计")
    hold(driver, 10)
    glide(driver, 600, 8)
    hold(driver, 8)
    glide(driver, 600, 8)
    hold(driver, 12)

    # 3. 可观测大屏：资金操作与恢复面板（22s）
    note("observability", "资金恢复与运行监控")
    click_text(driver, "可观测大屏")
    hold(driver, 20)

    # 4. 公网回放：case8 时间线（12s）
    note("replay", "公网运行回放")
    open_page(driver, f"{SITE}replay.html?case=CASE-2026-0008", wait=6)
    hold(driver, 4)
    try:
        driver.find_element(By.ID, "btn-play").click()
    except WebDriverException as exc:
        note("replay-play-failed", str(exc)[:120])
    hold(driver, 6)
finally:
    driver.quit()

summary = {
    "scenes": log, "frames": frame, "recorded_seconds": round(counter["t"], 1),
    "fps": FPS, "viewport": viewport,
}
(OUT / "record-log.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(OUT / "frame-times.json").write_text(json.dumps(frame_times) + "\n", encoding="utf-8")
print(json.dumps({"frames": frame, "recorded_seconds": round(counter["t"], 1)}, ensure_ascii=False))
