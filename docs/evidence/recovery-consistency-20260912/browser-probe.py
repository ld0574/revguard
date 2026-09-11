"""Browser contract uses a test Matrix identity fixture and the real RevGuard API/MCP path."""
import json, os, time, urllib.request
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
root = 'http://revguard-recovery-preview:9000'
case_id = 'CASE-2026-0008'
phase = os.environ['PHASE']
out = Path('/evidence'); out.mkdir(exist_ok=True)
def dashboard():
    request = urllib.request.Request(root + '/api/v1/cases/' + case_id + '/dashboard',
        headers={'Authorization':'Bearer rg-demo-viewer-key-1'})
    with urllib.request.urlopen(request, timeout=10) as response: return json.load(response)
options = Options(); options.binary_location = '/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050']: options.add_argument(arg)
options.set_capability('goog:loggingPrefs', {'browser':'ALL'})
driver = webdriver.Chrome(service=Service('/app/chromedriver'), options=options)
wait = WebDriverWait(driver, 60)
try:
    before = dashboard()
    driver.get(root + '/demo/?case=' + case_id)
    button = wait.until(lambda d: d.find_element(By.XPATH, "//button[contains(.,'核对并恢复')]"))
    assert before['case']['status'] == 'RECOVERY_REQUIRED'
    assert not before['executions']
    assert sum(t['status']=='RUNNING' for t in before['agent_tasks'])==1
    driver.save_screenshot(str(out/(phase+'-entry.png')))
    button.click()
    wait.until(lambda d: d.find_element(By.CLASS_NAME, 'human-login-form'))
    driver.find_element(By.CSS_SELECTOR, 'input[autocomplete="username"]').send_keys('finance')
    driver.find_element(By.CSS_SELECTOR, 'input[type="password"]').send_keys('isolated-browser-only')
    driver.find_element(By.CSS_SELECTOR, '.human-login-form button[type="submit"]').click()
    wait.until(lambda d: d.find_element(By.CLASS_NAME, 'human-proof-panel'))
    driver.find_element(By.CSS_SELECTOR, '.human-proof-panel .human-primary').click()
    result = {'phase':phase, 'identity_provider':'isolated Matrix contract fixture',
              'workflow':'real RevGuard API and MCP reference execution'}
    if phase == 'failure':
        wait.until(lambda d: '恢复提交结果未确认' in d.find_element(By.TAG_NAME,'body').text)
        after = dashboard()
        assert after['case'] == before['case']
        assert after['approval']['status'] == 'APPROVED'
        assert after['agent_tasks'] == before['agent_tasks']
        assert after['executions'] == []
        assert driver.find_element(By.CSS_SELECTOR,'input[type="password"]').get_attribute('value') == ''
        result.update(error_visible=True, case_preserved=True, old_task_preserved=True, no_money_effect=True)
        driver.save_screenshot(str(out/'recovery-failure.png'))
    else:
        wait.until(lambda d: dashboard()['case']['status'] == 'CLOSED')
        wait.until(lambda d: not d.find_elements(By.CLASS_NAME,'human-modal'))
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR,'.status-mini').text == 'CLOSED')
        after = dashboard()
        assert len(after['executions']) == 2
        assert after['approval']['status'] == 'APPROVED'
        assert len(after['agent_tasks']) == 17
        assert after['case']['team_run']['status'] == 'COMPLETED'
        assert sum(t['status'] == 'SUCCEEDED' for t in after['agent_tasks']) == 16
        assert sum(t['status'] == 'CANCELLED' for t in after['agent_tasks']) == 1
        result.update(final_status='CLOSED', executions=2, real_mcp_tasks=16, old_tasks_cancelled=1)
        driver.save_screenshot(str(out/(phase+'-closed.png')))
        driver.set_window_size(390,844); time.sleep(1)
        result['mobile_no_outer_overflow'] = driver.execute_script('return document.documentElement.scrollWidth <= innerWidth')
        assert result['mobile_no_outer_overflow']
        driver.save_screenshot(str(out/(phase+'-mobile.png')))
    errors = [entry['message'] for entry in driver.get_log('browser') if entry['level']=='SEVERE']
    errors = [e for e in errors if not (phase=='failure' and '503' in e)]
    assert not errors, errors
    result.update(console_errors=errors, passed=True)
    (out/('browser-'+phase+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
finally: driver.quit()
