import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from storekeeper.demo import DemoReplyModel
from storekeeper.live_model import create_live_model
from storekeeper.live_preview import create_live_preview


class LiveModelConfigurationTests(unittest.TestCase):
    def test_missing_key_fails_instead_of_falling_back_to_mock(self):
        with patch.dict(os.environ, {}, clear=True), patch('storekeeper.live_model.load_dotenv'):
            with self.assertRaises(RuntimeError):
                create_live_model()

    def test_configuration_uses_official_endpoint_and_no_automatic_retries(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key-not-real',
                                     'DEEPSEEK_MODEL': 'deepseek-flash'}, clear=True), \
                patch('storekeeper.live_model.load_dotenv'):
            model = create_live_model()
        self.assertEqual(str(model.openai_api_base), 'https://api.deepseek.com')
        self.assertEqual(model.max_retries, 0)

    def test_preview_explicitly_uses_fixtures_without_live_shopify(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_live_preview(Path(directory), model=DemoReplyModel())
            with TestClient(app) as client:
                response = client.get('/')
                self.assertIn('真实 DeepSeek', response.text)
                self.assertIn('测试订单', response.text)
                self.assertEqual(app.state.fixture_shopify_client.write_calls, [])


if __name__ == '__main__':
    unittest.main()
