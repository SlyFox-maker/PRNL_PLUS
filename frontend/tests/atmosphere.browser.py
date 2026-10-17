"""Bounded atmosphere effects, accessibility and lifecycle checks; no real API writes."""
import asyncio, json, os
from pathlib import Path
from playwright.async_api import async_playwright
root=Path(os.environ.get('ARTIFACT_DIR','/artifacts'));root.mkdir(exist_ok=True)
state={'at':'2026-10-15T12:00:00Z','accounts':[],'sessions':[],'platforms':[]}
init='''window.__visual=[];window.__raf=new Set;window.__motionListeners=0;window.__visibilityListeners=0;window.__hidden=false;
Object.defineProperty(document,'hidden',{get:()=>window.__hidden,configurable:true});
const animate=Element.prototype.animate;Element.prototype.animate=function(...args){window.__visual.push({target:this.className,duration:args[1]?.duration,delay:args[1]?.delay||0});return animate.apply(this,args)};
const raf=window.requestAnimationFrame,caf=window.cancelAnimationFrame;
window.requestAnimationFrame=cb=>{let id=raf(t=>{window.__raf.delete(id);cb(t)});window.__raf.add(id);return id};window.cancelAnimationFrame=id=>{window.__raf.delete(id);caf(id)};
const add=MediaQueryList.prototype.addEventListener,remove=MediaQueryList.prototype.removeEventListener;
MediaQueryList.prototype.addEventListener=function(type,...args){if(type==='change')window.__motionListeners++;return add.call(this,type,...args)};
MediaQueryList.prototype.removeEventListener=function(type,...args){if(type==='change')window.__motionListeners--;return remove.call(this,type,...args)};
const da=document.addEventListener.bind(document),dr=document.removeEventListener.bind(document);
document.addEventListener=(t,...args)=>{if(t==='visibilitychange')window.__visibilityListeners++;return da(t,...args)};document.removeEventListener=(t,...args)=>{if(t==='visibilitychange')window.__visibilityListeners--;return dr(t,...args)};
window.EventSource=class{constructor(){this.readyState=0;window.__stream=this}close(){this.readyState=2}};
'''
async def context(browser,reduced=False,hidden=False):
 ctx=await browser.new_context(viewport={'width':1440,'height':1050}, reduced_motion='reduce' if reduced else 'no-preference')
 await ctx.add_init_script(init+('window.__hidden=true;' if hidden else ''))
 authenticated=False
 async def api(route):
  nonlocal authenticated
  path=route.request.url.split('/api/',1)[1]
  if path=='login':authenticated=True
  if path=='logout':authenticated=False
  if path=='state' and not authenticated:await route.fulfill(status=401,json={'detail':'Войдите'});return
  await route.fulfill(json=state if path=='state' else [] if path.startswith('logs') else {'ok':True})
 await ctx.route('**/api/**',api)
 return ctx
async def login(page):
 await page.get_by_label('Пароль администратора').fill('fixture-only-not-a-real-password')
 await page.get_by_role('button',name='Войти в панель').click()
 await page.get_by_role('heading',name='Центр управления').wait_for()
async def emit(page):
 await page.evaluate('(data)=>{window.__stream.readyState=1;window.__stream.onmessage({data:JSON.stringify(data)})}',state)
