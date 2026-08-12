import unittest
from pathlib import Path

from tools.chat_extractor import extract_chat_messages
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord
from tools.secret_redactor import redact_normalized_records, redact_text


def record(text: str) -> NormalizedRecord:
    return NormalizedRecord(
        timestamp="2026-08-13T13:00:00+09:00",
        category="message",
        subtype="chat_message",
        source_record_type="response_item",
        source_payload_type="message",
        source_id="msg-1",
        turn_id="turn-1",
        role="user",
        phase=None,
        content=(NormalizedContentPart("text", text, "input_text"),),
        attributes={},
        source_path=Path("rollout-test.jsonl"),
        line_number=1,
        start_offset=0,
        end_offset=100,
    )


class SecretRedactorTests(unittest.TestCase):
    def test_redacts_assignments_headers_url_and_known_formats(self) -> None:
        source = (
            "api_key=secret-value\n"
            "PASSWORD='password-value'\n"
            "Authorization: Bearer bearer-value\n"
            "https://user:pass@example.test/path\n"
            "token sk-proj-abcdefghijklmnopqrst"
        )

        text, redactions = redact_text(source)

        self.assertNotIn("secret-value", text)
        self.assertNotIn("password-value", text)
        self.assertNotIn("bearer-value", text)
        self.assertNotIn("user:pass", text)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrst", text)
        self.assertIn("api_key=[REDACTED:API_KEY]", text)
        self.assertIn("PASSWORD='[REDACTED:PASSWORD]'", text)
        self.assertIn("Authorization: Bearer [REDACTED:TOKEN]", text)
        self.assertIn("https://[REDACTED:URL_CREDENTIAL]@example.test/path", text)
        self.assertEqual(5, len(redactions))
        self.assertEqual(
            2,
            sum(value.type == "api_key" for value in redactions),
        )

    def test_redacts_pem_private_key_as_one_secret(self) -> None:
        source = (
            "before\n-----BEGIN PRIVATE KEY-----\n"
            "sensitive-material\n-----END PRIVATE KEY-----\nafter"
        )

        text, redactions = redact_text(source)

        self.assertEqual("before\n[REDACTED:PRIVATE_KEY]\nafter", text)
        self.assertEqual("private_key", redactions[0].type)
        self.assertEqual("pem_private_key", redactions[0].detector)

    def test_redacts_complete_quoted_values_containing_spaces(self) -> None:
        source = 'PASSWORD="two word password"\n--token \'two word token\''

        text, redactions = redact_text(source)

        self.assertEqual(
            'PASSWORD="[REDACTED:PASSWORD]"\n--token \'[REDACTED:TOKEN]\'',
            text,
        )
        self.assertEqual(2, len(redactions))

    def test_only_sensitive_environment_variable_names_are_redacted(self) -> None:
        source = (
            "PATH=C:\\Tools\n"
            "LOCALAPPDATA=C:\\Users\\sample\\AppData\\Local\n"
            "export SERVICE_TOKEN=service-secret\n"
            "APP_SECRET=application-secret\n"
            "$env:DB_PASSWORD=database-secret"
        )

        text, redactions = redact_text(source)

        self.assertIn("PATH=C:\\Tools", text)
        self.assertIn("LOCALAPPDATA=C:\\Users\\sample\\AppData\\Local", text)
        self.assertIn("SERVICE_TOKEN=[REDACTED:TOKEN]", text)
        self.assertIn("APP_SECRET=[REDACTED:TOKEN]", text)
        self.assertIn("$env:DB_PASSWORD=[REDACTED:PASSWORD]", text)
        self.assertEqual(
            ["token", "token", "password"],
            [value.type for value in redactions],
        )

    def test_preserves_hash_uuid_email_phone_ip_and_ordinary_long_text(self) -> None:
        values = (
            "a" * 40,
            "b" * 64,
            "019fc727-eea9-7a70-976e-c7a5f7f23690",
            "person@example.test",
            "090-1234-5678",
            "192.0.2.121",
            "ordinarylongidentifierwithoutauthenticationcontext1234567890",
        )
        source = "\n".join(values)

        text, redactions = redact_text(source)

        self.assertEqual(source, text)
        self.assertEqual(tuple(), redactions)

    def test_existing_marker_is_not_double_redacted(self) -> None:
        source = "Authorization: Bearer [REDACTED:TOKEN]"

        text, redactions = redact_text(source)

        self.assertEqual(source, text)
        self.assertEqual(1, len(redactions))
        self.assertEqual("existing_marker", redactions[0].detector)

    def test_json_and_command_option_values_are_redacted(self) -> None:
        source = '{"client_secret":"json-secret"}\n--password command-secret'

        text, redactions = redact_text(source)

        self.assertEqual(
            '{"client_secret":"[REDACTED:TOKEN]"}\n'
            "--password [REDACTED:PASSWORD]",
            text,
        )
        self.assertEqual(2, len(redactions))

    def test_chat_extraction_receives_only_redacted_text_and_metadata(self) -> None:
        result = redact_normalized_records((record("token=raw-secret"),))

        chats = extract_chat_messages(result.records, "session-1")

        part = chats.messages[0].content[0]
        self.assertEqual("token=[REDACTED:TOKEN]", part.text)
        self.assertEqual("token", part.redactions[0].type)
        self.assertEqual("sensitive_assignment", part.redactions[0].detector)

    def test_records_are_replaced_in_memory_and_counts_contain_no_values(self) -> None:
        source = record("api_key=first\ntoken=second")

        result = redact_normalized_records((source,))

        self.assertTrue(result.is_masked)
        self.assertEqual("api_key=first\ntoken=second", source.content[0].text)
        self.assertNotIn("first", result.records[0].content[0].text)
        self.assertNotIn("second", result.records[0].content[0].text)
        self.assertEqual(
            {"api_key": 1, "token": 1},
            {value.type: value.count for value in result.counts},
        )
        self.assertFalse(hasattr(result.counts[0], "text"))


if __name__ == "__main__":
    unittest.main()
