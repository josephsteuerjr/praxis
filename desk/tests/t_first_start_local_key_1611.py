"""A local OpenAI-compatible server may be created without a key; public endpoints may not.

Observed on Windows 10 with llama.cpp: the first-start form demanded a key the local server
does not use, and a dummy value was the only workaround. An empty key on loopback or a private
network is stored as the existing ``local`` placeholder that the settings page already uses.
"""
import unittest

import t_first_start_3010 as fixture
from localharness import first_start as start


class LocalKeyOptional(unittest.TestCase):
    setUp = fixture.FirstStart.setUp
    prepare = fixture.FirstStart.prepare

    def create(self, **values):
        return start.create_agent(self.root, {'name': 'Local agent', 'owner': 'Owner',
                                              'provider': 'openai', 'model': 'gemma',
                                              **values}, places=self.places)

    def test_loopback_server_without_key_uses_the_local_placeholder(self):
        self.prepare()
        self.create(key='', base_url='http://127.0.0.1:8080/v1')
        model = start.load(self.root / 'helene.json')['model']
        self.assertEqual((model['key'], model['base_url'], model['model']),
                         ('local', 'http://127.0.0.1:8080/v1', 'gemma'))

    def test_explicit_key_for_a_local_server_is_kept_exactly(self):
        self.prepare()
        self.create(key='secret-local-key', base_url='http://localhost:8080/v1')
        self.assertEqual(start.load(self.root / 'helene.json')['model']['key'], 'secret-local-key')

    def test_public_or_unspecified_endpoints_still_require_a_key(self):
        self.prepare()
        for base in ('', 'https://api.openai.com/v1', 'http://8.8.8.8/v1',
                     'http://user:secret@127.0.0.1/v1', 'ftp://127.0.0.1/v1'):
            with self.subTest(base=base), self.assertRaises(ValueError) as caught:
                self.create(key='', base_url=base)
            self.assertEqual(caught.exception.native_caption['code'], 'first_api_key')
        self.assertFalse(start.agent_exists(self.places.data))

    def test_anthropic_keeps_requiring_a_key_even_locally(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.create(provider='anthropic', key='', base_url='http://127.0.0.1:8080')

    def test_address_classifier(self):
        local = ('http://127.0.0.1:8080/v1', 'http://localhost/v1', 'http://box.local:1234/v1',
                 'http://192.168.1.20:8080/v1', 'http://10.0.0.5/v1', 'http://[::1]:8080/v1',
                 'http://100.70.0.1/v1', 'https://127.0.0.1/v1')
        public = ('https://api.openai.com/v1', 'http://8.8.8.8/v1', 'http://fd.example.com/v1',
                  'http://user:pw@127.0.0.1/v1', 'ftp://127.0.0.1/', '', 'not a url')
        for value in local:
            self.assertTrue(start.keyless_local_endpoint(value), value)
        for value in public:
            self.assertFalse(start.keyless_local_endpoint(value), value)


if __name__ == '__main__':
    unittest.main()
