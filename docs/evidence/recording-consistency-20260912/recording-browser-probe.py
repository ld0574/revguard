import hashlib, json, os, time, urllib.request, urllib.error
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

root = 'http://revguard-recording-preview:9000'
case_id = 'CASE-2026-0008'
out = Path('/evidence'); out.mkdir(exist_ok=True)
phase = os.environ['PHASE']
def dashboard():
    request = urllib.request.Request(root + '/api/v1/cases/' + case_id + '/dashboard',
        headers={'Authorization': 'Bearer rg-demo-viewer-key-1'})
    with urllib.request.urlopen(request, timeout=10) as response: return json.load(response)
def has_status(expected):
    try: return dashboard()['case']['status'] == expected
    except urllib.error.HTTPError as error:
        if error.code in {409, 503}: return False
        raise
before = dashboard()
assert before['case']['status'] == 'CLOSED' and len(before['executions']) == 2
options = Options(); options.binary_location = '/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050']:
    options.add_argument(arg)
options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
driver = webdriver.Chrome(service=Service('/app/chromedriver'), options=options)
wait = WebDriverWait(driver, 45)
try:
    driver.get(root + '/demo/?case=' + case_id)
    button = wait.until(lambda d: d.find_element(By.XPATH, "//button[contains(.,'重新准备当前案件')]"))
    wait.until(lambda d: button.is_enabled())
    driver.save_screenshot(str(out / ('closed-' + phase + '.png')))
    button.click()
    result = {'phase': phase, 'closed_executions': len(before['executions'])}
    if phase == 'failure':
        wait.until(lambda d: '重新准备结果暂未确认' in d.find_element(By.TAG_NAME, 'body').text)
        after = dashboard()
        assert after == before, 'failed reprepare must preserve all visible evidence'
        result['snapshot_preserved'] = True
        result['error_visible'] = True
        driver.save_screenshot(str(out / 'reprepare-error.png'))
    else:
        wait.until(lambda d: has_status('CREATED'))
        fresh = dashboard()
        assert fresh['case'].get('recording_id') and fresh['executions'] == []
        assert fresh['report_available'] is False
        assert any(e['event'] == 'DEMO_CASE_REPREPARED' for e in fresh['audit_events'])
        result['fresh_recording_id'] = fresh['case']['recording_id']
        result['old_report_hidden'] = True
        driver.save_screenshot(str(out / 'reprepared.png'))
        start = wait.until(lambda d: d.find_element(By.XPATH, "//button[contains(.,'启动多智能体调查')]"))
        wait.until(lambda d: start.is_enabled())
        start.click()
        wait.until(lambda d: has_status('WAITING_FOR_APPROVAL'))
        wait.until(lambda d: '批准' in d.find_element(By.CLASS_NAME, 'primary-action').text)
        waiting = dashboard()
        assert waiting['report_available'] and len(waiting['agent_tasks']) == 8
        assert all(t['status'] == 'SUCCEEDED' for t in waiting['agent_tasks'])
        assert waiting['executions'] == []
        result['new_report_available'] = True
        result['real_mcp_tasks_succeeded'] = 8
        result['stopped_at_human_gate'] = True
        driver.save_screenshot(str(out / 'new-recording-human-gate.png'))
        driver.set_window_size(390,844); time.sleep(1)
        result['mobile_no_outer_overflow'] = driver.execute_script('return document.documentElement.scrollWidth <= innerWidth')
        driver.save_screenshot(str(out / 'recording-mobile.png'))
        assert result['mobile_no_outer_overflow']
    errors = [e['message'] for e in driver.get_log('browser') if e['level'] == 'SEVERE']
    if phase == 'failure': errors = [e for e in errors if '503' not in e]
    assert not errors, errors
    result['console_errors'] = errors
    result['passed'] = True
    (out / ('browser-' + phase + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
finally:
    driver.quit()
