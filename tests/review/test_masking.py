"""app/review/masking.py: no value of the repository's current files reaches the model."""

import pytest

from app.review.masking import is_config, looks_secret, mask_code, mask_config

SENSITIVE = ["**/secrets/**", ".env*", "*.pem", "*.key"]


@pytest.mark.parametrize(
    ("path", "config"),
    [
        (".env", True),
        ("config/.env-prod", True),
        ("deploy/app.env", True),
        ("src/main/resources/application-prod.yml", True),
        ("conf/db.properties", True),
        ("infra/prod.tfvars", True),
        ("k8s/secret.yaml", True),
        ("certs/server.pem", True),
        ("app/settings.py", False),
        ("src/Crypto.java", False),
    ],
)
def test_configuration_files_are_recognised(path: str, config: bool) -> None:
    assert is_config(path, SENSITIVE) is config


def test_config_values_never_show_any_character() -> None:
    text = "\n".join(
        [
            "# DB_PASSWORD=OldProdPass123",
            "AES_KEY=q8ZrT1vYw3LmN5bK",
            "DB_URL=${DATABASE_URL}",
            "DB_PASSWORD=${DB_PASSWORD:-Pr0dP@ssw0rd}",
            "SPRING_PASS=${DB_PASS:hunter2secret}",
            "HTPASS=pa$sword123",
            "API=$apr1$xyz$abcdefghijklmnop",
            "spring.datasource.password=hunter22",
            "password: Pr0dPass!",
            "  token: '{{ vault:secret/data/app#token }}'",
            "EMPTY=",
            "private_key: |",
            "  MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
            "  AbCdEfGhIjKlMnOpQrStUvWxYz0123456789ab=",
            "next: ok",
            "-----BEGIN PRIVATE KEY-----",
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789=",
            "-----END PRIVATE KEY-----",
            "QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTA=",
        ]
    )
    shown = mask_config(text)
    for secret in (
        "OldProdPass123",
        "q8ZrT1vYw3LmN5bK",
        "Pr0dP",
        "hunter2",
        "sword123",
        "apr1",
        "hunter22",
        "Pr0dPass",
        "MIIEvQ",
        "AbCdEf",
        "QWxhZGRp",
        "vault:",
    ):
        assert secret not in shown, secret
    lines = shown.splitlines()
    assert lines[0] == "<ẩn: chú thích>"
    assert lines[1] == "AES_KEY= <ẩn: chuỗi cứng 16 ký tự>"
    assert lines[2] == "DB_URL= <ẩn: đọc từ biến môi trường>"
    assert (
        lines[3] == "DB_PASSWORD= <ẩn: đọc từ biến môi trường, có giá trị mặc định cứng 12 ký tự>"
    )
    assert "SPRING_PASS= <ẩn: đọc từ biến môi trường, có giá trị mặc định cứng 13 ký tự>" in lines
    assert "HTPASS= <ẩn: chuỗi cứng 11 ký tự>" in lines
    assert "spring.datasource.password= <ẩn: chuỗi cứng 8 ký tự>" in lines
    assert "  token: <ẩn: lấy từ kho bí mật/template>" in lines
    assert "EMPTY= <ẩn: trống>" in lines
    assert "private_key: <ẩn: giá trị nhiều dòng>" in lines
    assert "next: <ẩn: chuỗi cứng 2 ký tự>" in lines
    assert lines.count("<ẩn: khối khoá>") == 1
    assert lines[-1] == "<ẩn: dòng>"  # a base64 run is not a key


def test_code_hides_literals_of_secret_names_but_keeps_references() -> None:
    text = "\n".join(
        [
            'AES_KEY = "q8ZrT1vYw3LmN5bK"',
            "password = cfg.db.password",
            "token = token_value",
            "db_password=hunter22",
            'conn = connect(host, password="Pr0dPass!")',
            "key = os.environ['AES_KEY']",
        ]
    )
    shown = mask_code(text)
    assert "q8ZrT1vYw3LmN5bK" not in shown and "hunter22" not in shown and "Pr0dPass" not in shown
    assert 'AES_KEY = "<ẩn: chuỗi cứng 16 ký tự>"' in shown
    assert "password = cfg.db.password" in shown
    assert "token = token_value" in shown
    assert "key = os.environ['AES_KEY']" in shown


@pytest.mark.parametrize(
    ("argument", "secret"),
    [
        ("Prod dùng key khác: AES_KEY=q8ZrT1vYw3LmN5bK, dev thì không quan trọng lắm đâu ạ.", True),
        ("Key prod là password: 'Pr0dPass!' nhưng nó nằm trong vault rồi, không lộ ra đâu.", True),
        ("-----BEGIN PRIVATE KEY----- dán thử", True),
        (
            "Key trong .env-dev chỉ dùng cho profile dev để debug; .env-prod đọc AES_KEY từ "
            "biến môi trường k8s.",
            False,
        ),
    ],
)
def test_an_argument_that_pastes_a_secret_is_recognised(argument: str, secret: bool) -> None:
    assert looks_secret(argument) is secret
