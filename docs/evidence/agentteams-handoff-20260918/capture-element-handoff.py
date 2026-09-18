"""Capture AgentTeams orchestration + worker rooms from Element Web (real run history)."""
import json
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = os.environ.get("ELEMENT_URL", "http://10.10.10.202:8088/")
ROOM = os.environ["ELEMENT_ROOM_ID"]
WORKER_ROOM = os.environ.get("ELEMENT_WORKER_ROOM_ID", "")
out = Path("/evidence")
out.mkdir(exist_ok=True)
creds = json.loads(Path("/creds.json").read_text(encoding="utf-8"))

options = Options()
options.binary_location = "/usr/bin/chromium"
for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1680,1050"):
    options.add_argument(arg)
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
result: dict = {"base": BASE, "room": ROOM}


def dismiss_overlays() -> None:
    """Close the service-worker dialog and the device-verification banner."""
    for text in ("OK", "Dismiss", "Can't confirm?", "Skip", "Later", "Continue", "Verify later"):
        try:
            driver.find_element(By.XPATH, f"//*[self::button or self::a][normalize-space()='{text}']").click()
            time.sleep(1.5)
        except Exception:  # noqa: BLE001
            continue
    driver.execute_script(
        """
        document.querySelectorAll('[role=dialog], .mx_Dialog, .mx_Toast, [role=alert]').forEach((n) => n.remove());
        document.querySelectorAll('div, aside, section').forEach((n) => {
            if (n.textContent.includes('Enable desktop notifications')
                && n.getBoundingClientRect().width < 520
                && n.getBoundingClientRect().top < 200) {
                n.remove();
            }
        });
        """
    )


try:
    driver.get(BASE)
    time.sleep(6)
    driver.find_element(By.CSS_SELECTOR, "a[href='#/login']").click()
    time.sleep(4)
    user = driver.find_element(By.CSS_SELECTOR, "#mx_LoginForm_username, input[name='username']")
    pwd = driver.find_element(By.CSS_SELECTOR, "#mx_LoginForm_password, input[name='password']")
    user.send_keys(creds["user"])
    pwd.send_keys(creds["password"])
    submit = None
    for selector in (
        "#mx_LoginForm_submit", "button[type='submit']",
        "[data-testid='loginButton']", "input[type='submit']",
    ):
        try:
            submit = driver.find_element(By.CSS_SELECTOR, selector)
            break
        except Exception:  # noqa: BLE001
            continue
    if submit is not None:
        submit.click()
    else:
        pwd.send_keys("\ue007")
    time.sleep(14)
    dismiss_overlays()
    driver.get(f"{BASE}#/room/{ROOM}")
    WebDriverWait(driver, 90).until(
        lambda d: "REVGUARD_STAGE_HANDOFF" in d.find_element(By.TAG_NAME, "body").text
    )
    time.sleep(4)
    dismiss_overlays()
    time.sleep(2)
    body_text = driver.find_element(By.TAG_NAME, "body").text
    result["handoff_mentions"] = body_text.count("REVGUARD_STAGE_HANDOFF")
    result["room_text_head"] = body_text[:900]
    driver.execute_script(
        "document.querySelectorAll('.mx_RoomView_MessageList').forEach((n) => n.scrollIntoView())"
    )
    driver.save_screenshot(str(out / "element-orchestration-room.png"))

    tiles = driver.find_elements(
        By.XPATH, "//li[.//*[contains(text(),'REVGUARD_STAGE_HANDOFF')]]"
    )
    result["handoff_tiles"] = len(tiles)
    if tiles:
        try:
            tiles[-2 if len(tiles) > 1 else -1].screenshot(
                str(out / "element-handoff-tile.png")
            )
            result["tile_shot"] = True
        except Exception as exc:  # noqa: BLE001
            result["tile_shot"] = f"{type(exc).__name__}: {exc}"
    if WORKER_ROOM:
        driver.get(f"{BASE}#/room/{WORKER_ROOM}")
        time.sleep(10)
        dismiss_overlays()
        time.sleep(2)
        worker_text = driver.find_element(By.TAG_NAME, "body").text
        result["worker_text_head"] = worker_text[:700]
        result["worker_has_skill_receipt"] = "skill_receipt" in worker_text
        driver.save_screenshot(str(out / "element-worker-room.png"))
finally:
    driver.quit()
(out / "element-result.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
)
print(json.dumps({k: v for k, v in result.items() if k != "room_text_head"}, ensure_ascii=False)[:1200])
