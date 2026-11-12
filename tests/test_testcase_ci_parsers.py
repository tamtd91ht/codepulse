"""CI report parsers (docs/specs/30a G1): JUnit / TestNG / xUnit XML, Playwright and Cucumber
JSON, C<id> keys, refused XML (DOCTYPE, entities), flaky rules."""

import json
from typing import Any

import pytest

from app.testcases import ci

JUNIT = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- generated -->
<testsuites>
  <testsuite name="auth" tests="4">
    <testcase classname="tests.test_login" name="test_C12_login_ok" time="1.25"/>
    <testcase classname="tests.test_login" name="[C13] wrong password" time="0.5">
      <failure message="AssertionError: 401 != 200" type="AssertionError">Traceback:
  File "x.py", line 3</failure>
      <system-out>[[ATTACHMENT|shots/shot.png]]</system-out>
    </testcase>
    <testcase classname="tests.test_login" name="test_logout">
      <properties><property name="test_id" value="C14"/></properties>
      <error message="boom"/>
    </testcase>
    <testcase classname="tests.test_login" name="test_skip"><skipped message="later"/></testcase>
  </testsuite>
</testsuites>"""


def test_junit() -> None:
    fmt, tests = ci.parse(JUNIT)
    assert fmt == "junit" and len(tests) == 4
    ok, bad, err, skip = tests
    assert ok.status == "passed" and ok.keys == [12] and ok.duration_s == 1.25
    assert ok.automation_id == "tests.test_login.test_C12_login_ok"
    assert bad.status == "failed" and bad.keys == [13]
    assert bad.message == "AssertionError: 401 != 200" and "line 3" in (bad.stack or "")
    assert bad.attachments == [{"name": "shot.png", "path": "shots/shot.png"}]
    assert err.status == "failed" and err.keys == [14] and err.message == "boom"
    assert skip.skipped and skip.message == "later"


def test_case_keys() -> None:
    assert ci.case_keys("@C1 [C2] C3: x", "C3", "XC4 C5x c6 C7_y") == [1, 2, 3, 7]


def test_testng_native() -> None:
    xml = b"""<testng-results><suite name="s"><test name="t">
      <class name="com.acme.LoginTest">
        <test-method status="PASS" name="setUp" is-config="true" duration-ms="5"/>
        <test-method status="PASS" name="login" description="C21" duration-ms="1500"/>
        <test-method status="FAIL" name="logout" duration-ms="20">
          <exception class="java.lang.AssertionError">
            <message><![CDATA[expected true]]></message>
            <full-stacktrace><![CDATA[java.lang.AssertionError at Foo]]></full-stacktrace>
          </exception>
        </test-method>
        <test-method status="SKIP" name="later" duration-ms="0"/>
      </class></test></suite></testng-results>"""
    fmt, tests = ci.parse(xml)
    assert fmt == "testng" and [t.name for t in tests] == ["login", "logout", "later"]
    assert tests[0].keys == [21] and tests[0].duration_s == 1.5 and tests[0].status == "passed"
    assert tests[1].status == "failed" and tests[1].message == "expected true"
    assert tests[1].stack == "java.lang.AssertionError at Foo"
    assert tests[1].automation_id == "com.acme.LoginTest.logout"
    assert tests[2].skipped


def test_xunit_net() -> None:
    xml = b"""<assemblies><assembly name="a.dll"><collection>
      <test name="Acme.Tests.Login.Works" type="Acme.Tests.Login" method="Works" time="0.2"
            result="Pass"><traits><trait name="Case" value="C31"/></traits></test>
      <test name="Acme.Tests.Login.Fails" type="Acme.Tests.Login" method="Fails" result="Fail">
        <failure><message>nope</message><stack-trace>at X</stack-trace></failure></test>
    </collection></assembly></assemblies>"""
    fmt, tests = ci.parse(xml)
    assert fmt == "xunit"
    assert tests[0].keys == [31] and tests[0].automation_id == "Acme.Tests.Login.Works"
    assert tests[1].status == "failed" and tests[1].message == "nope" and tests[1].stack == "at X"


def test_playwright() -> None:
    report = {
        "config": {},
        "suites": [
            {
                "title": "login.spec.ts",
                "file": "login.spec.ts",
                "specs": [],
                "suites": [
                    {
                        "title": "Login",
                        "file": "login.spec.ts",
                        "specs": [
                            {
                                "title": "@C41 works",
                                "tags": [],
                                "tests": [
                                    {
                                        "projectName": "chromium",
                                        "status": "flaky",
                                        "annotations": [],
                                        "results": [
                                            {"status": "failed", "duration": 100},
                                            {
                                                "status": "passed",
                                                "duration": 2500,
                                                "attachments": [
                                                    {
                                                        "name": "trace",
                                                        "contentType": "application/zip",
                                                        "path": "/r/trace.zip",
                                                    }
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            },
                            {
                                "title": "fails",
                                "tags": ["@C42"],
                                "tests": [
                                    {
                                        "status": "unexpected",
                                        "results": [
                                            {
                                                "status": "timedOut",
                                                "duration": 30000,
                                                "error": {"message": "Timeout", "stack": "at y"},
                                            }
                                        ],
                                    }
                                ],
                            },
                            {"title": "skip", "tests": [{"status": "skipped", "results": []}]},
                        ],
                    }
                ],
            }
        ],
    }
    fmt, tests = ci.parse(json.dumps(report).encode())
    assert fmt == "playwright" and len(tests) == 3
    works, fails, skip = tests
    assert works.keys == [41] and works.status == "passed" and works.duration_s == 2.5
    assert works.attempts == ["failed", "passed"] and works.attachments[0]["path"] == "/r/trace.zip"
    assert works.classname == "login.spec.ts > Login" and works.file == "login.spec.ts"
    assert fails.keys == [42] and fails.status == "failed" and fails.message == "Timeout"
    assert skip.skipped
    assert works.project == "chromium"
    assert works.identity == "login.spec.ts > Login.@C41 works [chromium]"
    assert fails.identity == fails.automation_id  # no project: the automation id alone


def test_cucumber() -> None:
    report = [
        {
            "uri": "features/login.feature",
            "name": "Login",
            "tags": [],
            "elements": [
                {"type": "background", "steps": [{"result": {"status": "passed"}}]},
                {
                    "type": "scenario",
                    "name": "Ok",
                    "tags": [{"name": "@C51"}],
                    "steps": [{"result": {"status": "passed", "duration": 2_000_000_000}}],
                },
                {
                    "type": "scenario",
                    "name": "Bad",
                    "tags": [{"name": "@C52"}],
                    "steps": [
                        {
                            "keyword": "Then ",
                            "name": "I see home",
                            "result": {"status": "failed", "error_message": "boom\n at z"},
                        }
                    ],
                },
                {
                    "type": "scenario",
                    "name": "Todo",
                    "steps": [{"result": {"status": "undefined"}}],
                },
            ],
        }
    ]
    fmt, tests = ci.parse(json.dumps(report).encode())
    assert fmt == "cucumber" and [t.name for t in tests] == ["Ok", "Bad", "Todo"]
    assert tests[0].status == "passed" and tests[0].keys == [51] and tests[0].duration_s == 2.0
    assert tests[1].status == "failed" and tests[1].message == "Then I see home: boom"
    assert tests[1].stack == "at z" and tests[1].automation_id == "Login.Bad"
    assert tests[2].skipped


@pytest.mark.parametrize(
    "data",
    [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 '
        b'"&lol;&lol;&lol;&lol;&lol;&lol;">]><testsuites><testcase name="&lol2;"/></testsuites>',
        b'<!DOCTYPE x SYSTEM "file:///etc/passwd"><testsuites/>',
        b"<!-- c --><!DOCTYPE x><testsuites/>",
        "<testsuites/>".encode("utf-16"),
        b"<testsuites><testcase name='&xxe;'/></testsuites>",
        b"<html><body/></html>",
        b"{}",
        b"not a report",
        b"",
    ],
)
def test_refused(data: bytes) -> None:
    with pytest.raises(ci.ReportError):
        ci.parse(data)


def test_refused_even_with_explicit_format() -> None:
    with pytest.raises(ci.ReportError, match="DOCTYPE"):
        ci.parse(b"<!DOCTYPE x><testsuites/>", "junit")


def test_is_flaky() -> None:
    same = [("passed", {"commit_sha": "a"}), ("failed", {"commit_sha": "a"})]
    assert ci.is_flaky(same)
    retried = {"commit_sha": "a", "attempts": ["failed", "passed"]}
    assert ci.is_flaky([("passed", retried)])
    # Without a commit nothing says the code was the same.
    assert not ci.is_flaky([("passed", {"attempts": ["failed", "passed"]})])
    flips: list[tuple[str, dict[str, Any]]] = [
        ("passed", {"commit_sha": "1"}),
        ("failed", {"commit_sha": "2"}),
        ("passed", {"commit_sha": "3"}),
        ("failed", {"commit_sha": "4"}),
    ]
    assert ci.is_flaky(flips)
    assert not ci.is_flaky(flips[:3])
    # Flips only count results with a commit.
    no_commit: list[tuple[str, dict[str, Any]]] = [(s, {}) for s, _ci in flips]
    assert not ci.is_flaky(no_commit)
    steady = [("failed", {"commit_sha": "a"}), ("passed", {"commit_sha": "b"})]
    assert not ci.is_flaky(steady)


def test_is_flaky_per_identity() -> None:
    browsers = {
        "commit_sha": "a",
        "attempts": ["passed", "failed"],
        "identities": {"t [chromium]": ["passed"], "t [webkit]": ["failed"]},
    }
    assert not ci.is_flaky([("failed", browsers)])
    retry = {"commit_sha": "a", "identities": {"t [chromium]": ["failed", "passed"]}}
    assert ci.is_flaky([("passed", retry)])
    # The same identity across two results of one commit.
    first = {"commit_sha": "a", "identities": {"t": ["failed"]}}
    second = {"commit_sha": "a", "identities": {"t": ["passed"]}}
    assert ci.is_flaky([("failed", first), ("passed", second)])
    other = {"commit_sha": "a", "identities": {"u": ["passed"]}}
    assert not ci.is_flaky([("failed", first), ("passed", other)])


def test_merged_keeps_attempts_per_identity() -> None:
    merged = ci.Merged()
    merged.add(ci.CiTest(name="t", classname="a", status="passed", project="chromium"))
    merged.add(ci.CiTest(name="t", classname="a", status="failed", project="webkit"))
    assert merged.status == "failed"
    assert merged.identities == {"a.t [chromium]": ["passed"], "a.t [webkit]": ["failed"]}


def test_too_many_tests() -> None:
    body = b"".join(b'<testcase name="t%d"/>' % i for i in range(ci.MAX_TESTS + 1))
    with pytest.raises(ci.ReportError, match="tối đa"):
        ci.parse(b"<testsuites>" + body + b"</testsuites>")
