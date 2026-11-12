import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publisher_probe import parse, probe, request_for, error_status, KEY_NAMES, NoRedirect, transport
from urllib.request import ProxyHandler


class PublisherProbeTests(unittest.TestCase):
    def test_no_credentials_or_dry_run_never_send(self):
        fetch = Mock(side_effect=AssertionError('Network forbidden'))
        self.assertEqual(probe('scopus', '10.1000/a', {}, True, fetch)['status'], 'credential_missing')
        self.assertEqual(probe('springer', '10.1000/a', {KEY_NAMES[2]: 'secret'}, False, fetch)['network_calls'], 0)
        fetch.assert_not_called()

    def test_primary_doi_only_and_nested_abstract(self):
        xml = b'<abstracts-retrieval-response xmlns:p="urn:prism" xmlns:d="urn:dc"><coredata><p:doi>10.1000/a</p:doi><d:description><p>One paragraph.</p><p> Another.</p></d:description></coredata><references><doi>10.1000/b</doi></references></abstracts-retrieval-response>'
        self.assertEqual(parse('scopus', '10.1000/a', xml)['status'], 'abstract_available')
        self.assertEqual(parse('scopus', '10.1000/b', xml)['status'], 'content_mismatch')
        self.assertEqual(parse('scopus', '10.1000/a', b'<html>login</html>')['status'], 'parse_failed')

    def test_empty_record_differs_from_missing_abstract_and_conflict(self):
        def payload(items):
            return json.dumps({'resultList': {'result': items}}).encode()
        self.assertEqual(parse('europepmc', '10.1000/a', payload([]))['status'], 'record_not_found')
        self.assertEqual(parse('europepmc', '10.1000/a', payload([{'doi': '10.1000/a'}]))['status'], 'abstract_missing')
        self.assertEqual(parse('europepmc', '10.1000/a', payload([
            {'doi': '10.1000/a', 'abstractText': 'one'}, {'doi': '10.1000/a', 'abstractText': 'two'}]))['status'], 'content_mismatch')
        data = json.dumps({'records': [{'doi': '10.1000/a', 'abstract': 'A formal abstract.'}]}).encode()
        self.assertTrue(parse('springer', '10.1000/a', data)['doi_verified'])

    def test_secrets_not_reported_or_forwarded_and_errors_not_conflated(self):
        keys = {KEY_NAMES[0]: 'test-secret-key', KEY_NAMES[1]: 'test-institution-token'}
        req = request_for('scopus', '10.1000/a', keys)
        self.assertNotIn(keys[KEY_NAMES[0]], req.full_url)
        self.assertIn('META_ABS', req.full_url)
        self.assertIsNone(NoRedirect().redirect_request(req, None, 302, '', {}, 'https://other.invalid/'))
        result = probe('scopus', '10.1000/a', keys, True,
                       lambda r: (403, b'test-secret-key INSUFFICIENT_PRIVILEGES', {}))
        self.assertEqual(result['status'], 'not_entitled')
        self.assertNotIn('test-secret', json.dumps(result))
        self.assertEqual(error_status(403, b'Forbidden'), 'forbidden_unknown')
        self.assertEqual(error_status(429, b'Forbidden'), 'rate_limited')
        self.assertEqual(error_status(401, b'Invalid key'), 'authentication_failed')

    def test_401_authorization_error_is_not_an_invalid_key_claim(self):
        payload = b'<service-error><status><statusCode>AUTHORIZATION_ERROR</statusCode></status></service-error>'
        result = probe('scopus', '10.1000/a', {KEY_NAMES[0]: 'private-value'}, True,
                       lambda r: (401, payload, {}))
        self.assertEqual(result['status'], 'not_entitled')
        self.assertEqual(result['provider_error_code'], 'AUTHORIZATION_ERROR')
        self.assertEqual(error_status(401, b'AUTHENTICATION_ERROR'), 'authentication_failed')
        self.assertEqual(error_status(403, b'AUTHENTICATION_ERROR'), 'authentication_failed')

    def test_direct_transport_only_changes_its_own_proxy_handler(self):
        response=Mock(status=200)
        response.read.return_value=b'{}'
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        with patch('publisher_probe.build_opener') as factory:
            factory.return_value.open.return_value=response
            req=request_for('europepmc','10.1000/a',{})
            self.assertEqual(transport(req,direct=True)[0],200)
            proxy=next(h for h in factory.call_args.args if isinstance(h,ProxyHandler))
            self.assertEqual(proxy.proxies,{})
            transport(req)
            self.assertFalse(any(isinstance(h,ProxyHandler) for h in factory.call_args.args))


if __name__ == '__main__':
    unittest.main()
