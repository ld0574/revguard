"""Record the finals demo walkthrough frame-by-frame inside the 202 Docker lab.

Runs headless Chromium at a true 1920x1080 viewport in
`revguard-recorder:20260918` on 10.10.10.202, walks the scenes of
`submission/决赛视频分镜脚本-v0.6.0-rc3.md`, and writes numbered frames plus a
scene log and per-frame timestamps.  The frames are encoded to mp4 by
`scripts/encode_finals_walkthrough.py` (ffmpeg) in the same container image.

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
CASE1 = os.environ.get("CASE1", "CASE-2026-0001")
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
    ticks = max(1, int(seconds * FPS))
    for _ in range(ticks):
        grab(driver)
        time.sleep(max(0.0, 1.0 / FPS - 0.01))


def glide(driver, pixels: float, seconds: float) -> None:
    ticks = max(1, int(seconds * FPS))
    step = pixels / ticks
    for _ in range(ticks):
        driver.execute_script(f"window.scrollBy(0, {step});")
        grab(driver)
        time.sleep(max(0.0, 1.0 / FPS - 0.01))


def click_text(driver, label: str) -> bool:
    """Click a tab by label, scrolling it into view first to avoid interception."""
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
    """Pick the case explicitly from the WebUI dropdown (the URL param is only a hint)."""
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
    # 1. 官网首屏与两个案例卡片（20s）
    note("site-home", "官网首屏")
    open_page(driver, SITE, wait=4)
    hold(driver, 6)
    glide(driver, 900, 9)
    hold(driver, 5)

    # 2. 现场栈：CASE-2026-0001 概览（30s）
    note("demo-case1", "演示栈案件概览")
    open_page(driver, f"{DEMO}?case={CASE1}", wait=6)
    select_case(driver, CASE1)
    hold(driver, 9)
    glide(driver, 700, 9)
    hold(driver, 6)
    driver.execute_script("window.scrollTo(0, 0);")
    hold(driver, 6)

    # 3. 决策依据：证据链 → 政策时间线 → 复算账本（50s）
    note("decision-tab", "决策依据")
    click_text(driver, "决策依据")
    hold(driver, 10)
    glide(driver, 800, 12)
    hold(driver, 8)
    glide(driver, 900, 12)
    hold(driver, 8)

    # 4. 执行与审计：审批承诺 → 幂等键 → 写入快照（40s）
    note("audit-tab", "执行与审计")
    driver.execute_script("window.scrollTo(0, 0);")
    click_text(driver, "执行与审计")
    hold(driver, 8)
    glide(driver, 700, 8)
    hold(driver, 8)
    glide(driver, 700, 8)
    hold(driver, 8)

    # 5. 独立复核结果（30s）
    note("verify", "独立验证卡片")
    glide(driver, 800, 10)
    hold(driver, 16)

    # 6. CASE-2026-0008：偏差与冲销（30s）
    note("demo-case8", "错误恢复案件")
    open_page(driver, f"{DEMO}?case={CASE8}", wait=6)
    select_case(driver, CASE8)
    click_text(driver, "执行与审计")
    hold(driver, 8)
    glide(driver, 900, 10)
    hold(driver, 12)

    # 7. 可观测大屏（25s）
    note("observability", "只读大屏")
    click_text(driver, "可观测大屏")
    hold(driver, 24)
    glide(driver, 400, 5)

    # 8. 公网运行回放（15s）
    note("replay", "公网回放页")
    open_page(driver, f"{SITE}replay.html", wait=6)
    hold(driver, 5)
    try:
        driver.find_element(By.ID, "btn-play").click()
    except WebDriverException as exc:
        note("replay-play-failed", str(exc)[:120])
    hold(driver, 4)
finally:
    driver.quit()

summary = {
    "scenes": log,
    "frames": frame,
    "recorded_seconds": round(counter["t"], 1),
    "fps": FPS,
    "viewport": viewport,
}
(OUT / "record-log.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(OUT / "frame-times.json").write_text(json.dumps(frame_times) + "\n", encoding="utf-8")
print(json.dumps({"frames": frame, "recorded_seconds": round(counter["t"], 1)}, ensure_ascii=False))
