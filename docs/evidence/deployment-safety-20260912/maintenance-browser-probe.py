import json,time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
out=Path('/evidence');out.mkdir(exist_ok=True)
options=Options();options.binary_location='/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050']:options.add_argument(arg)
options.set_capability('goog:loggingPrefs',{'performance':'ALL'})
driver=webdriver.Chrome(service=Service('/app/chromedriver'),options=options)
try:
 driver.get('http://127.0.0.1:19000/demo/?case=CASE-2026-0008')
 wait=WebDriverWait(driver,30)
 wait.until(lambda d:d.find_element(By.CSS_SELECTOR,'.status-mini').text=='CREATED')
 driver.find_elements(By.CSS_SELECTOR,'.primary-action')[0].click()
 wait.until(lambda d:'部署维护中，暂不接受业务操作' in d.find_element(By.TAG_NAME,'body').text)
 assert driver.find_element(By.CSS_SELECTOR,'.status-mini').text=='CREATED'
 responses=[]
 for item in driver.get_log('performance'):
  message=json.loads(item['message'])['message']
  if message['method']=='Network.responseReceived':
   response=message['params']['response']
   if response['url'].endswith('/team/run'):responses.append(response['status'])
 assert responses==[503],responses
 driver.save_screenshot(str(out/'maintenance.png'))
 result={'browser':'Chromium in 202 Docker','read_only_case_visible':True,'maintenance_message_visible':True,'team_run_status':responses,'case_remained_created':True,'passed':True}
 (out/'maintenance-browser.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
finally:driver.quit()
