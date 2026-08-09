import tempfile
import unittest
from pathlib import Path

from welearn_studio.services.account_import import parse_account_file, parse_accounts


class AccountImportTests(unittest.TestCase):
    def test_csv_header_order_quotes_bom_and_bad_rows(self) -> None:
        text = (
            "\ufeffnickname,password,username\r\n"
            '"Alpha, One",secret-one,alpha@example.test\r\n'
            "Missing Password,,broken@example.test\r\n"
        )
        result = parse_accounts(text, format_hint="csv")

        self.assertEqual(len(result.accounts), 1)
        self.assertEqual(result.accounts[0].identity.username, "alpha@example.test")
        self.assertEqual(result.accounts[0].identity.nickname, "Alpha, One")
        self.assertEqual(result.accounts[0].password, "secret-one")
        self.assertNotIn("secret-one", repr(result.accounts[0]))
        self.assertEqual(result.issues[0].line_number, 3)

    def test_headerless_semicolon_csv_and_casefolded_duplicates(self) -> None:
        result = parse_accounts(
            "first@example.test;one;First\nFIRST@example.test;two;Duplicate\n",
            format_hint="csv",
        )

        self.assertEqual([value.identity.nickname for value in result.accounts], ["First"])
        self.assertEqual(len(result.issues), 1)
        self.assertIn("duplicate", result.issues[0].message)

    def test_password_like_data_value_does_not_turn_first_row_into_header(self) -> None:
        result = parse_accounts(
            "alice@example.test,password,Alpha\n",
            format_hint="csv",
        )

        self.assertEqual(len(result.accounts), 1)
        self.assertEqual(result.accounts[0].identity.username, "alice@example.test")

    def test_short_header_like_credentials_are_kept_as_data(self) -> None:
        result = parse_accounts("user,pass\n", format_hint="csv")

        self.assertEqual(len(result.accounts), 1)
        self.assertEqual(result.accounts[0].identity.username, "user")

    def test_txt_supports_comments_quotes_and_delimiters(self) -> None:
        result = parse_accounts(
            "# synthetic accounts\n"
            'one@example.test pw-one "One Person"\n'
            "two@example.test\tpw-two\tTwo\n"
            "three@example.test,pw-three,Three\n",
            format_hint="txt",
        )

        self.assertEqual(len(result.accounts), 3)
        self.assertEqual(result.accounts[0].identity.nickname, "One Person")
        self.assertFalse(result.issues)

    def test_header_missing_required_column_reports_issue(self) -> None:
        result = parse_accounts("username,nickname\na@example.test,Alpha\n", format_hint="csv")
        self.assertFalse(result.accounts)
        self.assertEqual(result.issues[0].line_number, 1)
        self.assertIn("requires username and password", result.issues[0].message)

    def test_utf16_account_file_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "accounts.csv")
            path.write_bytes("username,password\na@example.test,pw\n".encode("utf-16"))

            result = parse_account_file(path)

        self.assertEqual(result.accounts[0].identity.username, "a@example.test")


if __name__ == "__main__":
    unittest.main()
