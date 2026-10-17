"""Browser regression checks with isolated API/SSE fixtures; never writes real accounts."""
import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright
root=Path(os.environ.get('ARTIFACT_DIR', '/artifacts'));root.mkdir(exist_ok=True)
state={'at':'2026-10-15T12:00:00Z','platforms':[{'id':'telemost','label':'Яндекс Телемост'}],
'accounts':[{'id':'qa-account','name':'Домашний Телемост','platform':'telemost','portal':'','has_cookies':True,'updated_at':'2026-10-15T12:00:00Z','browser_open':False}],
'sessions':[{'id':'qa-session','name':'Основной туннель','account_id':'qa-account','state':'running','resources':'default','has_link':True,'telemetry':'live','peer':'recent','rx_bytes':4096,'tx_bytes':8192,'rx_rate':2048,'tx_rate':4096,'active_connections':3}]}
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True)
  ctx=await b.new_context(viewport={'width':1440,'height':1050})
  await ctx.add_init_script('''window.__feedback=[];window.__cards=0;window.__listeners=0;
   const animate=Element.prototype.animate;Element.prototype.animate=function(...args){window.__feedback.push(this.className||this.tagName);return animate.apply(this,args)};
   const add=MediaQueryList.prototype.addEventListener,remove=MediaQueryList.prototype.removeEventListener;
   MediaQueryList.prototype.addEventListener=function(type,...args){if(type==='change')window.__listeners++;return add.call(this,type,...args)};
   MediaQueryList.prototype.removeEventListener=function(type,...args){if(type==='change')window.__listeners--;return remove.call(this,type,...args)};
   document.addEventListener('animationstart',e=>{if(e.animationName==='card-appear')window.__cards++});
   window.EventSource=class{constructor(){this.readyState=0;window.__stream=this}close(){this.readyState=2}};
  ''')
  async def api(route):
   path=route.request.url.split('/api/',1)[1]
   await route.fulfill(json=state if path=='state' else [] if path.startswith('logs') else {'ok':True})
  await ctx.route('**/api/**',api)
  page=await ctx.new_page();errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('console',lambda m: errors.append(m.text) if m.type=='error' else None)
  await page.goto('http://localhost:8080')
  await page.get_by_role('heading',name='Центр управления').wait_for()
  async def emit(data=state):
   await page.evaluate('(data)=>{window.__stream.readyState=1;window.__stream.onmessage({data:JSON.stringify(data)})}',data)
  await emit();await page.wait_for_timeout(700)
  assert await page.locator('.system-card').get_attribute('data-connection')=='connected'
  initialCards=await page.evaluate('window.__cards');assert initialCards==4,initialCards
  await page.evaluate('window.__feedback=[]')
  for i in range(5):
   identical={**state,'at':f'2026-10-15T12:00:0{i+1}Z'}
   await emit(identical)
   await page.wait_for_timeout(50)
  assert await page.evaluate('window.__feedback.length')==0,'Identical snapshots triggered feedback'
  assert await page.evaluate('window.__cards')==initialCards,'SSE replayed card entrance'
  updated=json.loads(json.dumps(state));updated['sessions'][0]['rx_rate']=4096
  await emit(updated)
  await page.wait_for_timeout(80)
  assert await page.locator('.stats article').nth(2).locator('strong').inner_text()=='8.0 КБ/ с'
  assert await page.evaluate('window.__feedback.length')==2,'Only summary and changed RX should flash'
  await page.screenshot(path=str(root/'changed-value.png'),full_page=True)
  await page.wait_for_timeout(600)
  assert await page.evaluate('document.getAnimations().length')==0,'Feedback did not finish'
  for ready,expected in [(0,'reconnecting'),(2,'disconnected')]:
   await page.evaluate('(ready)=>{window.__stream.readyState=ready;window.__stream.onerror()}',ready)
   await page.wait_for_timeout(600)
   assert await page.locator('.system-card').get_attribute('data-connection')==expected
   await page.screenshot(path=str(root/f'{expected}.png'),full_page=True)
  await emit(updated);await page.wait_for_timeout(700)
  assert await page.locator('.system-card').get_attribute('data-connection')=='connected'
  updated['sessions'][0]['state']='error';await emit(updated);await page.wait_for_timeout(600)
  assert await page.locator('.badge.error').inner_text()=='Ошибка'
  await page.screenshot(path=str(root/'session-error.png'),full_page=True)
  # Listener counts return to the same value after each navigation round trip.
  before=await page.evaluate('window.__listeners')
  for _ in range(3):
   await page.get_by_role('button',name='Аккаунты',exact=False).first.click()
   await page.get_by_role('button',name='Настроить',exact=True).wait_for();await page.wait_for_timeout(350)
   await page.get_by_role('button',name='Сессии',exact=False).first.click()
   await page.locator('.stats').wait_for();await page.wait_for_timeout(450)
  assert await page.evaluate('window.__listeners')==before,'Media listeners leaked on navigation'
  # Reduced motion suppresses both CSS appearance and JS value feedback.
  await page.emulate_media(reduced_motion='reduce');await page.evaluate('window.__feedback=[]')
  updated['sessions'][0]['rx_rate']=8192;await emit(updated);await page.wait_for_timeout(100)
  assert await page.evaluate('window.__feedback.length')==0
  assert await page.evaluate('document.getAnimations().length')==0
  for width in [1440,768,375]:
   await page.set_viewport_size({'width':width,'height':900})
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   await page.screenshot(path=str(root/f'dashboard-{width}.png'),full_page=True)
  await page.get_by_role('button',name='Новая сессия').click();await page.get_by_role('dialog').wait_for()
  assert await page.locator('.dialog').evaluate('(el)=>getComputedStyle(el).transitionDuration')=='0s'
  await page.keyboard.press('Escape');await page.get_by_role('dialog').wait_for(state='detached')
  # Mobile sidebar hides logout: return desktop to check directive disposal on logout.
  await page.set_viewport_size({'width':1440,'height':1050})
  if await page.get_by_role('button',name='Выйти',exact=True).count():await page.get_by_role('button',name='Выйти',exact=True).click()
  await page.get_by_role('heading',name='Всё под контролем.').wait_for()
  assert await page.evaluate('window.__listeners')==0,'Directive listeners retained after logout'
  assert await page.evaluate('document.getAnimations().length')==0
  assert not errors,errors
  print('PASS: identical SSE, exact change feedback, no replay, reconnect/close/recover, session error, completed effects, listener disposal, reduced motion, 1440/768/375, stage-1 dialog.')
  await b.close()
asyncio.run(main())
