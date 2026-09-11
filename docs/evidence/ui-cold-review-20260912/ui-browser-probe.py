import json,os,time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
out=Path('/evidence');out.mkdir(exist_ok=True)
root=os.environ.get('DEMO_URL','http://revguard-ui-preview:9000/demo/')
phase=os.environ.get('PHASE','pending')
options=Options();options.binary_location='/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050']:options.add_argument(arg)
options.set_capability('goog:loggingPrefs',{'browser':'ALL'})
driver=webdriver.Chrome(service=Service('/app/chromedriver'),options=options)
wait=WebDriverWait(driver,30);results=[]
try:
 cases=[('0003','CREATED','neutral'),('0001','WAITING_FOR_APPROVAL','warning')] if phase=='pending' else [('0001','CLOSED','success'),('0002','CLOSED','neutral'),('0008','ROLLED_BACK','success')]
 if phase=='production':cases=[('0008','ROLLED_BACK','success')]
 for suffix,status,tone in cases:
  driver.set_window_size(1600,1050)
  cid='CASE-2026-'+suffix;driver.get(root+'?case='+cid)
  wait.until(lambda d:d.find_element(By.CSS_SELECTOR,'.status-mini').text==status)
  state=driver.find_element(By.CSS_SELECTOR,'.summary-6')
  assert 'outcome-'+tone in state.get_attribute('class'), state.text
  assert 'AgentTeams 已连接' not in driver.find_element(By.TAG_NAME,'body').text
  for width in [1600,1180,760,390]:
   driver.set_window_size(width,1050 if width>760 else 844);time.sleep(.25)
   measured=driver.execute_script('const e=document.querySelector(".disclosure"),r=e.getBoundingClientRect();return {shown: getComputedStyle(e).display!=="none" && r.width>0 && r.height>0 && r.top>=0 && r.bottom<=innerHeight, overflow:document.documentElement.scrollWidth>innerWidth,text:e.textContent};')
   assert measured['shown'] and not measured['overflow'], (cid,width,measured)
   driver.execute_script('window.scrollTo(0,document.body.scrollHeight)');time.sleep(.1)
   assert driver.execute_script('const r=document.querySelector(".disclosure").getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight;')
   driver.execute_script('window.scrollTo({top:0,behavior:"instant"})')
   if width in [1600,390]:driver.save_screenshot(str(out/(phase+'-'+suffix+'-'+str(width)+'.png')))
  results.append({'case_id':cid,'status':status,'tone':tone,'disclosure_visible_and_no_overflow':[1600,1180,760,390]})
 if phase=='final':
  driver.set_window_size(1600,1050)
  driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':'''const realFetch=window.fetch;window.fetch=async (...args)=>{const response=await realFetch(...args);if(String(args[0]).includes('CASE-2026-0001/dashboard'))await new Promise(r=>setTimeout(r,2200));return response;};'''})
  driver.get(root+'?case=CASE-2026-0001')
  wait.until(lambda d:len(d.find_elements(By.CSS_SELECTOR,'.case-select option'))==8)
  Select(driver.find_element(By.CSS_SELECTOR,'.case-select')).select_by_value('CASE-2026-0008')
  wait.until(lambda d:d.find_element(By.CSS_SELECTOR,'.summary-6 strong').text=='ROLLED_BACK')
  time.sleep(3)
  assert driver.find_element(By.CSS_SELECTOR,'.summary-6 strong').text=='ROLLED_BACK'
  assert 'CASE-2026-0008' in driver.find_element(By.CSS_SELECTOR,'.safety-rail').text
  driver.save_screenshot(str(out/'case-switch-late-response.png'))
  results.append({'late_previous_case_response_discarded':True})
  driver.execute_script('window.fetch=()=>Promise.reject(new TypeError("isolated browser transport failure"))')
  Select(driver.find_element(By.CSS_SELECTOR,'.case-select')).select_by_value('CASE-2026-0003')
  wait.until(lambda d:'无法连接 RevGuard API' in d.find_element(By.TAG_NAME,'body').text)
  assert '已连接' not in driver.find_element(By.CSS_SELECTOR,'.topbar').text
  driver.save_screenshot(str(out/'api-unavailable.png'))
  results.append({'simulated_browser_transport_failure_visible':True})
 errors=[e['message'] for e in driver.get_log('browser') if e['level']=='SEVERE'];assert not errors,errors
 result={'phase':phase,'browser':'Chromium in 202 Docker','results':results,'console_errors':errors,'passed':True}
 (out/('browser-'+phase+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(result,ensure_ascii=False))
finally:driver.quit()
