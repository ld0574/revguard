import json,time,re,os,urllib.request,urllib.error
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

out=Path('/evidence');out.mkdir(exist_ok=True)
demo_url=os.environ.get('DEMO_URL','http://revguard-grafana-api-preview:9000/demo/?view=observability')
parsed=urlsplit(demo_url)
health_url=urlunsplit((parsed.scheme,parsed.netloc,'/api/v1/health','',''))
for attempt in range(30):
    try:
        with urllib.request.urlopen(health_url,timeout=3) as response:
            if json.load(response).get('ready'):break
    except (urllib.error.URLError,TimeoutError):pass
    time.sleep(1)
else:raise SystemExit('API did not become ready before browser verification')
options=Options();options.binary_location='/usr/bin/chromium'
for arg in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1600,1050','--screen-info={1600x1050}']:options.add_argument(arg)
options.set_capability('goog:loggingPrefs',{'browser':'ALL','performance':'ALL'})
driver=webdriver.Chrome(service=Service('/app/chromedriver'),options=options)
def safe(value):return re.sub(r'[a-f0-9]{32}', '<dashboard>',str(value))
try:
    driver.get(demo_url)
    WebDriverWait(driver,30).until(lambda d:d.find_elements(By.TAG_NAME,'iframe') or '暂时不可用' in d.find_element(By.TAG_NAME,'body').text)
    time.sleep(8)
    frames=driver.find_elements(By.TAG_NAME,'iframe')
    result={'iframe_count':len(frames),'outer_text':driver.find_element(By.TAG_NAME,'body').text,'screen':driver.execute_script('return {width:screen.width,height:screen.height}')}
    if frames:
        driver.switch_to.frame(frames[0]);time.sleep(8)
        result['frame_text']=driver.find_element(By.TAG_NAME,'body').text
        result['frame_url']=safe(driver.execute_script('return location.href'))
        driver.switch_to.default_content()
    unavailable=os.environ.get('EXPECT_UNAVAILABLE') == 'true'
    driver.save_screenshot(str(out/('grafana-unavailable.png' if unavailable else 'grafana-embedded.png')))
    if frames and os.environ.get('CHECK_LAYOUT') == 'true':
        driver.find_element(By.XPATH,"//button[contains(.,'全屏')]").click()
        time.sleep(2)
        result['fullscreen']=driver.execute_script('return Boolean(document.fullscreenElement)')
        driver.save_screenshot(str(out/'grafana-fullscreen.png'))
        if result['fullscreen']:driver.execute_script('document.exitFullscreen()')
        driver.set_window_size(390,844);time.sleep(2)
        result['mobile_no_outer_overflow']=driver.execute_script('return document.documentElement.scrollWidth <= innerWidth')
        driver.save_screenshot(str(out/'grafana-mobile.png'))
    result['console']=[safe(x['message']) for x in driver.get_log('browser') if x['level']=='SEVERE']
    failed=[];queries=[];query_bodies=[]
    for entry in driver.get_log('performance'):
        msg=json.loads(entry['message'])['message'];p=msg.get('params',{})
        if msg['method']=='Network.requestWillBeSent' and '/panels/' in p['request']['url'] and len(query_bodies)<1:
            query_bodies.append(p['request'].get('postData'))
        if msg['method']=='Network.responseReceived':
            response=p['response'];url=response['url']
            if response['status']>=400:failed.append({'status':response['status'],'url':safe(url)})
            if '/panels/' in url and '/query' in url:queries.append({'status':response['status'],'url':safe(url)})
    result['failed_requests']=failed;result['panel_queries']=queries;result['query_bodies']=query_bodies
    result['unique_panel_ids']=sorted({int(re.search(r'/panels/(\d+)/query',x['url'])[1]) for x in queries})
    result['passed']=len(frames)==1 and result['unique_panel_ids']==list(range(1,13)) and all(x['status']==200 for x in queries) and result.get('frame_text','').count('正常')>=3 and not failed and not result['console']
    if os.environ.get('CHECK_LAYOUT') == 'true':result['passed']=result['passed'] and result.get('fullscreen',False) and result.get('mobile_no_outer_overflow',False)
    if unavailable:result['passed']=len(frames)==0 and 'Grafana 暂时不可用' in result['outer_text']
    (out/('browser-unavailable.json' if unavailable else 'browser-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    if not result['passed']:raise SystemExit(1)
finally:driver.quit()
