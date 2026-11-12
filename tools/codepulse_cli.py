#!/usr/bin/env python3
"""CodePulse CLI (docs/specs/30 §15.4, G1): upload a CI test report as a test run's results.

Standard library only: copy this one file into a pipeline and run it with python3.

    python3 tools/codepulse_cli.py results upload \\
        --url https://codepulse.example.com --token "$CODEPULSE_TOKEN" \\
        --project 8 --suite 3 --file reports/junit.xml \\
        --mr "$CI_MERGE_REQUEST_IID" --commit "$CI_COMMIT_SHA" --branch "$CI_COMMIT_REF_NAME" \\
        --repo "$CI_PROJECT_PATH" --close --wait

Formats: JUnit XML (pytest, Surefire, Jest/Mocha reporters, TestNG's JUnit report), TestNG
testng-results.xml, xUnit.net v2 XML, Playwright JSON reporter, Cucumber JSON (up to 20 MB).
A test maps to a case by a C<id> in its name or tags (@C12, [C12], C12:), else by the case's
Automation ID ("classname.name"); --auto-create makes the missing cases (needs --suite).

GitLab CI example (.gitlab-ci.yml):

    test:
      script:
        - pytest --junitxml=reports/junit.xml || true
        - python3 tools/codepulse_cli.py results upload --url "$CODEPULSE_URL"
            --token "$CODEPULSE_TOKEN" --project 8 --suite 3 --file reports/junit.xml
            --mr "$CI_MERGE_REQUEST_IID" --commit "$CI_COMMIT_SHA"
            --branch "$CI_COMMIT_REF_NAME" --repo "$CI_PROJECT_PATH" --wait
      artifacts:
        when: always
        reports:
          junit: reports/junit.xml

The token is a personal access token (cpat_...) with the `results:write` scope (enough for
--wait too); creating a run needs the "manage test runs" permission of its owner (or pass
--run-id of an open run). --close needs that permission and the `testcases:write` scope. One
upload at a time per run: a second one into a run still importing is refused (409).
Exit code: 0 done, 1 failed (upload refused, import failed, or timed out while waiting).
"""

import argparse
import contextlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

API = "/api/v1"
TIMEOUT_S = 60


class CliError(Exception):
    pass


def _request(
    url: str, token: str, *, body: bytes | None = None, content_type: str | None = None
) -> Any:
    method = "POST" if body is not None else "GET"
    req = urllib.request.Request(url, data=body, method=method)  # noqa: S310 (checked scheme)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "codepulse-cli/1")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (http/https)
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        with contextlib.suppress(ValueError, AttributeError):
            detail = json.loads(detail).get("detail", detail)
        raise CliError(f"HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise CliError(f"không kết nối được {url}: {exc.reason}") from None


def multipart(fields: dict[str, str], file_field: str, path: str) -> tuple[bytes, str]:
    """A multipart/form-data body with the fields and one file."""
    boundary = f"----codepulse{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode("utf-8") + b"\r\n"
    filename = os.path.basename(path).replace('"', "")
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        data = fh.read()
    out += f"--{boundary}\r\n".encode()
    out += (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def upload(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise CliError("--url phải bắt đầu bằng https:// hoặc http://")
    token = args.token or os.environ.get("CODEPULSE_TOKEN") or ""
    if not token:
        raise CliError("thiếu --token (hoặc biến môi trường CODEPULSE_TOKEN)")
    fields = {"business_project_id": str(args.project)}
    optional = {
        "suite_id": args.suite,
        "run_id": args.run_id,
        "run_name": args.run_name,
        "milestone_id": args.milestone,
        "config_ids": args.configs,
        "mr_iid": args.mr if (args.mr or "").strip().isdigit() else None,
        "commit_sha": args.commit,
        "branch": args.branch,
        "project": args.repo,
    }
    fields.update({k: str(v) for k, v in optional.items() if v not in (None, "")})
    if args.close:
        fields["close_run"] = "true"
    if args.auto_create:
        fields["auto_create"] = "true"
    body, ctype = multipart(fields, "file", args.file)
    started = _request(
        f"{base}{API}/testcases/runs/import-results", token, body=body, content_type=ctype
    )
    import_id = started["import_id"]
    print(f"Đã gửi báo cáo: lượt nhập #{import_id} ({started['status']})")
    if not args.wait:
        return 0
    deadline = time.monotonic() + args.timeout
    while True:
        got = _request(f"{base}{API}/testcases/ci-imports/{import_id}", token)
        status = got.get("status")
        if status in ("done", "failed"):
            break
        if time.monotonic() > deadline:
            raise CliError(
                f"hết thời gian chờ lượt nhập #{import_id} ({got.get('done')}/{got.get('total')})"
            )
        time.sleep(2)
    if status == "failed":
        raise CliError(f"lượt nhập #{import_id} lỗi: {got.get('error')}")
    print(
        f"Xong: {got['total']} test, {got['mapped']} đã map, {got['created_cases']} case tạo mới, "
        f"{got['skipped']} bỏ qua, {len(got['unmapped'])} chưa map; run {got.get('run_key')}"
    )
    for name in got["unmapped"][:20]:
        print(f"  chưa map: {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Vietnamese text on a non-UTF-8 console
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        prog="codepulse_cli.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="group", required=True)
    results = sub.add_parser("results", help="kết quả test từ CI").add_subparsers(
        dest="action", required=True
    )
    up = results.add_parser(
        "upload",
        help="gửi báo cáo test (JUnit / TestNG / xUnit / Playwright / Cucumber)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    up.add_argument(
        "--url", required=True, help="địa chỉ CodePulse, ví dụ https://codepulse.example.com"
    )
    up.add_argument("--token", help="token cpat_... (mặc định: $CODEPULSE_TOKEN)")
    up.add_argument("--project", required=True, type=int, help="business_project_id của dự án")
    up.add_argument("--suite", type=int, help="bộ testcase (bắt buộc khi --auto-create)")
    up.add_argument("--file", required=True, help="file báo cáo")
    up.add_argument("--run-id", type=int, help="ghi vào run đang mở này thay vì tạo run mới")
    up.add_argument("--run-name", help="tên run mới (mặc định: CI <branch> <sha>)")
    up.add_argument("--milestone", type=int, help="milestone của run mới")
    up.add_argument("--configs", help="id cấu hình, cách nhau bởi dấu phẩy")
    up.add_argument("--mr", help="IID của merge request ($CI_MERGE_REQUEST_IID; trống: bỏ qua)")
    up.add_argument("--commit", help="commit SHA ($CI_COMMIT_SHA)")
    up.add_argument("--branch", help="nhánh ($CI_COMMIT_REF_NAME)")
    up.add_argument("--repo", help="đường dẫn repo GitLab ($CI_PROJECT_PATH)")
    up.add_argument("--close", action="store_true", help="đóng run sau khi nhập")
    up.add_argument("--auto-create", action="store_true", help="tự tạo case cho test chưa map")
    up.add_argument("--wait", action="store_true", help="chờ xử lý xong và in tổng kết")
    up.add_argument("--timeout", type=int, default=600, help="số giây chờ tối đa với --wait")
    args = parser.parse_args(argv)
    try:
        return upload(args)
    except (CliError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
