"""Offline endpoint compatibility checks; no imports of the optional TTS engine."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit


def load_helper():
    tree = ast.parse(Path(__file__).with_name('edge_tts_voice.py').read_text(encoding='utf-8-sig'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_apply_edge_tts_endpoint_compat')
    namespace = dict(parse_qs=parse_qs, urlencode=urlencode, urlsplit=urlsplit)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<endpoint compatibility>', 'exec'), namespace)
    return namespace[function.name]


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.apply = load_helper()
        self.token = 'a'*32
        self.old = ('wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1'
                    '?TrustedClientToken=' + self.token)
        self.engine = NS(communicate=NS(WSS_URL=self.old, SEC_MS_GEC_VERSION='1-existing',
                                       WSS_HEADERS={'User-Agent': 'existing'}),
                         constants=NS(WSS_URL=self.old, VOICE_LIST='unchanged voice list'))

    def test_actual_request_namespace_and_constants_are_corrected_without_changing_authentication(self):
        self.assertTrue(self.apply(self.engine))
        parsed = urlsplit(self.engine.communicate.WSS_URL)
        self.assertEqual(parsed.netloc, 'api.msedgeservices.com')
        self.assertEqual(parsed.path, '/tts/cognitiveservices/websocket/v1')
        self.assertEqual(parse_qs(parsed.query), {'Ocp-Apim-Subscription-Key': [self.token]})
        self.assertEqual(self.engine.constants.WSS_URL, self.engine.communicate.WSS_URL)
        self.assertEqual(self.engine.communicate.SEC_MS_GEC_VERSION, '1-existing')
        self.assertEqual(self.engine.communicate.WSS_HEADERS, {'User-Agent': 'existing'})
        self.assertEqual(self.engine.constants.VOICE_LIST, 'unchanged voice list')

    def test_repeated_configuration_is_idempotent(self):
        self.apply(self.engine)
        corrected = self.engine.communicate.WSS_URL
        self.assertFalse(self.apply(self.engine))
        self.assertEqual(corrected, self.engine.communicate.WSS_URL)

    def test_future_or_custom_endpoints_are_not_overridden(self):
        for endpoint in ('wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v2',
                         'wss://example.invalid/custom', self.old.replace('wss:', 'ws:'),
                         self.old.replace('speech.platform.bing.com', 'speech.platform.bing.com.example.invalid')):
            with self.subTest(endpoint=endpoint):
                self.engine.communicate.WSS_URL = endpoint
                self.assertFalse(self.apply(self.engine))
                self.assertEqual(self.engine.communicate.WSS_URL, endpoint)

    def test_missing_or_malformed_tokens_do_not_rewrite_endpoints(self):
        for query in ('', '?TrustedClientToken=bad', '?TrustedClientToken=' + 'z'*32,
                      '?TrustedClientToken=' + self.token + '&TrustedClientToken=' + self.token):
            endpoint = self.old.split('?')[0] + query
            self.engine.communicate.WSS_URL = endpoint
            self.assertFalse(self.apply(self.engine))
            self.assertEqual(self.engine.communicate.WSS_URL, endpoint)

    def test_optional_old_engine_without_module_namespace_remains_usable(self):
        self.assertFalse(self.apply(NS()))
        del self.engine.constants
        self.assertTrue(self.apply(self.engine))

    def test_both_load_paths_and_reused_engines_apply_the_fix(self):
        tree = ast.parse(Path(__file__).with_name('edge_tts_voice.py').read_text(encoding='utf-8-sig'))
        configure = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == '_configure_edge_tts_module')
        calls = [node for node in ast.walk(configure) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == '_apply_edge_tts_endpoint_compat']
        self.assertEqual(len(calls), 3)  # Installed module, source fallback, cached module.


if __name__ == '__main__':
    unittest.main()
