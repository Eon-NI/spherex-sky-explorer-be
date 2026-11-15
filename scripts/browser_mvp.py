"""Bounded browser checks using installed Edge and the local dependency-free CDP client."""
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from scripts.cdp_client import BrowserCDP


def main():
    phase = sys.argv[1]
    if phase not in ['search', 'blink', 'timelapse', 'spectral']:
        raise ValueError('Unknown browser phase')
    Path('tmp').mkdir(exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    evidence = {'phase': phase, 'passed': False}
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(dir='tmp', prefix='eon-browser-', ignore_cleanup_errors=True) as profile:
        edge = subprocess.Popen([r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', '--headless=new',
                                 '--disable-gpu', '--no-first-run', '--remote-debugging-port='+str(port),
                                 '--user-data-dir='+str(Path(profile).resolve()), 'about:blank'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        client = None
        try:
            for _ in range(40):
                try:
                    pages = session.get(f'http://127.0.0.1:{port}/json', timeout=(1, 2)).json()
                    page = next(p for p in pages if p['type']=='page')
                    client = BrowserCDP(page['webSocketDebuggerUrl'])
                    break
                except (requests.RequestException, StopIteration):
                    time.sleep(.25)
            assert client
            client.call('Network.enable')
            url = '/timelapse?ra=210.80227&dec=54.34895&fov=0.1&det=D2&start=2025-05-01&end=2026-11-01'
            if phase in ['search', 'blink']:
                url = '/search?ra=210.80227&dec=54.34895&fov=0.1&mode='+('single' if phase=='search' else 'blink')
            if phase == 'spectral':
                url = '/spectral?ra=10.6847&dec=41.26875&fov=0.1'
            client.call('Page.navigate', {'url':'http://localhost:8080'+url})
            expected = 5 if phase=='timelapse' else 1 if phase=='spectral' else 2
            deadline = time.monotonic()+480
            responses = []
            while time.monotonic()<deadline:
                client.evaluate('document.readyState')
                responses = [e['params']['response'] for e in client.events if e.get('method')=='Network.responseReceived']
                pngs = [r for r in responses if r['status']==200 and ('/frames/' in r['url'] or '/preview/' in r['url'])]
                if len(pngs)>=expected or any(r['status']>=400 and '/api/v1/' in r['url'] for r in responses):
                    break
                time.sleep(1)
            time.sleep(2)
            evidence['requests'] = [{'url':r['url'],'status':r['status'],'type':r.get('mimeType')} for r in responses if '/api/' in r['url']]
            evidence['body'] = client.evaluate('document.body.innerText')[-6500:]
            assert len(pngs)>=expected, 'Real frame delivery did not complete'
            if phase == 'blink':
                selections = client.evaluate("(()=>{const s=[...document.querySelectorAll('#search select')].filter(s=>s.options.length>0&&/UTC/.test(s.options[0].textContent));if(s.length!==2)return null;s[0].value=s[1].value;s[0].dispatchEvent(new Event('change',{bubbles:true}));return true})()")
                assert selections, 'Epoch controls missing'
                values = client.evaluate("[...document.querySelectorAll('#search select')].filter(s=>s.options.length>0&&/UTC/.test(s.options[0].textContent)).map(s=>s.value)")
                assert len(set(values))==2, 'Blink allowed identical epochs'
                evidence['distinct_epoch_guard'] = values
            canvas = "document.querySelector('section canvas')?.toDataURL()"
            before = (client.call('Page.captureScreenshot', {'format':'png'})['data'] if phase=='spectral' else client.evaluate(canvas))
            assert before
            evidence['canvas_sha256'] = hashlib.sha256(before.encode()).hexdigest()
            if phase == 'spectral':
                point = client.evaluate("(()=>{const b=[...document.querySelectorAll('button')].find(b=>/Trazar espectro del centro|Plot.*center/i.test(b.textContent));if(!b||b.disabled)return null;b.scrollIntoView({block:'center',behavior:'instant'});const r=b.getBoundingClientRect();const x=r.left+r.width/2,y=r.top+r.height/2;const h=document.elementFromPoint(x,y);return {x,y,hit:!!h&&(b===h||b.contains(h))}})()")
                assert point, 'Spectral center-spectrum control is not available'
                assert point.pop('hit'), 'Spectral button is not hit-test accessible'
                client.call('Input.dispatchMouseEvent', {'type':'mouseMoved', **point})
                client.call('Input.dispatchMouseEvent', {'type':'mousePressed', **point, 'button':'left', 'clickCount':1})
                client.call('Input.dispatchMouseEvent', {'type':'mouseReleased', **point, 'button':'left', 'clickCount':1})
                spectral_result = None
                deadline = time.monotonic()+120
                while time.monotonic()<deadline and spectral_result is None:
                    client.evaluate('document.readyState')
                    for event in list(reversed(client.events)):
                        if event.get('method') != 'Network.responseReceived':
                            continue
                        params = event['params']
                        if '/api/v1/spectral/jobs/' not in params['response']['url']:
                            continue
                        try:
                            payload = json.loads(client.call('Network.getResponseBody', {'requestId':params['requestId']})['body'])
                        except (RuntimeError, ValueError):
                            continue
                        result = payload.get('result') or {}
                        if payload.get('status')=='completed' and 'points' in result:
                            spectral_result = result
                            break
                    if spectral_result is None:
                        time.sleep(1)
                assert spectral_result is not None, 'Spectral point/spectrum workflow did not complete'
                assert len(spectral_result['points']) == 102
                assert all(spectral_result['points'][i]['flux'] is None for i in [50,51])
                evidence['spectrum'] = {'point_count':102,'blank_channels':[50,51],
                                        'product_id':spectral_result['product_id'],'unit':spectral_result['unit']}
                evidence['body'] = client.evaluate('document.body.innerText')[-6500:]

            if phase in ['blink', 'timelapse']:
                clicked = client.evaluate("(()=>{const b=[...document.querySelectorAll('section button[aria-label]')].find(b=>/play|reproducir|reanudar/i.test(b.getAttribute('aria-label'))&&!b.disabled);if(b)b.click();return !!b})()")
                assert clicked, 'Playback control missing'
                hashes = []
                for _ in range(5):
                    time.sleep(.33)
                    image = client.evaluate(canvas)
                    hashes.append(hashlib.sha256(image.encode()).hexdigest())
                evidence['playback_hashes'] = hashes
                assert len(set(hashes+[evidence['canvas_sha256']]))>1, 'Playback canvas did not change'
            evidence['passed'] = True
        except Exception as exc:  # noqa: BLE001 - diagnostic CLI
            if client:
                evidence['late_network'] = [{'url': e['params']['response']['url'], 'status': e['params']['response']['status']} for e in client.events if e.get('method')=='Network.responseReceived' and '/api/' in e['params']['response']['url']]
            evidence['error'] = type(exc).__name__+': '+str(exc)[:500]
        finally:
            if client:
                try:
                    client.call('Browser.close')
                except (ConnectionError, OSError):
                    pass
            try:
                edge.wait(timeout=10)
            except subprocess.TimeoutExpired:
                edge.terminate()
    if '--save' in sys.argv[2:]:
        root = Path('docs/evidence/mvp_integration')
        root.mkdir(parents=True, exist_ok=True)
        (root/('browser-'+phase+'.json')).write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=True), flush=True)
    return 0 if evidence['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
