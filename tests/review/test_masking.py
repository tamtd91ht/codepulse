"""app/review/masking.py: no value of the repository's current files reaches the model."""

import pytest

from app.review.masking import is_config, looks_secret, mask_code, mask_config

SENSITIVE = ["**/secrets/**", ".env*", "*.pem", "*.key"]
# Fake keys built at run time: written whole, GitHub's push protection takes them for real ones.
STRIPE = "sk_" + "live_" + "51HabcdEFGhijKLmnopQRstu"
GOOGLE = "AI" + "zaSyA1234567890abcdefghijklmnopqrs"
SLACK = "https://hooks." + "slack.com/services/" + "T000/B000/XXXX"


@pytest.mark.parametrize(
    ("path", "config"),
    [
        (".env", True),
        ("config/.env-prod", True),
        ("deploy/app.env", True),
        ("src/main/resources/application-prod.yml", True),
        ("src/main/resources/application-prod.yml.example", True),
        ("deploy/values.yaml.tpl", True),
        ("conf/db.properties", True),
        ("infra/prod.tfvars", True),
        ("k8s/secret.yaml", True),
        ("certs/server.pem", True),
        ("Web.config", True),
        ("Dockerfile", True),
        ("docker/Dockerfile.prod", True),
        ("scripts/deploy.sh", True),
        (".npmrc", True),
        ("home/.netrc", True),
        ("app/settings.py", False),
        ("src/Crypto.java", False),
    ],
)
def test_configuration_files_are_recognised(path: str, config: bool) -> None:
    assert is_config(path, SENSITIVE) is config


CONFIG_LEAKS = [
    ("# DB_PASSWORD=OldProdPass123", "OldProdPass123"),
    ("AES_KEY=q8ZrT1vYw3LmN5bK", "q8ZrT1vYw3LmN5bK"),
    ("DB_PASSWORD=${DB_PASSWORD:-Pr0dP@ssw0rd}", "Pr0dP"),
    ("SPRING_PASS=${DB_PASS:hunter2secret}", "hunter2"),
    ("HTPASS=pa$sword123", "sword123"),
    ("API=$apr1$xyz$abcdefghijklmnop", "apr1"),
    ("spring.datasource.password=hunter22", "hunter22"),
    ("password: Pr0dPass!", "Pr0dPass"),
    ("  token: '{{ vault:secret/data/app#token }}'", "vault:"),
    ('<add key="ApiKey" value="abc123xyz"/>', "abc123xyz"),
    ("ENV POSTGRES_PASSWORD supersecret", "supersecret"),
    ("export DB_PASSWORD=changemeplease", "changemeplease"),
    (f"tokens:\n  {STRIPE}: billing", STRIPE[:10]),
    ("AKIAIOSFODNN7EXAMPLE: aws", "AKIAIOSFODNN7"),
    ("glpat-abcdefghij0123456789: ci", "glpat-"),
    ('cert: "MIIBabc\n  QW5vdGhlcg=="', "QW5vdGhlcg"),
    ("key: |2\n  c2VjcmV0LXZhbHVlLWhlcmU=\nnext: ok", "c2VjcmV0"),
    ("key: | # comment\n  hunter2-value\nnext: ok", "hunter2"),
    (
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nAbCdEf0123=\n-----END PRIVATE KEY-----",
        "MIIEvQ",
    ),
    ("QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTA=", "QWxhZGRp"),
    ("DB_URL=postgres://app:pa/ss@db:5432/x", "pa/ss"),
    ('{"password": "J5on$ecret", "user": "app"}', "J5on"),
    ("[client]\nuser=app\npassword=1n1Secret", "1n1Secret"),
]


@pytest.mark.parametrize(("text", "secret"), CONFIG_LEAKS)
def test_config_values_never_show_any_character(text: str, secret: str) -> None:
    assert secret not in mask_config(text)


def test_config_labels_say_where_values_come_from() -> None:
    shown = mask_config(
        "\n".join(
            [
                "AES_KEY=q8ZrT1vYw3LmN5bK",
                "DB_URL=${DATABASE_URL}",
                "DB_PASSWORD=${DB_PASSWORD:-Pr0dP@ssw0rd}",
                "EMPTY=",
                "private_key: |",
                "  MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
                "next: ok",
            ]
        )
        + "\r\n# trailing comment\r\n"
    ).splitlines()
    assert lines_of(shown) == [
        "AES_KEY= <ẩn: chuỗi cứng 16 ký tự>",
        "DB_URL= <ẩn: đọc từ biến môi trường>",
        "DB_PASSWORD= <ẩn: đọc từ biến môi trường, có giá trị mặc định cứng 12 ký tự>",
        "EMPTY= <ẩn: trống>",
        "private_key: <ẩn: giá trị nhiều dòng>",
        "next: <ẩn: chuỗi cứng 2 ký tự>",
        "<ẩn: chú thích>",
    ]


def lines_of(shown: list[str]) -> list[str]:
    return [line for line in shown if line]


