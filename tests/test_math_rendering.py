"""Offline browser regression for safe Markdown plus mathematical notation."""
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_multi_user as existing


class MathTests(unittest.TestCase):
    setUp=existing.MultiUserTests.setUp
    tearDown=existing.MultiUserTests.tearDown
    register=existing.MultiUserTests.register
    own=existing.MultiUserTests.own

    def test_formulas_markdown_safety_code_and_mobile(self):
        import uvicorn
        from playwright.sync_api import sync_playwright
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True)
        with patch.dict(os.environ,{'COSIGHT_COOKIE_SECURE':'false'}):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[];requests=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda req:requests.append(req.url))
                    page.goto(f'http://127.0.0.1:{port}/cosight/login.html')
                    page.fill('#username','alice');page.fill('#password','test-password-1234');page.click('#submit')
                    page.wait_for_url('**/cosight/')
                    page.wait_for_function("typeof window.katex==='object' && typeof window.renderStudyMarkdown==='function'")
                    text=r'''### 费马小定理：结论汇总
对素数 \(p\) 与任意整数 \(a\)，两种证明路线得到同一结论：
\[ a^p\equiv a\pmod p. \]
当 \(p\nmid a\) 时，在模 \(p\) 下可约去 \(a\)，得到
\[ a^{p-1}\equiv1\pmod p. \]
分式与极限同样支持：$\frac{1}{2}$，$$\lim_{x\to0}\frac{\sin x}{x}=1.$$
**注意条件**：指数形式需要互素条件。
1. 检查素数条件。
2. 检查互素条件。'''
                    page.evaluate("text=>{window.appendChatMessage(text,'ai');document.querySelector('.initial-input-container').classList.add('hidden');document.querySelector('.middle-container').classList.add('show');window.expandChatPanel();}",text)
                    page.wait_for_selector('#chat-messages .katex')
                    self.assertGreaterEqual(page.locator('#chat-messages .katex').count(),9)
                    self.assertEqual(page.locator('#chat-messages .katex-error').count(),0)
                    self.assertEqual(page.locator('#chat-messages annotation').first.text_content(),'p')
                    self.assertTrue(page.locator('#chat-messages strong').count())
                    self.assertEqual(page.locator('#chat-messages li').count(),2)
                    self.assertNotIn('XINGGUIMATH',page.locator('#chat-messages').inner_html())
                    code=r'`\(x^2\)`'+'\n\n```tex\n'+r'\[a^p\equiv a\pmod p\]'+'\n```'
                    result=page.evaluate("text=>{const d=document.createElement('div');d.innerHTML=safeMarkdown(text);return {math:d.querySelectorAll('.katex').length,code:d.querySelectorAll('code').length,text:d.textContent};}",code)
                    self.assertEqual(result['math'],0)
                    self.assertEqual(result['code'],2)
                    self.assertIn(r'\(x^2\)',result['text'])
                    malicious=r'<img src=x onerror="window.mathAttack=1"><script>window.mathAttack=2</script> \(\href{javascript:alert(1)}{x}\) [link](javascript:alert(1))'
                    page.evaluate("text=>window.appendChatMessage(text,'ai')",malicious)
                    self.assertIsNone(page.evaluate('window.mathAttack'))
                    self.assertEqual(page.locator('#chat-messages img,#chat-messages script,#chat-messages iframe').count(),0)
                    self.assertFalse(page.evaluate("[...document.querySelectorAll('#chat-messages a')].some(a=>a.href.startsWith('javascript:'))"))
                    page.evaluate("document.querySelector('#chat-messages .chat-msg:last-child').remove()")
                    page.evaluate('document.fonts.ready')
                    page.wait_for_function("getComputedStyle(document.querySelector('.middle-container')).opacity === '1'")
                    page.wait_for_function("getComputedStyle(document.querySelector('.initial-input-container')).display === 'none' || getComputedStyle(document.querySelector('.initial-input-container')).visibility === 'hidden'")
                    folder=Path(__file__).resolve().parents[1]/'artifacts';folder.mkdir(exist_ok=True)
                    page.screenshot(path=str(folder/'math-rendering-desktop.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.evaluate("text=>window.appendChatMessage(text,'ai')",r'\[a_1+a_2+a_3+a_4+a_5+a_6+a_7+a_8+a_9+a_{10}+a_{11}+a_{12}=\sum_{i=1}^{12}a_i\]')
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),392)
                    page.screenshot(path=str(folder/'math-rendering-mobile.png'),full_page=True)
                    self.assertFalse([url for url in requests if not url.startswith(f'http://127.0.0.1:{port}')],requests)
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':unittest.main(verbosity=2)
