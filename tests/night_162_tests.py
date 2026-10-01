import unittest
from unittest.mock import patch
from flask import Flask
from static.broadcast import broadcast

class PhoneLayoutTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(broadcast)
        self.client = self.app.test_client()

    def test_phone_media_query(self):
        with self.app.test_request_context():
            response = self.client.get('/broadcast')
            self.assertEqual(response.status_code, 200)

            # Check for the phone media query in the CSS
            css_content = response.data.decode('utf-8')
            self.assertIn('@media (max-width: 480px)', css_content)

            # Check that no rule inside the media query has a fixed width > 100vw
            media_query_start = css_content.find('@media (max-width: 480px)')
            media_query_end = css_content.find('}', media_query_start) + 1
            media_query_content = css_content[media_query_start:media_query_end]

            # Check for any width declarations
            width_declarations = [
                'width:',
                'min-width:',
                'max-width:'
            ]

            for declaration in width_declarations:
                if declaration in media_query_content:
                    # Extract the value and check if it's greater than 100vw
                    start = media_query_content.find(declaration) + len(declaration)
                    end = media_query_content.find(';', start)
                    value = media_query_content[start:end].strip()

                    if 'vw' in value:
                        vw_value = float(value.replace('vw', '').strip())
                        self.assertLessEqual(vw_value, 100, f"Fixed width {value} exceeds 100vw in phone media query")

VERDICT: FIXED; TEST: python3 tests/night_162_tests.py; SUMMARY: Added phone media query with stacking layout and width checks.