CODE_LEAKS = [
    ('AES_KEY = "q8ZrT1vYw3LmN5bK"', "q8ZrT1vYw3LmN5bK"),
    ("db_password=hunter22", "hunter22"),
    ("DB_PASSWORD=changemeplease", "changemeplease"),
    ('conn = connect(host, password="Pr0dPass!")', "Pr0dPass"),
    ('SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-abc123")', "django-insecure"),
    ('jwt = process.env.JWT_SECRET || "devsecret123"', "devsecret123"),
    ('@Value("${jwt.secret:mysecretkey123}")', "mysecretkey123"),
    ('pw = config.get("password", "fallbackPw9")', "fallbackPw9"),
    ('DB_PASSWORD = "P@ss\'w0rd!"', "w0rd"),
    ('password = "a\\"bSecretTail"', "SecretTail"),
    ('SECRET_KEY = b"sup3rs3cr3t"', "sup3rs3cr3t"),
    ('password = "abc" + "defSecond"', "defSecond"),
    ('PASSWORD = (\n    "multiLineSecret1"\n)', "multiLineSecret1"),
    (f'STRIPE_KEY = "{STRIPE}"', STRIPE[:7]),
    ('ENCRYPTION_KEY = "Zx9-strong-key"', "Zx9-strong"),
    (f'MAPS = "{GOOGLE}"', GOOGLE[:7]),
    (f'HOOK = "{SLACK}"', "T000/B000"),
    ('requests.get(url, auth=("admin", "S3cr3t!"))', "S3cr3t"),
    ('headers = {"Authorization": "Basic dXNlcjpwYXNz"}', "dXNlcjpwYXNz"),
    ("curl -u admin:S3cretPw https://x", "S3cretPw"),
    ("mysql -uroot -pS3cretDb app", "S3cretDb"),
    ("cli --password S3cretCli", "S3cretCli"),
    ('DB_URL = "postgres://app:p@ss@db/x"', "p@ss"),
    ("const k = 'sk-abcdefghijklmnopqrstuvwxyz012345'", "sk-abcdef"),
    ('"""\nPASSWORD=docstringSecret9\n"""', "docstringSecret9"),
]


@pytest.mark.parametrize(("text", "secret"), CODE_LEAKS)
def test_code_never_shows_a_secret(text: str, secret: str) -> None:
    assert secret not in mask_code(text)


def test_code_keeps_what_proves_where_a_value_comes_from() -> None:
    shown = mask_code(
        "\n".join(
            [
                "password = cfg.db.password",
                "key = os.environ['AES_KEY']",
                "def load():",
                "    return decrypt(blob)",
            ]
        )
    )
    assert "password = cfg.db.password" in shown
    assert "key = os.environ[<chuỗi 7 ký tự>]" in shown
    assert "    return decrypt(blob)" in shown


@pytest.mark.parametrize(
    "argument",
    [
        "Prod dùng key khác: AES_KEY=q8ZrT1vYw3LmN5bK, dev thì không quan trọng lắm đâu ạ.",
        "Key prod là password: 'Pr0dPass!' nhưng nó nằm trong vault rồi, không lộ ra đâu.",
        "-----BEGIN PRIVATE KEY----- dán thử",
        "mật khẩu prod là Abc@12345 nhưng chỉ dùng nội bộ thôi ạ, không ai biết đâu ạ.",
        "password Abc@12345 chỉ dùng cho môi trường dev local, prod thì khác hoàn toàn.",
        "pw: Abc@12345 là của máy dev, không liên quan tới môi trường production đâu ạ.",
        "Chuỗi wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY là key test thôi, không phải key thật.",
        f"Dev dùng {STRIPE} để thử, prod dùng biến môi trường riêng.",
        "Kết nối dev là postgres://app:pa/ss@db/x, prod đọc DATABASE_URL từ secret của k8s.",
    ],
)
def test_an_argument_that_pastes_a_secret_is_recognised(argument: str) -> None:
    assert looks_secret(argument)


@pytest.mark.parametrize(
    "argument",
    [
        "Key trong .env-dev chỉ dùng cho profile dev để debug; .env-prod đọc AES_KEY từ "
        "biến môi trường k8s.",
        'spring.datasource.password: "${DB_PASSWORD}" trong application-prod.yml, giá trị lấy '
        "từ secret của k8s.",
        "Token được đọc từ vault khi khởi động, file config/application-prod.yml không có giá "
        "trị nào hardcode cả.",
        "Mật khẩu prod nằm trong secret k8s tên db-credentials, deploy/k8s/deployment.yaml "
        "mount nó vào biến DB_PASSWORD.\r\nDòng thứ hai của phản biện.",
        "Đây là một đoạn phản biện dài. " * 30,
        "Hàm bypass_cache không liên quan tới bảo mật, compass chỉ là tên module điều hướng ạ.",
    ],
)
def test_a_normal_argument_is_not_refused(argument: str) -> None:
    assert not looks_secret(argument)
