"""Serial, credential-free publisher access diagnostics. HTTP status is evidence, not a guessed cause."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import socket
import ssl
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from library import ROOT,registry

CHALLENGES=['are you a robot','verify you are human','checking your browser','just a moment','captcha','enable javascript and cookies to continue']
SOLUTIONS={
 'accessible':'入口可读取；仍须单独验证目录分页、在线日期及正式摘要解析。',
 'rate_limited':'遵守 Retry-After，降低频率；不轮换IP或密集重试。',
 'authentication_required':'核查出版社 API 和机构授权；人工登录不能替代服务器授权。',
 'browser_challenge':'优先申请出版社 API 或获准的数据入口；浏览器可读仅作为辅助，验证码交由用户处理。',
 'forbidden_unexplained':'403 未说明具体原因；需比较浏览器访问、出版社访问政策及实际服务器出口，不能直接归因为反爬。',
 'certificate_error':'核查证书链、系统时间与受信任根证书，不关闭 TLS 验证。',
 'network_error':'核查 DNS、连接与出口网络；后续分时重测，避免重复请求。',
 'http_error':'核对官方入口是否迁移及服务是否故障，保留状态码和响应证据。',
 'unrecognised_response':'响应并非可识别的正常页面；人工核对重定向或页面结构，不能记为零篇。'}

def classify(status,text,error=None):
    low=(text or '').lower()
    if error=='certificate_error':return 'certificate_error'
    if error:return 'network_error'
    if status==429:return 'rate_limited'
    if status==401:return 'authentication_required'
    if any(s in low for s in CHALLENGES):return 'browser_challenge'
    if status==403:return 'forbidden_unexplained'
    if status and status>=400:return 'http_error'
    if status==200 and ('<html' in low or '<!doctype html' in low):return 'accessible'
    return 'unrecognised_response'

def probe(url):
    data={'url':url,'tested_at':datetime.now(timezone.utc).isoformat(),'status':None,'error':None}
    body=b'';headers={}
    try:
        with urlopen(Request(url,headers={'User-Agent':'LiteratureHOT/0.2 (journal metadata access verification)','Accept':'text/html'}),timeout=20) as res:
            data.update(status=res.status,final_url=res.url);headers=dict(res.headers);body=res.read(2*1024*1024)
    except HTTPError as exc:
        data.update(status=exc.code,final_url=exc.url);headers=dict(exc.headers);body=exc.read(500000);exc.close()
    except (URLError,TimeoutError,OSError) as exc:
        data['error']='certificate_error' if isinstance(getattr(exc,'reason',None),ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(exc) else 'network_error'
        data['error_detail']=str(exc)
    text=body.decode('utf-8',errors='replace')
    data['classification']=classify(data['status'],text,data['error'])
    data['signals']=[s for s in CHALLENGES if s in text.lower()]
    # Deliberately omit cookies/session headers.
    data['headers']={k:v for k,v in headers.items() if k.lower() in {'server','content-type','retry-after','cf-ray','location'}}
    data['body_sha256']=hashlib.sha256(body).hexdigest()
    data['solution']=SOLUTIONS[data['classification']]
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--only',nargs='+');p.add_argument('--environment-label',default='local Windows, no institution session');a=p.parse_args()
    folder=ROOT/'outputs/access-diagnostics';folder.mkdir(parents=True,exist_ok=True)
    result={'environment':a.environment_label,'platform':platform.system(),'host':socket.gethostname(),'tested_at':datetime.now(timezone.utc).isoformat(),'journals':[]}
    for source in registry().values():
        if a.only and source['seed_issn'] not in a.only:continue
        record={'journal':source['title'],'seed_issn':source['seed_issn'],**probe(source['homepage'])}
        result['journals'].append(record)
        (folder/'latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(source['title'],record['classification'],record['status'],flush=True)
        time.sleep(3)
    lines=['# 出版社访问诊断','',f"测试环境：{result['environment']}。本次为无机构会话的串行 HTTP 检查，尚非目标服务器实测。",'',
           '正常首页不等于目录和摘要均已覆盖；403 无明确挑战提示时不推断根因。无 Cookie 或密钥进入此报告。','',
           '| 期刊 | HTTP | 观察到的情况 | 可行处理路径（未实施的只是建议） |','|---|---|---|---|']
    for r in result['journals']:lines.append(f"| {r['journal']} | {r['status']} | {r['classification']}; {', '.join(r['signals']) or r.get('error') or '无明确挑战文字'} | {r['solution']} |")
    (folder/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
