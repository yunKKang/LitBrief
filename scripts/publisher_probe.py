"""One DOI, one read-only API request. Never stores abstracts, logs secrets or invokes AI.

Use --live only with the relevant API entitlement. Missing credentials never send a request.
Results describe this request, not whole-journal coverage or permission for third-party AI.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from audit_sources import doi, plain

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = ('scopus', 'sciencedirect', 'springer', 'europepmc')
KEY_NAMES = ('LITERATUREHOT_ELSEVIER_API_KEY', 'LITERATUREHOT_ELSEVIER_INSTTOKEN',
             'LITERATUREHOT_SPRINGER_API_KEY')
MAX_BYTES = 2 * 1024 * 1024


def credentials():
    values = {}
    env_file = ROOT / '.env'
    if env_file.exists():
        for line in env_file.read_text('utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in KEY_NAMES:
                values[key.strip()] = value.strip().strip('\"\'')
    values.update({k: os.environ[k] for k in KEY_NAMES if k in os.environ})
    return values


def request_for(provider, identifier, keys):
    identifier = doi(identifier)
    if not identifier or provider not in PROVIDERS:
        raise ValueError('A valid DOI and known provider are required')
    headers = {'User-Agent': 'LitBrief/0.1 (single DOI access verification)'}
    if provider in ('scopus', 'sciencedirect'):
        key = keys.get(KEY_NAMES[0])
        if not key:
            return None
        headers.update({'X-ELS-APIKey': key, 'Accept': 'application/xml'})
        if keys.get(KEY_NAMES[1]):
            headers['X-ELS-Insttoken'] = keys[KEY_NAMES[1]]
        product = 'abstract' if provider == 'scopus' else 'article'
        url = f'https://api.elsevier.com/content/{product}/doi/{quote(identifier, safe="/")}?view=META_ABS'
    elif provider == 'springer':
        key = keys.get(KEY_NAMES[2])
        if not key:
            return None
        headers['Accept'] = 'application/json'
        url = 'https://api.springernature.com/meta/v2/json?' + urlencode({'q': 'doi:' + identifier, 'api_key': key})
    else:
        headers['Accept'] = 'application/json'
        url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urlencode({
            'query': 'DOI:"' + identifier + '"', 'format': 'json', 'resultType': 'core'})
    return Request(url, headers=headers)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(request):
    """No raw URL, response headers or exception messages escape this boundary."""
    try:
        with build_opener(NoRedirect).open(request, timeout=35) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return None, b'', {'transport_status': 'response_too_large'}
            return response.status, body, {}
    except HTTPError as exc:
        # Do not persist the raw error body; it may echo credentials.
        body = exc.read(65536)
        retry = exc.headers.get('Retry-After', '')
        return exc.code, body, {'retry_after_seconds': int(retry)} if retry.isdigit() else {}
    except (URLError, TimeoutError, OSError):
        return None, b'', {'transport_status': 'network_error'}


def error_status(status, body):
    text = body.decode('utf-8', errors='replace').lower()
    if status == 429:
        return 'rate_limited'
    if status in (301, 302, 303, 307, 308):
        return 'redirect_blocked'
    if any(code in text for code in ('insufficient_privileges', 'insufficient scope', 'not entitled', 'not_entitled')):
        return 'not_entitled'
    if status == 401:
        return 'authentication_failed'
    if status == 403:
        return 'challenge_page' if any(v in text for v in ('just a moment', 'verify you are human')) else 'forbidden_unknown'
    if status == 404:
        return 'record_not_found'
    return 'request_failed'


def verified_abstract(identifier, actual_doi, abstract, journal=''):
    if doi(actual_doi) != doi(identifier):
        return {'status': 'content_mismatch'}
    abstract = plain(abstract)
    if not abstract:
        return {'status': 'abstract_missing', 'doi_verified': True}
    return {'status': 'abstract_available', 'doi_verified': True, 'journal': plain(journal),
            'abstract_characters': len(abstract), 'abstract_sha256': hashlib.sha256(abstract.encode()).hexdigest()}


def parse(provider, identifier, body):
    try:
        if provider in ('scopus', 'sciencedirect'):
            if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
                return {'status': 'parse_failed'}
            root = ET.fromstring(body)
            expected_root = 'abstracts-retrieval-response' if provider == 'scopus' else 'full-text-retrieval-response'
            if root.tag.rsplit('}', 1)[-1] != expected_root:
                return {'status': 'parse_failed'}
            cores = root.findall('./{*}coredata')
            if len(cores) != 1:
                return {'status': 'parse_failed'}
            core = cores[0]
            def field(name):
                node = core.find('./{*}' + name)
                return ''.join(node.itertext()) if node is not None else ''
            # Read only the primary record; never accept a referenced paper's DOI/abstract.
            return verified_abstract(identifier, field('doi'), field('description'), field('publicationName'))
        data = json.loads(body)
        if provider == 'springer':
            records = data['records']
            matches = [r for r in records if doi(r.get('doi')) == doi(identifier)]
            journal_key, abstract_key = 'publicationName', 'abstract'
        else:
            records = data['resultList']['result']
            matches = [r for r in records if doi(r.get('doi')) == doi(identifier)]
            journal_key, abstract_key = 'journalTitle', 'abstractText'
        if not records:
            return {'status': 'record_not_found'}
        if not matches:
            return {'status': 'content_mismatch'}
        abstracts = {plain(r.get(abstract_key)) for r in matches if r.get(abstract_key)}
        if len(abstracts) > 1:
            return {'status': 'content_mismatch'}
        return verified_abstract(identifier, matches[0]['doi'], next(iter(abstracts), ''), matches[0].get(journal_key, ''))
    except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
        return {'status': 'parse_failed'}


def probe(provider, identifier, keys, live=False, fetch=transport):
    request = request_for(provider, identifier, keys)
    result = {'provider': provider, 'doi': doi(identifier), 'network_calls': 0, 'ai_calls': 0}
    if request is None:
        return {**result, 'status': 'credential_missing'}
    if not live:
        return {**result, 'status': 'ready_for_explicit_probe', 'note': 'Credentials present; entitlement unverified'}
    status, body, details = fetch(request)
    result.update(network_calls=1, http_status=status, **details)
    if status != 200:
        result['status'] = details.get('transport_status') or error_status(status, body)
    else:
        result.update(parse(provider, identifier, body))
    # Defence in depth against a provider echoing a credential inside parsed metadata.
    for name, value in list(result.items()):
        if isinstance(value, str):
            for secret in keys.values():
                if secret:
                    value = value.replace(secret, '[redacted]')
            result[name] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=PROVIDERS)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--doi')
    target.add_argument('--paper-id', type=int)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    identifier = args.doi
    if args.paper_id is not None:
        database = ROOT / 'outputs/library/library.sqlite3'
        with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True) as db:
            row = db.execute('SELECT data FROM papers WHERE id=?', (args.paper_id,)).fetchone()
        identifier = json.loads(row[0]).get('doi') if row else None
    if not doi(identifier):
        parser.error('Provide a valid DOI or an existing paper with a DOI')
    print(json.dumps(probe(args.provider, identifier, credentials(), args.live), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
