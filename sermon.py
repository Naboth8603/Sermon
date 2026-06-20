"""
설교 유튜브 링크 → 4컷 노트 인포그래픽  (표준 라이브러리만 사용)
요약·구성: Google Gemini  /  화면: 손글씨 4컷 노트

실행:  GEMINI_API_KEY=... python sermon.py     →  http://localhost:8000
키 발급: aistudio.google.com/apikey
"""
import os, re, json, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 키: 환경변수 또는 아래 따옴표 안에 한 번만 붙여넣기 (UI에서 입력 X) ──
GEMINI_API_KEY = "" or os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
PORT           = int(os.environ.get("PORT", "8000"))
MAX_CHARS      = int(os.environ.get("MAX_CHARS", "48000"))  # 2시간 설교까지 수용
UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.9", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def video_id(url):
    m = re.search(r"(?:youtu\.be/|v=|embed/|shorts/|/live/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def _json_array_after(text, key):
    """text 안에서 "key":[ ... ] 배열을 대괄호 깊이로 정확히 잘라낸다."""
    i = text.find('"%s":' % key)
    if i < 0:
        return None
    i = text.index("[", i)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "[": depth += 1
        elif text[j] == "]":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def get_transcript(vid):
    page = http_get("https://www.youtube.com/watch?v=%s&hl=ko" % vid, {"Cookie": "CONSENT=YES+1"})
    raw = _json_array_after(page, "captionTracks")
    if not raw:
        raise RuntimeError("이 영상에는 자막이 없어요. (자동 자막이 없는 영상일 수 있어요)")
    tracks = json.loads(raw)
    tracks.sort(key=lambda t: 0 if t.get("languageCode", "").startswith("ko")
                else 1 if t.get("languageCode", "").startswith("en") else 2)
    data = http_get(tracks[0]["baseUrl"] + "&fmt=json3")
    segs = [s.get("utf8", "") for ev in json.loads(data).get("events", []) for s in (ev.get("segs") or [])]
    return re.sub(r"\s+", " ", "".join(segs)).strip()


SYSTEM = """너는 교회 설교를 "손글씨 노트 4컷 인포그래픽" 데이터로 바꾸는 전문가다.
아래 JSON 스키마로만 답한다. meta의 title·scripture·preacher·date는 스크립트에서 추론해 채운다.
각 항목은 노트 필기처럼 짧고 간결하게(한 줄) 한국어로 쓴다.
{"meta":{"title":"","scripture":"","preacher":"","date":"","confession":"한 줄 결단"},
 "panels":[{"no":1,"title":"소제목","icon":"이모지1개","blocks":[
   {"t":"sec","h":"소제목","c":"#fbeeb0","items":["..."]},
   {"t":"check","h":"기억하자!","items":["..."]},
   {"t":"verse","ref":"성경 약자 장:절","text":"핵심 구절 요약"},
   {"t":"compare","lh":"세상","li":["..."],"rh":"하나님","ri":["..."]},
   {"t":"callout","text":"핵심 한 줄","c":"#f9c9d6"},
   {"t":"table","cols":["A","B","C"],"rows":[["..","..",".."]]}]}]}
규칙: panels는 정확히 4개(도입→전개→적용→결론), 패널당 블록 2~4개.
색은 #fbeeb0 #f9c9d6 #bfdcf5 #c7e8bf #ddccf0 중 선택. table은 비교가 꼭 필요할 때만.
스크립트에 '…(중략)…' 표시가 있으면 중간이 생략된 것이니, 전체 설교 흐름을 추론해 4컷을 구성한다."""


def fit(text):
    """길면 앞·중간·끝을 균형 있게 추려 MAX_CHARS 안에 담는다(도입·전개·결론 보존)."""
    if len(text) <= MAX_CHARS:
        return text
    f = int(MAX_CHARS * 0.4)                 # 앞 40% (도입·본문 시작)
    m = int(MAX_CHARS * 0.2)                 # 중간 20% (전개)
    e = MAX_CHARS - f - m                    # 끝 40% (적용·결론)
    mid = (len(text) - m) // 2
    return text[:f] + "\n…(중략)…\n" + text[mid:mid + m] + "\n…(중략)…\n" + text[-e:]


def gemini_summarize(transcript):
    body = json.dumps({
        "contents": [{"parts": [{"text": SYSTEM + "\n\n[설교 스크립트]\n" + fit(transcript)}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 4000},
    }).encode("utf-8")
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (GEMINI_MODEL, GEMINI_API_KEY)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])


class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/":
            return self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
        if p.path == "/manifest.json":
            return self._send(200, "application/manifest+json", MANIFEST.encode("utf-8"))
        if p.path == "/icon.svg":
            return self._send(200, "image/svg+xml", ICON.encode("utf-8"))
        if p.path != "/api/run":
            return self._send(404, "text/plain", b"not found")
        url = (urllib.parse.parse_qs(p.query).get("url") or [""])[0]
        out = {}
        try:
            if not GEMINI_API_KEY: raise RuntimeError("서버에 GEMINI_API_KEY가 없어요. 코드 상단이나 환경변수에 키를 넣어주세요.")
            vid = video_id(url)
            if not vid: raise RuntimeError("유효한 유튜브 링크가 아니에요.")
            text = get_transcript(vid)
            if len(text) < 80: raise RuntimeError("자막을 충분히 가져오지 못했어요.")
            out["result"] = gemini_summarize(text)
        except urllib.error.HTTPError as e:
            out["error"] = "Gemini 오류 %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
        except Exception as e:
            out["error"] = str(e)
        self._send(200, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *a): pass


INDEX_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
<meta name="theme-color" content="#f3efe6" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-title" content="4컷노트" />
<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/icon.svg" />
<title>설교 → 4컷 노트</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Gaegu:wght@400;700&family=Nanum+Pen+Script&display=swap');
:root{--ink:#3a3633;--ink-soft:#6b6560;--line:#e7e0d3;--pink:#f9c9d6;--pink-deep:#e0507a;--blue:#bfdcf5;--blue-bg:#eaf4fd;--blue-line:#7fb4e6;--green:#c7e8bf;--green-deep:#4a9c3e;--yellow:#fbeeb0;--purple:#ddccf0;--purple-deep:#8a6dc6;--shadow:0 10px 30px rgba(58,54,51,.10)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;padding:0}
body{font-family:'Gaegu',system-ui,sans-serif;color:var(--ink);font-size:17px;background:radial-gradient(900px 500px at 80% -10%,#fff6e8 0%,transparent 60%),radial-gradient(700px 450px at -10% 110%,#f0f6ff 0%,transparent 55%),#f3efe6;min-height:100vh;-webkit-font-smoothing:antialiased;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}
.wrap{max-width:980px;margin:0 auto;padding:22px 14px 56px}
.brand{font-family:'Nanum Pen Script',cursive;font-size:42px;line-height:.95;margin:2px 0 0}
.brand .hl{background:linear-gradient(transparent 55%,var(--yellow) 55%);padding:0 4px}
.sub{color:var(--ink-soft);font-size:16px;margin:6px 0 18px}
.card{background:#fffdf8;border:2px solid var(--line);border-radius:18px;padding:16px;box-shadow:var(--shadow)}
label{font-size:15px;color:var(--ink-soft);font-weight:700;display:block;margin-bottom:6px}
input{font-family:'Gaegu',sans-serif;font-size:18px;color:var(--ink);background:#fff;border:2px solid var(--line);border-radius:12px;padding:14px 14px;outline:none;width:100%;min-height:54px;transition:border-color .15s,box-shadow .15s}
input:focus{border-color:var(--blue-line);box-shadow:0 0 0 4px var(--blue-bg)}
.hint{font-size:13.5px;color:var(--ink-soft);margin-top:8px;line-height:1.45}
.btn{display:block;width:100%;font-family:'Gaegu',sans-serif;font-weight:700;font-size:20px;cursor:pointer;border:2px solid var(--ink);border-radius:14px;padding:15px 18px;background:var(--pink-deep);color:#fff;border-color:#b83b62;box-shadow:3px 4px 0 rgba(58,54,51,.18);margin-top:12px;transition:transform .07s}
.btn:active{transform:translate(1px,2px);box-shadow:1px 2px 0 rgba(58,54,51,.18)}
.btn.ghost{background:#fbf4ec;color:var(--ink);border-color:var(--ink)}
.btn:disabled{opacity:.5}
.status{display:none;align-items:center;gap:11px;font-size:17px;color:var(--ink-soft);margin-top:14px}
.spinner{width:22px;height:22px;border:3px solid var(--line);border-top-color:var(--pink-deep);border-radius:50%;animation:spin .8s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}
.err{display:none;color:#c0392b;background:#fdecea;border:2px solid #f5b7b1;border-radius:12px;padding:11px 14px;margin-top:12px;font-size:15px;line-height:1.5}
.tools{display:none;align-items:center;justify-content:space-between;gap:10px;margin:24px 0 12px}
.tools h2{font-family:'Nanum Pen Script',cursive;font-size:30px;margin:0}
#sheet{display:none}
.grid{display:grid;grid-template-columns:1fr;gap:16px;background:#efe8d8;padding:14px;border-radius:16px}
.panel{position:relative;background:linear-gradient(#fcfaf4,#fcfaf4) padding-box,repeating-linear-gradient(0deg,transparent,transparent 31px,#eef0e6 31px,#eef0e6 32px);border:2px solid #d9d2c4;border-radius:14px;padding:16px;box-shadow:0 6px 16px rgba(0,0,0,.07)}
.panel-no{position:absolute;top:-12px;right:14px;width:33px;height:33px;border-radius:50%;background:#fff;border:2.5px solid var(--ink);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:19px;font-family:'Nanum Pen Script',cursive}
.panel-title{font-family:'Nanum Pen Script',cursive;font-size:28px;margin:0 0 11px;border-bottom:3px solid var(--blue);padding-bottom:5px;line-height:1.1}
.block{margin-bottom:12px}.block:last-child{margin-bottom:0}
.sec-head{display:inline-block;font-weight:700;font-size:17px;background:linear-gradient(transparent 55%,var(--c,var(--yellow)) 55%);padding:0 6px;margin-bottom:5px}
.bullets{margin:2px 0 0;padding-left:4px;list-style:none}
.bullets li{position:relative;padding-left:16px;font-size:16px;line-height:1.45;margin-bottom:1px}
.bullets li::before{content:"\2022";position:absolute;left:2px;color:var(--pink-deep)}
.check-head{font-weight:700;font-size:17px;color:var(--purple-deep);margin-bottom:5px}
.check-list{margin:0;padding:0;list-style:none}
.check-list li{position:relative;padding-left:23px;font-size:16px;line-height:1.5;margin-bottom:2px}
.check-list li::before{content:"\2713";position:absolute;left:3px;color:var(--green-deep);font-weight:700}
.verse{background:var(--blue-bg);border:2px dashed var(--blue-line);border-radius:10px;padding:9px 12px}
.verse-ref{font-weight:700;color:#2f6db5;font-size:15px;margin-bottom:3px}
.verse-text{font-size:15px;line-height:1.5;color:#42504f}
.compare{display:flex;gap:8px}
.compare .col{flex:1;border-radius:10px;padding:9px 10px}
.compare .col.left{background:#fdeaee;border:2px solid var(--pink)}
.compare .col.right{background:#eaf6e7;border:2px solid var(--green)}
.compare .col-h{font-weight:700;font-size:15px;text-align:center;margin-bottom:4px}
.compare .col.left .col-h{color:#cf5a86}.compare .col.right .col-h{color:#3f9a55}
.compare ul{margin:0;padding:0;list-style:none;text-align:center}.compare li{font-size:15px;line-height:1.4}
.compare .vs{display:flex;align-items:center;font-size:20px;color:var(--ink-soft)}
.callout{text-align:center;font-weight:700;font-size:16px;line-height:1.4;background:var(--c,var(--yellow));border-radius:10px;padding:9px 12px;border:2px solid rgba(0,0,0,.06)}
.tbl{overflow:hidden;border-radius:10px;border:2px solid var(--line)}
.tbl table{width:100%;border-collapse:collapse;font-size:14px}
.tbl th{background:var(--purple);color:#4a3a66;font-weight:700;padding:6px 5px;font-size:15px}
.tbl td{padding:6px 7px;border-top:1px solid var(--line);line-height:1.35;vertical-align:top}
.tbl tr td:first-child{font-weight:700;background:#faf6ee}
.sheet-head{display:flex;justify-content:space-between;gap:8px;font-size:15px;color:var(--ink-soft);margin-bottom:8px;padding:0 2px}
.sheet-title-main{font-family:'Nanum Pen Script',cursive;font-size:34px;text-align:center;margin:0 0 2px}
.sheet-scripture{text-align:center;font-size:17px;color:var(--ink-soft);margin-bottom:8px}
.sheet-meta{margin-top:12px;text-align:center;font-family:'Nanum Pen Script',cursive;font-size:22px;color:var(--pink-deep)}
@media (min-width:720px){.grid{grid-template-columns:1fr 1fr}.panel-title{font-size:30px}}
.grid.export{grid-template-columns:1fr 1fr;width:1000px}
.overlay{display:none;position:fixed;inset:0;background:rgba(40,36,33,.82);z-index:50;padding:18px;overflow:auto;text-align:center}
.overlay.open{display:block}
.overlay .note{color:#fff;font-size:17px;margin:6px 0 12px}
.overlay img{max-width:100%;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.5)}
.overlay .btn{max-width:360px;margin:14px auto 0}
.overlay .close{position:fixed;top:14px;right:16px;color:#fff;font-size:30px;cursor:pointer;line-height:1}
</style>
</head>
<body>
<div class="wrap">
  <h1 class="brand"><span class="hl">설교</span> → 4컷 노트</h1>
  <p class="sub">유튜브 설교 링크만 넣으면 끝. 📱</p>
  <div class="card">
    <label>유튜브 링크</label>
    <input id="url" type="url" inputmode="url" placeholder="https://youtu.be/..." autocapitalize="off" autocorrect="off" spellcheck="false" />
    <button class="btn" id="go">▶️ 4컷 노트 만들기</button>
    <div class="hint">자막(자동자막 포함)이 있는 설교 영상이면 됩니다. 보통 10~30초 걸려요.</div>
    <div class="status" id="status"><div class="spinner"></div><span id="stxt">처리 중…</span></div>
    <div class="err" id="err"></div>
  </div>
  <div class="tools" id="tools"><h2>📒 완성</h2><button class="btn ghost" id="save" style="width:auto;margin:0">🖼️ 이미지 저장</button></div>
  <div id="sheet"><div class="grid" id="grid"></div></div>
</div>
<div class="overlay" id="ov">
  <span class="close" id="ovx">✕</span>
  <div class="note">이미지를 <b>길게 눌러 저장</b>하거나 아래 버튼을 누르세요</div>
  <img id="ovimg" alt="설교 노트" />
  <a class="btn" id="ovdl" download="설교노트.png">⬇️ 다운로드</a>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
const $=id=>document.getElementById(id), C=["①","②","③","④"];
const TC=[["#2f6db5","var(--blue)"],["#cf5a86","var(--pink)"],["#3f9a55","var(--green)"],["#b07ddb","var(--purple)"]];
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function blk(b){switch(b.t){
 case 'sec':return `<div class="block"><span class="sec-head" style="--c:${b.c||'var(--yellow)'}">${esc(b.h)}</span><ul class="bullets">${(b.items||[]).map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`;
 case 'check':return `<div class="block"><div class="check-head">${esc(b.h||'기억하자!')}</div><ul class="check-list">${(b.items||[]).map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`;
 case 'verse':return `<div class="block verse"><div class="verse-ref">[${esc(b.ref)}]</div><div class="verse-text">${esc(b.text)}</div></div>`;
 case 'compare':return `<div class="block compare"><div class="col left"><div class="col-h">${esc(b.lh)}</div><ul>${(b.li||[]).map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div><div class="vs">↔</div><div class="col right"><div class="col-h">${esc(b.rh)}</div><ul>${(b.ri||[]).map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div></div>`;
 case 'callout':return `<div class="block callout" style="--c:${b.c||'var(--yellow)'}">${esc(b.text)}</div>`;
 case 'table':return `<div class="block tbl"><table><thead><tr>${(b.cols||[]).map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${(b.rows||[]).map(r=>`<tr>${r.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 default:return '';}}
function render(d){
 const head=`<div style="grid-column:1/-1"><div class="sheet-head"><span>${esc(d.meta?.preacher||'')}</span><span>${esc(d.meta?.date||'')}</span></div><h1 class="sheet-title-main">${esc(d.meta?.title||'설교 요약')}</h1>${d.meta?.scripture?`<div class="sheet-scripture">[ ${esc(d.meta.scripture)} ]</div>`:''}</div>`;
 const ps=(d.panels||[]).slice(0,4).map((p,i)=>{const[c,b]=TC[i%4];return `<div class="panel"><span class="panel-no">${C[i]}</span><h2 class="panel-title" style="color:${c};border-color:${b}">${esc((p.icon?p.icon+' ':'')+(p.title||''))}</h2>${(p.blocks||[]).map(blk).join('')}</div>`;}).join('');
 const ft=d.meta?.confession?`<div class="sheet-meta" style="grid-column:1/-1">${esc(d.meta.confession)}</div>`:'';
 $('grid').innerHTML=head+ps+ft;$('sheet').style.display='block';$('tools').style.display='flex';$('tools').scrollIntoView({behavior:'smooth'});
}
function busy(on){$('status').style.display=on?'flex':'none';$('go').disabled=on;}
function err(m){$('err').innerHTML=m;$('err').style.display='block';}
$('go').addEventListener('click',async()=>{
 const url=$('url').value.trim();
 if(!url){err('유튜브 링크를 넣어주세요.');return;}
 $('err').style.display='none';busy(true);
 try{
  const r=await fetch('/api/run?url='+encodeURIComponent(url));
  const d=await r.json();
  if(d.error)err(d.error);else render(d.result);
 }catch(e){err('요청 실패: '+e.message);}finally{busy(false);}
});
$('url').addEventListener('keydown',e=>{if(e.key==='Enter')$('go').click();});
$('save').addEventListener('click',async()=>{
 const g=$('grid');g.classList.add('export');
 try{const cv=await html2canvas(g,{scale:2,backgroundColor:'#efe8d8',windowWidth:1040});
  const u=cv.toDataURL('image/png');$('ovimg').src=u;$('ovdl').href=u;$('ov').classList.add('open');}
 catch(e){err('이미지 저장 실패: '+e.message);}finally{g.classList.remove('export');}
});
$('ovx').addEventListener('click',()=>$('ov').classList.remove('open'));
$('ov').addEventListener('click',e=>{if(e.target.id==='ov')$('ov').classList.remove('open');});
</script>
</body>
</html>'''


MANIFEST = json.dumps({
    "name": "설교 4컷 노트", "short_name": "4컷노트", "start_url": "/",
    "display": "standalone", "background_color": "#f3efe6", "theme_color": "#e0507a",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
}, ensure_ascii=False)

ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
        '<rect width="192" height="192" rx="40" fill="#e0507a"/>'
        '<text x="96" y="132" font-size="104" text-anchor="middle">📒</text></svg>')


if __name__ == "__main__":
    import threading, webbrowser
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:%d" % PORT)).start()
    print("\n  ✅ http://localhost:%d   (종료: Ctrl+C)" % PORT)
    print("  키 설정됨:", "예" if GEMINI_API_KEY else "아니오 — GEMINI_API_KEY 필요\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