async def final_text(page):
 assert await page.locator('.wordmark-display').all_text_contents()==['prnl','+']
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True)
  ctx=await context(b);page=await ctx.new_page();errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://localhost:8080');await page.wait_for_timeout(500)
  assert await page.locator('.terminal-signature').inner_text()=='PRNL / CONTROL PLANE'
  await page.screenshot(path=str(root/'login.png'),full_page=True)
  assert await page.evaluate('window.__raf.size')==0
  await login(page)
  brand_duration=await page.locator('.wordmark-plus .wordmark-display').evaluate('(el)=>getComputedStyle(el).animationDuration')
  await page.wait_for_timeout(70)
  initial_box=await page.locator('.wordmark').bounding_box()
  await page.screenshot(path=str(root/'decrypt-in-progress.png'),full_page=True)
  # Buttons are usable while the intro is running; fields are never scrambled.
  await page.get_by_role('button',name='Новая сессия').click(force=True)
  await page.get_by_label('Название сессии').fill('Ввод во время появления')
  await page.wait_for_timeout(500)
  assert await page.get_by_label('Название сессии').input_value()=='Ввод во время появления'
  for _ in range(10): await page.keyboard.press('Tab')
  assert await page.evaluate('!!document.activeElement.closest(".dialog")')
  await final_text(page)
  assert float(brand_duration.removesuffix('s')) <= .6 and float(brand_duration.removesuffix('s')) > 0, brand_duration
  after_box=await page.locator('.wordmark').bounding_box()
  assert all(abs(initial_box[k]-after_box[k])<.1 for k in ('width','height')),f'Wordmark dimensions shifted: {initial_box} -> {after_box}'
  await page.keyboard.press('Escape');await page.wait_for_timeout(300)
  assert await page.evaluate('document.activeElement.textContent.includes("Новая сессия")')
  assert await page.evaluate('window.__raf.size')==0
  # Four boot animations and one terminal reveal, no persistent tasks.
  effects=await page.evaluate('window.__visual')
  assert len([e for e in effects if e['duration']==260])==4
  assert len([e for e in effects if e['duration']==360])==1
  assert await page.evaluate('window.__visibilityListeners')==0
  await page.wait_for_timeout(700)
  assert await page.evaluate('document.getAnimations().every(a=>a.playState==="finished")'),'Decorative animations did not finish'
  await emit(page);await page.wait_for_timeout(600)
  before=await page.evaluate('window.__visual.length')
  for _ in range(4):await emit(page)
  await page.wait_for_timeout(650)
  assert await page.evaluate('window.__visual.length')==before
  for name in ['Аккаунты','Журнал событий','Сессии']:
   await page.get_by_role('button',name=name,exact=False).first.click();await page.wait_for_timeout(500)
  assert len([e for e in await page.evaluate('window.__visual') if e['duration']==260])==4
  for width in [1440,768,375]:
   await page.set_viewport_size({'width':width,'height':900})
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   await final_text(page)
   await page.screenshot(path=str(root/f'dashboard-{width}.png'),full_page=True)
  await page.set_viewport_size({'width':1440,'height':1050})
  await page.get_by_role('button',name='Выйти',exact=True).click()
  await page.get_by_role('heading',name='Всё под контролем.').wait_for()
  await login(page);await page.wait_for_timeout(650);await final_text(page)
  assert len([e for e in await page.evaluate('window.__visual') if e['duration']==260])==4,'Boot repeated'
  await page.get_by_role('button',name='Выйти',exact=True).click();await page.wait_for_timeout(300)
  assert await page.evaluate('window.__motionListeners')==0
  assert await page.evaluate('window.__visibilityListeners')==0
  assert await page.evaluate('window.__raf.size')==0
  assert not errors,errors
  await ctx.close()
  for mode in ['reduced','hidden','cancel-reduced','cancel-hidden']:
   ctx=await context(b,reduced=mode=='reduced',hidden=mode=='hidden');page=await ctx.new_page()
   await page.goto('http://localhost:8080');await login(page)
   if mode=='cancel-reduced':await page.emulate_media(reduced_motion='reduce')
   if mode=='cancel-hidden':await page.evaluate("window.__hidden=true;document.dispatchEvent(new Event('visibilitychange'))")
   await page.wait_for_timeout(700);await final_text(page)
   assert await page.evaluate('window.__raf.size')==0
   assert await page.evaluate('window.__visibilityListeners')==0
   assert await page.evaluate('document.getAnimations().length')==0, f'Unexpected animation for {mode}: {await page.evaluate("[...document.getAnimations()].map(a=>({name:a.animationName,state:a.playState}))")}'
   if mode in ['reduced','hidden']:assert await page.evaluate('window.__visual.length')==0
   await ctx.close()
  print('PASS: original final text, <=600ms settle, fixed wordmark size, nonblocking input, terminal label, boot once, identical SSE, navigation, login/logout, 375/768/1440, reduced/hidden cancellation, listener/RAF cleanup, no JS errors.')
  await b.close()
asyncio.run(main())
