"""Read-only visual check after the recovery UI's next polling update."""
import json
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
out=Path('/evidence');options=Options();options.binary_location='/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050']:options.add_argument(arg)
options.set_capability('goog:loggingPrefs',{'browser':'ALL'})
driver=webdriver.Chrome(service=Service('/app/chromedriver'),options=options)
try:
    driver.get('http://revguard-recovery-preview:9000/demo/?case=CASE-2026-0008')
    WebDriverWait(driver,30).until(lambda d:d.find_element(By.CSS_SELECTOR,'.status-mini').text=='CLOSED')
    assert driver.find_element(By.CSS_SELECTOR,'.result-note strong').text=='CLOSED'
    driver.save_screenshot(str(out/'success-closed.png'))
    driver.set_window_size(390,844)
    WebDriverWait(driver,15).until(lambda d:d.execute_script('return document.documentElement.scrollWidth <= innerWidth'))
    driver.save_screenshot(str(out/'success-mobile.png'))
    errors=[e['message'] for e in driver.get_log('browser') if e['level']=='SEVERE'];assert not errors,errors
    result={'visible_status':'CLOSED','mobile_no_outer_overflow':True,'console_errors':errors,'passed':True}
    (out/'closed-view.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
finally:driver.quit()
